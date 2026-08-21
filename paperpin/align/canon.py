"""Canonicalization with offset maps (§6.3) — THE core trick (E-11).

canonical_map("Reg.No: 40103567891  VAT:LV40103567891") returns a squeezed
accent-folded alphanumeric string plus an index array mapping every canonical
character back to its position in the original string, so a canonical
substring hit can be projected back onto exact source characters (and from
there onto pixels).
"""
from __future__ import annotations

import unicodedata
from difflib import SequenceMatcher
from functools import lru_cache


@lru_cache(maxsize=None)  # the glyph universe is small; a 3x win on every
def fold_char(ch: str) -> str:  # canon path (measured, m18)
    """Fold one character to its canonical skeleton, lowercased.

    Latin diacritics fold to ASCII ('Č'→'c', 'é'→'e', 'ā'→'a'). Non-Latin
    scripts keep their letters (mark-stripped, casefolded): the engine is
    script-agnostic and Cyrillic / Greek / CJK text must not canonize to
    nothing. Decimal digits of any script normalize to ASCII. Punctuation
    contributes nothing. Multi-character expansions survive whole — 'ﬁ'
    folds to 'fi' and 'ß' to 'ss', because PDF text layers print them and a
    value spelled plainly must still match."""
    decomposed = unicodedata.normalize("NFKD", ch)
    out: list[str] = []
    for c in decomposed:
        if unicodedata.combining(c):
            continue
        low = c.lower()
        if "a" <= low <= "z" or "0" <= low <= "9":
            out.append(low)
            continue
        if c.isdigit():
            try:
                out.append(str(unicodedata.decimal(c)))
                continue
            except (TypeError, ValueError):
                pass
        if c.isalnum():
            folded = c.casefold()
            out.extend(f for f in folded if f.isalnum())
    return "".join(out)


def canonical_map(s: str) -> tuple[str, list[int]]:
    """Return (canonical string, index array) where canon[i] originated at
    s[idx[i]]. Canonical = accent-folded lowercase alphanumerics only; a
    source character that folds to several canonical characters (ligatures,
    'ß') repeats its index for each of them."""
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        folded = fold_char(ch)
        for f in folded:
            out.append(f)
            idx.append(i)
    return "".join(out), idx


def canon_value(s: str) -> str:
    return canonical_map(s)[0]


def ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def find_all(haystack: str, needle: str) -> list[int]:
    """All start offsets of needle in haystack (overlapping allowed)."""
    if not needle:
        return []
    hits = []
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            return hits
        hits.append(pos)
        start = pos + 1


def fuzzy_windows(haystack: str, needle: str, min_ratio: float,
                  len_slack: int = 1) -> list[tuple[int, int, float]]:
    """Sliding-window fuzzy search: windows of len(needle)±len_slack scored by
    SequenceMatcher ratio. Returns (start, end, ratio) hits ≥ min_ratio,
    deduplicated to local maxima."""
    n = len(needle)
    if n == 0 or len(haystack) == 0:
        return []
    # whole-haystack multiset bound: no window can match material the row
    # simply doesn't contain — this skips most rows before any window scan
    # (the sweep measured 1.3s/field on 50-page docs without it)
    from collections import Counter
    have = Counter(haystack)
    possible = sum(min(c, have[ch]) for ch, c in Counter(needle).items())
    if 2.0 * possible / (n + max(1, n - len_slack)) < min_ratio:
        return []
    hits: list[tuple[int, int, float]] = []
    sm = SequenceMatcher(autojunk=False)
    sm.set_seq2(needle)  # difflib caches seq2 stats across set_seq1 calls
    for wlen in range(max(1, n - len_slack), n + len_slack + 1):
        if wlen > len(haystack):
            continue
        for start in range(0, len(haystack) - wlen + 1):
            sm.set_seq1(haystack[start:start + wlen])
            if (sm.real_quick_ratio() < min_ratio
                    or sm.quick_ratio() < min_ratio):
                continue
            r = sm.ratio()
            if r >= min_ratio:
                hits.append((start, start + wlen, r))
    hits.sort(key=lambda h: -h[2])
    chosen: list[tuple[int, int, float]] = []
    for h in hits:
        if all(h[1] <= c[0] or h[0] >= c[1] for c in chosen):  # no overlap
            chosen.append(h)
    return chosen
