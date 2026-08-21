"""Type-aware matchers (§6.3).

Numbers are compared via *interpretation candidate sets*: `146,14` reads as
146.14 (comma-decimal) but not 14614 (invalid thousands grouping — groups
must all be 3 digits); `1,234` legitimately reads both ways and matches
either; `4,950407` only reads comma-decimal. The document side and the
LLM-value side both expand to sets; a hit is a non-empty intersection.

Dates expand to (y, m, d) tuples across format families; day/month order
ambiguity (03/04/2026) also produces candidate sets. IDs and text match on
canonical strings (accent-folded alphanumerics) exactly, then fuzzily.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional

from .canon import canon_value, canonical_map, find_all, fuzzy_windows
from .rows import Row

ID_FUZZY_RATIO = 0.88
TEXT_FUZZY_RATIO = 0.82
BLOCK_TOKEN_OVERLAP = 0.65


@dataclass
class RawMatch:
    """A hit inside one row, as a character range of row.text.

    `spans` (when set) lists the exact sub-ranges that matched — the bbox is
    the union of THOSE, never the full start..end stretch. Critical for block
    values on multi-column layouts where a row interleaves unrelated text.
    """
    row: Row
    start: int
    end: int
    score: float          # 1.0 exact; fuzzy = similarity ratio
    exact: bool
    matched_text: str
    spans: Optional[list[tuple[int, int]]] = None
    fused: bool = False   # span glued from separate printed runs

    def bbox_px(self) -> Optional[tuple[float, float, float, float]]:
        """Pixel bbox of the match — union of `spans` when set, else the
        contiguous start..end range. Every consumer must go through this:
        the raw stretch swallows interleaved columns on block matches."""
        if self.spans:
            boxes = [b for b in (self.row.char_range_bbox(s, e)
                                 for s, e in self.spans) if b is not None]
            if not boxes:
                return None
            return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                    max(b[2] for b in boxes), max(b[3] for b in boxes))
        return self.row.char_range_bbox(self.start, self.end)


# ---------------------------------------------------------------- numbers ---

_NUM_RUN = re.compile(r"\d[\d.,]*\d|\d")


def _glue_spaced_thousands(text: str, runs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Join `1 234,56`-style runs: a following group may be glued only when it
    is exactly 3 digits (optionally with a decimal tail) — IBAN groups of 4 and
    phone numbers stay separate."""
    if not runs:
        return runs
    glued: list[tuple[int, int]] = []
    cur_s, cur_e = runs[0]
    for s, e in runs[1:]:
        between = text[cur_e:s]
        head = text[cur_s:cur_e]
        tail = text[s:e]
        if (between == " " and re.fullmatch(r"\d{1,3}", re.split(r"[., ]", head)[-1])
                and re.fullmatch(r"\d{3}([.,]\d+)?", tail)
                # a run whose tail is already decimal ('2 044.43') is complete —
                # gluing the NEXT number onto it fuses two separate amounts
                and not re.search(r"[.,]\d{1,2}$", head)):
            cur_e = e
        else:
            glued.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    glued.append((cur_s, cur_e))
    return glued


def number_interpretations(run: str) -> set[Decimal]:
    """All plausible numeric readings of a separator-bearing digit run."""
    run = run.replace(" ", "")
    out: set[Decimal] = set()

    def try_add(s: str) -> None:
        try:
            out.add(Decimal(s))
        except InvalidOperation:
            pass

    if "," not in run and "." not in run:
        try_add(run)
        return out

    def groups_ok(parts: list[str]) -> bool:
        # thousands grouping: first group 1-3 digits, rest exactly 3
        return (len(parts) > 1 and 1 <= len(parts[0]) <= 3
                and all(len(p) == 3 for p in parts[1:]))

    # reading A: comma = decimal (dots/spaces = thousands)
    if run.count(",") == 1:
        left, right = run.split(",")
        left_parts = left.split(".")
        if len(left_parts) == 1 or groups_ok(left_parts):
            try_add(f"{''.join(left_parts)}.{right}")
    elif run.count(",") == 0 and "." in run:
        parts = run.split(".")
        if groups_ok(parts):  # dots as pure thousands: 1.234.567
            try_add("".join(parts))

    # reading B: dot = decimal (commas = thousands)
    if run.count(".") == 1:
        left, right = run.split(".")
        left_parts = left.split(",")
        if len(left_parts) == 1 or groups_ok(left_parts):
            try_add(f"{''.join(left_parts)}.{right}")
    elif run.count(".") == 0 and "," in run:
        parts = run.split(",")
        if groups_ok(parts):  # commas as pure thousands: 1,234,567
            try_add("".join(parts))

    return out


def value_number_set(value) -> set[Decimal]:
    """Interpretation set of the LLM-provided value."""
    if isinstance(value, bool):
        return set()
    if isinstance(value, (int, float)):
        return {Decimal(str(value))}
    if isinstance(value, str):
        s = value.strip()
        s = re.sub(r"[€$£]|(?i:\b(eur|usd|gbp|czk|pln)\b)", "", s).strip()
        neg = s.startswith("-") or (s.startswith("(") and s.endswith(")"))
        s = s.strip("()- ").replace("\u00a0", " ").replace(" ", "")
        interp = number_interpretations(s)
        if not interp:
            # unit-suffixed cells ("50kg", "16ks", "1900,00K\u010d", "100,-"): a
            # single numeric core with short non-digit decoration around it
            # reads as that number; two numeric runs stay ambiguous \u2192 empty
            m = re.fullmatch(r"([^\d]{0,4}?)(\d(?:[\d.,]*\d)?)([^\d]{0,5})", s)
            if m:
                interp = number_interpretations(m.group(2))
        return {-v for v in interp} if neg else interp
    return set()


def _fusion_is_cross_column(row: Row, parts: list[tuple[int, int]]) -> bool:
    """A glued run built from prints a COLUMN apart ('qty 24 | 158,97') is a
    different animal than spaced-thousands typography ('15 000,00'): the
    pixel gap between its parts is several glyphs wide, not one space."""
    boxes = [row.char_range_bbox(s, e) for s, e in parts]
    if len(boxes) < 2 or any(b is None for b in boxes):
        return False
    for (l_box, l_part), (r_box, _r) in zip(zip(boxes, parts),
                                            list(zip(boxes, parts))[1:]):
        glyph_w = (l_box[2] - l_box[0]) / max(1, l_part[1] - l_part[0])
        if r_box[0] - l_box[2] > 2.5 * max(glyph_w, 1e-6):
            return True
    return False


def match_number(rows: list[Row], value, *, percent: bool = False) -> list[RawMatch]:
    targets = value_number_set(value)
    if not targets:
        return []
    abs_targets = {abs(t) for t in targets}
    matches: list[RawMatch] = []
    for row in rows:
        text = row.text.replace("\u00a0", " ")
        raw_runs = [(m.start(), m.end()) for m in _NUM_RUN.finditer(text)]
        # both readings, candidate-set style: "24 158,97" is EITHER the single
        # number 24158.97 (spaced thousands) OR qty 24 next to price 158.97 \u2014
        # geometry can't tell, so both spans compete and anchors disambiguate
        runs = list(dict.fromkeys(raw_runs + _glue_spaced_thousands(text, raw_runs)))
        raw_set = set(raw_runs)
        for s, e in runs:
            fused = ((s, e) not in raw_set
                     and _fusion_is_cross_column(
                         row, [r for r in raw_runs if s <= r[0] and r[1] <= e]))
            interp = number_interpretations(text[s:e])
            if not interp & abs_targets:
                continue
            # negativity: leading '-' or accounting parentheses (E-21)
            before = text[:s].rstrip()
            neg = before.endswith("-") or (before.endswith("(") and ")" in text[e:e + 3])
            signs = {t < 0 for t in targets}
            if len(signs) == 1 and neg != signs.pop():
                # sign disagrees in BOTH directions: OCR drops minus glyphs
                # ('-22,90' reads '22,90'), and a credit note prints -1200
                # where the extraction says 1200 — the digits are proven,
                # the sign is not. Fuzzy pin, never exact, never not_found.
                matches.append(RawMatch(row=row, start=s, end=e, score=0.85,
                                        exact=False, matched_text=text[s:e],
                                        fused=fused))
                continue
            score = 1.0
            if percent:
                after = text[e:e + 3].lstrip()
                score = 1.0 if after.startswith("%") else 0.9
            matches.append(RawMatch(row=row, start=s, end=e, score=score,
                                    exact=True, matched_text=text[s:e],
                                    fused=fused))
    if matches:
        return matches
    # OCR-damage fallbacks — fuzzy only, never verified without other proof:
    # 1. lost separator: "27,33" read as "2733" → cents reading (run / 100)
    # 2. merged neighbor: a lone qty glyph fused into the next number,
    #    "2" + "112,22" read as "2112,22" → 1-2 digit prefix split; the box
    #    covers only the prefix glyphs
    short_targets = {t for t in abs_targets
                     if t == t.to_integral_value() and 0 < t < 100}
    for row in rows:
        text = row.text.replace(" ", " ")
        for m in _NUM_RUN.finditer(text):
            run = m.group(0)
            if run.isdigit() and len(run) >= 3 and Decimal(run) / 100 in abs_targets:
                matches.append(RawMatch(row=row, start=m.start(), end=m.end(),
                                        score=0.85, exact=False,
                                        matched_text=run))
                continue
            if short_targets and len(run) >= 4:
                for plen in (1, 2):
                    prefix, rest = run[:plen], run[plen:]
                    if (prefix.isdigit() and Decimal(prefix) in short_targets
                            and rest[:1].isdigit() and number_interpretations(rest)):
                        matches.append(RawMatch(
                            row=row, start=m.start(), end=m.start() + plen,
                            score=0.75, exact=False, matched_text=prefix))
                        break
    return matches


# ------------------------------------------------------------------ dates ---

MONTHS: dict[str, int] = {}
_MONTH_TABLES = {
    1: ["january", "jan", "januar", "januara", "januari", "janvaris", "janvari",
        "janvara", "gennaio", "janvier", "ledna", "leden", "stycznia", "styczen"],
    2: ["february", "feb", "februar", "februara", "februaris", "februari",
        "fevrier", "unora", "unor", "lutego", "luty"],
    3: ["march", "mar", "marec", "marca", "marts", "marta", "marz", "maerz",
        "mars", "brezna", "brezen", "marzec"],
    4: ["april", "apr", "aprila", "aprilis", "aprili", "avril", "dubna",
        "duben", "kwietnia", "kwiecien"],
    5: ["may", "maj", "maja", "maijs", "maija", "mai", "kvetna", "kveten"],
    6: ["june", "jun", "juna", "junijs", "junija", "juni", "juin", "cervna",
        "cerven", "czerwca", "czerwiec"],
    7: ["july", "jul", "jula", "julijs", "julija", "juli", "juillet",
        "cervence", "cervenec", "lipca", "lipiec"],
    8: ["august", "aug", "augusta", "augusts", "avgusts", "aout", "srpna",
        "srpen", "sierpnia", "sierpien"],
    9: ["september", "sep", "sept", "septembra", "septembris", "septembri",
        "zari", "wrzesnia", "wrzesien"],
    10: ["october", "oct", "oktober", "oktobra", "oktobris", "oktobri",
         "octobre", "rijna", "rijen", "pazdziernika", "pazdziernik"],
    11: ["november", "nov", "novembra", "novembris", "novembri", "novembre",
         "listopadu", "listopad", "listopada"],
    12: ["december", "dec", "decembra", "decembris", "decembri", "decembre",
         "prosince", "prosinec", "grudnia", "grudzien"],
}
for _m, _names in _MONTH_TABLES.items():
    for _n in _names:
        MONTHS[_n] = _m

_D_NUMERIC = re.compile(  # receipts glue a time to the date: '05-20-2509:56' —
    # the lazy year + lookahead stop before a glued hh:mm instead of eating it
    r"(?<!\d)(\d{1,4})\s?([./-])\s?(\d{1,2})\s?\2\s?(\d{1,4}?)(?=\d{1,2}:\d{2}|\D|$)")
# the month-name run is bounded: an unbounded [A-Za-z]+ backtracks
# quadratically across any long alphabetic run (measured: 20k chars = 2.5s,
# reachable from DOCUMENT text via match_date and from BYO values)
_D_MONTHNAME_MDY = re.compile(r"([A-Za-zÀ-ž]{1,24})\.?\s+(\d{1,2})\.?,?\s+(\d{4})")
_D_MONTHNAME_DMY = re.compile(r"(?<!\d)(\d{1,2})\.?\s+([A-Za-zÀ-ž]{1,24})\.?\s?,?\s+(\d{4})")


def _valid_ymd(y: int, m: int, d: int) -> Optional[tuple[int, int, int]]:
    if not (1 <= m <= 12 and 1 <= d <= 31 and 1900 <= y <= 2100):
        return None
    return (y, m, d)


def _expand_year(y: int) -> int:
    if y >= 100:
        return y
    return 2000 + y if y < 70 else 1900 + y


def date_interpretations_in(text: str) -> list[tuple[int, int, set[tuple[int, int, int]]]]:
    """Date hits in text: (start, end, {(y,m,d) candidate tuples})."""
    hits: list[tuple[int, int, set]] = []
    for m in _D_NUMERIC.finditer(text):
        a, b, c = int(m.group(1)), int(m.group(3)), int(m.group(4))
        cands: set[tuple[int, int, int]] = set()
        if len(m.group(1)) == 4:                      # yyyy-mm-dd
            t = _valid_ymd(a, b, c)
            if t: cands.add(t)
        else:
            t = _valid_ymd(_expand_year(c), b, a)      # dd.mm.yyyy (European default)
            if t: cands.add(t)
            if a <= 12 and b <= 31:
                t = _valid_ymd(_expand_year(c), a, b)  # mm/dd/yyyy when plausible
                if t: cands.add(t)
        if cands:
            hits.append((m.start(), m.end(), cands))
    for rx, order in ((_D_MONTHNAME_MDY, "mdy"), (_D_MONTHNAME_DMY, "dmy")):
        for m in rx.finditer(text):
            if order == "mdy":
                month_word, day, year = m.group(1), int(m.group(2)), int(m.group(3))
            else:
                day, month_word, year = int(m.group(1)), m.group(2), int(m.group(3))
            month = MONTHS.get(canon_value(month_word))
            if not month:
                continue
            t = _valid_ymd(year, month, day)
            if t:
                hits.append((m.start(), m.end(), {t}))
    return hits


def value_date_set(value) -> set[tuple[int, int, int]]:
    if not isinstance(value, str):
        return set()
    out: set[tuple[int, int, int]] = set()
    for _s, _e, cands in date_interpretations_in(value.strip()):
        out |= cands
    return out


def match_date(rows: list[Row], value) -> list[RawMatch]:
    targets = value_date_set(value)
    if not targets:
        return []
    matches: list[RawMatch] = []
    for row in rows:
        for s, e, cands in date_interpretations_in(row.text):
            if cands & targets:
                matches.append(RawMatch(row=row, start=s, end=e, score=1.0,
                                        exact=True, matched_text=row.text[s:e]))
    return matches


# ---------------------------------------------------------------- id/text ---

def _canon_range_to_text_range(row: Row, c_start: int, c_end: int) -> tuple[int, int]:
    t_start = row.canon_idx[c_start]
    t_end = row.canon_idx[c_end - 1] + 1
    return t_start, t_end


_SYMBOL_CONFUSABLES = {"£": "fFEe£", "€": "eEcC€", "$": "sS$"}


def _match_symbol_only(rows: list[Row], value) -> list[RawMatch]:
    """Values the canonicalizer strips entirely (£, €, $, %) are matched as
    raw substrings — a currency symbol IS on the page even if canon says ''.
    OCR reads the glyphs as look-alikes ('£952.00' → 'f952.00'): a confusable
    directly prefixing a digit, outside a word, pins fuzzy."""
    sym = str(value).strip()
    matches: list[RawMatch] = []
    if not sym:
        return matches
    for row in rows:
        for pos in find_all(row.text, sym):
            matches.append(RawMatch(row=row, start=pos, end=pos + len(sym),
                                    score=1.0, exact=True, matched_text=sym))
    if matches:
        return matches
    confusables = _SYMBOL_CONFUSABLES.get(sym)
    if not confusables:
        return matches
    pattern = re.compile(rf"(?<![A-Za-z0-9])[{confusables}](?=\d)")
    for row in rows:
        for m in pattern.finditer(row.text):
            matches.append(RawMatch(row=row, start=m.start(), end=m.end(),
                                    score=0.8, exact=False,
                                    matched_text=row.text[m.start():m.end()]))
    return matches


def _pattern_matches(rows: list[Row], value, pattern: str) -> list[RawMatch]:
    """Schema-declared shape tolerance: extractors return canonical ids
    ('IE6356477S'), pages print local forms ('6356477S'). A print that
    fullmatches the declared pattern and contains — or is contained by —
    the value's canon core is the value in different clothes."""
    needle = canon_value(str(value))
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error:
        return []
    if not needle or not rx.fullmatch(needle):
        return []  # the value itself must fit the declared shape
    core_needle = needle.lstrip("abcdefghijklmnopqrstuvwxyz")
    matches: list[RawMatch] = []
    for row in rows:
        for m in rx.finditer(row.canon):
            span = m.group(0)
            if len(span) < 6 or span == needle:
                continue  # short cores are E-22 territory; equals = exact path
            # a print may DROP the alpha prefix, never digits — a digit
            # subset is a different number, not a variant of this one
            core_span = span.lstrip("abcdefghijklmnopqrstuvwxyz")
            core = len(core_span) >= 6 and core_needle == core_span
            # containment alone is not enough: the find_all guard just
            # rejected spans whose neighbouring DIGITS extend the run, and
            # this fallback must not re-admit them — only letter neighbours
            # (a dropped alpha prefix) keep the identity
            w_at = -1
            i = span.find(needle)
            while i >= 0:
                after = i + len(needle)
                if ((i == 0 or not span[i - 1].isdigit())
                        and (after >= len(span) or not span[after].isdigit())):
                    w_at = i
                    break
                i = span.find(needle, i + 1)
            if w_at < 0 and not core:
                continue
            if w_at >= 0:  # box the value itself, not the whole span
                start, end = m.start() + w_at, m.start() + w_at + len(needle)
            else:          # only the letter-stripped core is the value
                start, end = m.start() + len(span) - len(core_span), m.end()
            s, e = _canon_range_to_text_range(row, start, end)
            matches.append(RawMatch(row=row, start=s, end=e, score=1.0,
                                    exact=True, matched_text=row.text[s:e]))
    return matches


def match_id(rows: list[Row], value, pattern: Optional[str] = None) -> list[RawMatch]:
    needle = canon_value(str(value))
    if not needle:
        return _match_symbol_only(rows, value)
    # ids are exact-ish strings: a hit inside a longer digit run or inside a
    # word is a DIFFERENT identifier ('2024' in '20241231-77', 'kc' in
    # 'Rekchek', 'FV2025001' in 'FV2025001234'): every id gets the guard;
    # lettered ones over 4 chars used to skip it, which let a truncated id
    # verify as a prefix of the full print
    matches: list[RawMatch] = []
    for row in rows:
        for pos in find_all(row.canon, needle):
            s, e = _canon_range_to_text_range(row, pos, pos + len(needle))
            # digit ids own their separators ('1 00018615', '16611/2025' are
            # the id's print split by OCR or formatting) — only a digit
            # extending the run changes identity. Lettered codes keep the
            # strict no-stitch rule.
            # the no-stitch rule is for COMPACT needles only: they carry no
            # internal redundancy, so canon folding can stitch them out of
            # unrelated tokens ('CZK' on 'CZ, K'). A long lettered id has
            # redundancy — and its PRINT legitimately carries separators the
            # value omits ('FN 82573g', every grouped IBAN).
            stitched = (len(needle) <= _COMPACT_NEEDLE_LEN
                        and not needle.isdigit()
                        and _stitched(value, row.text[s:e]))
            if (stitched
                    or _id_run_infix(row, pos, len(needle), needle.isdigit())):
                continue
            # canon erased the separators — when the PRINT's separators say
            # "date" or "decimal amount" and the value is a bare digit id,
            # the identity is in doubt ('20260315' on '2026-03-15'): pin it,
            # but never as verified. Space-split digit runs stay exact (OCR
            # splits are why digit ids own their separators at all).
            doubted = (needle.isdigit() and len(needle) >= 4
                       and _separator_kind_mismatch(row.text[s:e]))
            matches.append(RawMatch(row=row, start=s, end=e,
                                    score=0.85 if doubted else 1.0,
                                    exact=not doubted,
                                    matched_text=row.text[s:e]))
    if matches:
        return matches
    if pattern:
        matches = _pattern_matches(rows, value, pattern)
        if matches:
            return matches
    if len(needle) > 32:
        # same floor match_text has: the sliding SequenceMatcher sweep is
        # O(rows x len) — an unlocatable 64-char id measured 11s per 400
        # dense rows, and ids get no BLOCK fallback to hand off to
        return matches
    for row in rows:  # fuzzy fallback → low_confidence (confusables E-14)
        for c_s, c_e, r in fuzzy_windows(row.canon, needle, ID_FUZZY_RATIO, len_slack=1):
            s, e = _canon_range_to_text_range(row, c_s, c_e)
            if ((len(needle) <= _COMPACT_NEEDLE_LEN and not needle.isdigit()
                 and _stitched(value, row.text[s:e]))
                    or _id_run_infix(row, c_s, c_e - c_s, needle.isdigit())):
                continue
            matches.append(RawMatch(row=row, start=s, end=e, score=r,
                                    exact=False, matched_text=row.text[s:e]))
    return matches


# canon folding erases separators, so a compact code can "match" a span
# stitched from unrelated tokens ("CZK" on "CZ, K"). Values this short carry
# no internal redundancy to survive that; longer values do (E-22 analog).
_COMPACT_NEEDLE_LEN = 4
_TOKEN_SEPARATORS = re.compile(r"[\s,;:|/\\]")


def _stitched(value: str, matched: str) -> bool:
    return (not _TOKEN_SEPARATORS.search(str(value).strip())
            and bool(_TOKEN_SEPARATORS.search(matched)))


def _separator_kind_mismatch(matched_text: str) -> bool:
    """True when the matched print reads as a DATE or a DECIMAL AMOUNT —
    i.e. its separators declare a different kind than a bare digit id."""
    mt = matched_text.strip()
    if not any(not c.isalnum() and not c.isspace() for c in mt):
        return False  # pure or space-split digits: an id's own print
    if value_date_set(mt):
        return True   # '2026-03-15', '15.03.2026'
    return bool(re.fullmatch(r"\d{1,3}(?:[.,]\d{3})*[.,]\d{1,2}",
                             mt.replace(" ", "")))


def _id_run_infix(row: Row, c_start: int, c_len: int, digits_only: bool) -> bool:
    """True when the canon hit continues into the same source run in a way
    that changes its identity. For an all-digit needle only a DIGIT
    neighbor does ('2024' inside '20241231') — a letter neighbor is a
    prefix like the country code in 'CZ45357366', still the same number.
    A needle with letters is a code; any alnum neighbor makes it part of a
    longer word ('kc' inside 'Rekchek'). Checked in TEXT space so
    punctuation still breaks a run."""
    extends = str.isdigit if digits_only else str.isalnum
    t_before = row.canon_idx[c_start] - 1 if c_start > 0 else -1
    if t_before >= 0 and extends(row.text[t_before]):
        return True
    last_t = row.canon_idx[c_start + c_len - 1]
    t_after = last_t + 1
    return t_after < len(row.text) and extends(row.text[t_after])


def _word_infix(row_text: str, s: int, e: int) -> bool:
    """Letter neighbors mean the span sits inside a word ('kc' in 'Rekchek').
    Digit neighbors stay legal: unit prints glue to amounts ('1900,00Kč')."""
    before = row_text[s - 1] if s > 0 else " "
    after = row_text[e] if e < len(row_text) else " "
    return before.isalpha() or after.isalpha()


def match_text(rows: list[Row], value) -> list[RawMatch]:
    needle = canon_value(str(value))
    if not needle:
        return _match_symbol_only(rows, value)
    compact = len(needle) <= _COMPACT_NEEDLE_LEN
    matches: list[RawMatch] = []
    for row in rows:
        for pos in find_all(row.canon, needle):
            s, e = _canon_range_to_text_range(row, pos, pos + len(needle))
            if compact and (_stitched(value, row.text[s:e])
                            or _word_infix(row.text, s, e)):
                continue
            matches.append(RawMatch(row=row, start=s, end=e, score=1.0,
                                    exact=True, matched_text=row.text[s:e]))
    if matches:
        return matches
    if len(needle) > 32:
        # the sliding SequenceMatcher sweep is O(rows x len) and measured in
        # MINUTES for one long unlocatable name on big documents; long TEXT
        # values are exactly what the token-union BLOCK fallback handles
        return matches
    slack = max(1, len(needle) // 8)
    for row in rows:
        for c_s, c_e, r in fuzzy_windows(row.canon, needle, TEXT_FUZZY_RATIO, len_slack=slack):
            s, e = _canon_range_to_text_range(row, c_s, c_e)
            if compact and (_stitched(value, row.text[s:e])
                            or _word_infix(row.text, s, e)):
                continue
            matches.append(RawMatch(row=row, start=s, end=e, score=r,
                                    exact=False, matched_text=row.text[s:e]))
    return matches


def match_block(rows: list[Row], value) -> list[RawMatch]:
    """Multi-line values (E-27): token-set matching with COLUMN discipline.

    Two-column layouts interleave unrelated text into one visual row, so:
    - tokens shorter than 2 canonical chars never match ("3" inside "0308")
    - every token occurrence gets its own box; occurrences are clustered by
      horizontal position and only the dominant cluster counts — a block can
      never span disjoint columns
    - the reported bbox is the union of matched token boxes (spans), not the
      first-to-last stretch
    """
    raw_tokens = [canon_value(t) for t in re.split(r"\s+", str(value).strip())]
    tokens = list(dict.fromkeys(t for t in raw_tokens if len(t) >= 2))
    # single-char tokens can't match inside runs, but they must still COUNT:
    # dropped from the denominator too, a wrong house number scored a
    # perfect overlap ('Hlavna 5' verified against a printed 'Hlavna 9').
    # They match only as standalone runs — "3" inside "0308" stays a miss.
    short_tokens = list(dict.fromkeys(t for t in raw_tokens
                                      if len(t) == 1))
    if not tokens:
        return match_text(rows, value)
    total_tokens = len(tokens) + len(short_tokens)

    matches: list[RawMatch] = []
    for row in rows:
        occs: list[tuple[str, int, int, tuple[float, float, float, float]]] = []
        for tok in tokens:
            for pos in find_all(row.canon, tok):
                s, e = _canon_range_to_text_range(row, pos, pos + len(tok))
                bbox = row.char_range_bbox(s, e)
                if bbox is not None:
                    occs.append((tok, s, e, bbox))
        for tok in short_tokens:
            for pos in find_all(row.canon, tok):
                s, e = _canon_range_to_text_range(row, pos, pos + 1)
                before = row.text[s - 1] if s > 0 else " "
                after = row.text[e] if e < len(row.text) else " "
                if before.isalnum() or after.isalnum():
                    continue
                bbox = row.char_range_bbox(s, e)
                if bbox is not None:
                    occs.append((tok, s, e, bbox))
        if not occs:
            continue

        # cluster occurrences by horizontal gap; a gap wider than ~8 average
        # glyphs separates columns
        occs.sort(key=lambda o: o[3][0])
        widths = [(o[3][2] - o[3][0]) / max(1, len(o[0])) for o in occs]
        glyph_w = sorted(widths)[len(widths) // 2]
        gap_limit = max(8 * glyph_w, 1e-6)
        clusters: list[list[tuple]] = [[occs[0]]]
        for o in occs[1:]:
            if o[3][0] - max(p[3][2] for p in clusters[-1]) > gap_limit:
                clusters.append([o])
            else:
                clusters[-1].append(o)

        def distinct(cluster):  # one occurrence per token within the cluster
            seen: dict[str, tuple] = {}
            for o in cluster:
                if o[0] not in seen:
                    seen[o[0]] = o
            return list(seen.values())

        best = max(clusters, key=lambda c: len(distinct(c)))
        kept = distinct(best)
        overlap = len(kept) / total_tokens
        if overlap < BLOCK_TOKEN_OVERLAP:
            continue
        kept.sort(key=lambda o: o[1])
        spans = [(o[1], o[2]) for o in kept]
        evidence = " ".join(row.text[s:e] for s, e in spans)
        matches.append(RawMatch(
            row=row, start=spans[0][0], end=spans[-1][1],
            score=overlap, exact=overlap >= 0.999,
            matched_text=evidence, spans=spans))
    return matches
