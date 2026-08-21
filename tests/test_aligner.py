"""Aligner semantics on hand-built segments — no OCR, fully deterministic."""
from paperpin.align.aligner import align_fields
from paperpin.align.rows import build_rows
from paperpin.types import FieldSpec, FieldType, Segment, Status

PAGE = {0: (600.0, 800.0)}


def seg(text, x0, top, x1, bottom, page=0, conf=1.0):
    return Segment(text=text, x0=x0, top=top, x1=x1, bottom=bottom,
                   conf=conf, page=page)


def make_rows(segments):
    return build_rows(segments)


def run(segments, extraction, specs=None):
    specs = {k: FieldSpec.coerce(k, v) for k, v in (specs or {}).items()}
    for name, value in extraction.items():
        if name not in specs:
            from paperpin.schemas import infer_spec
            specs[name] = infer_spec(name, value)
    return align_fields(make_rows(segments), PAGE, extraction, specs)


BASE = [
    seg("Invoice", 40, 40, 100, 55), seg("No.", 104, 40, 130, 55),
    seg("20260461", 140, 40, 220, 55),
    seg("Total", 380, 700, 430, 715), seg("146,14", 480, 700, 540, 715),
    seg("Page", 40, 780, 70, 792), seg("1", 74, 780, 80, 792),
    seg("of", 84, 780, 98, 792), seg("1", 102, 780, 108, 792),
    seg("Qty", 40, 400, 70, 415),
    seg("1", 150, 400, 158, 415),
    seg("Unit", 200, 400, 240, 415), seg("48,00", 260, 400, 300, 415),
]


def test_not_present_vs_not_found_e25():
    res = run(BASE, {"total": "146,14", "iban": None, "fake": "ZZZ-999"})
    assert res["total"].status == Status.VERIFIED
    assert res["iban"].status == Status.NOT_PRESENT
    assert res["fake"].status == Status.NOT_FOUND


def test_qty_anchor_disambiguation_e22():
    # qty=1 appears as "Page 1 of 1" too — the anchor must pin the table row
    res = run(BASE, {"qty": "1"}, {"qty": {"type": "number", "anchors": ["qty"]}})
    fr = res["qty"]
    assert fr.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert fr.bbox is not None
    # table row sits at y≈400..415 → normalized ~0.5; "Page 1 of 1" at ~0.97
    cy = (fr.bbox[1] + fr.bbox[3]) / 2
    assert 0.45 < cy < 0.58, f"pinned wrong instance at cy={cy}"
    assert fr.anchor == "qty"


def test_ambiguous_when_different_content_ties():
    segments = [
        seg("Ref", 40, 100, 70, 115), seg("555", 80, 100, 110, 115),
        seg("Code", 300, 500, 340, 515), seg("555", 350, 500, 380, 515),
    ]
    res = run(segments, {"num": "555"}, {"num": {"type": "number"}})
    # two instances of the same evidence text → NOT ambiguous (E-23), pinned + listed
    assert res["num"].status == Status.VERIFIED
    assert len(res["num"].candidates) == 2


def test_quote_shape_accepted():
    res = run(BASE, {"total": {"value": "146,14", "quote": "Total 146,14"}})
    assert res["total"].status == Status.VERIFIED
    assert res["total"].quote == "Total 146,14"


# value repeats on two rows whose labels are NOT in the anchor lexicon —
# without the quote the aligner has nothing but "topmost wins"
REPEATED_TOTAL = [
    seg("Artikel", 40, 100, 100, 115), seg("88,53", 200, 100, 250, 115),
    seg("Karte", 40, 400, 90, 415), seg("88,53", 200, 400, 250, 415),
]


def test_quote_context_picks_the_quoted_instance():
    # real-doc regression (IMG_9140): value repeats, the model's quote names
    # the lower line — the pin must follow the quote, not "topmost wins"
    res = run(REPEATED_TOTAL,
              {"total": {"value": "88,53", "quote": "Karte 88,53"}},
              {"total": {"type": "number"}})
    fr = res["total"]
    assert fr.bbox is not None
    cy = (fr.bbox[1] + fr.bbox[3]) / 2
    assert cy > 0.4, f"pinned the top twin at cy={cy}, not the quoted row"


def test_value_only_quote_changes_nothing():
    # a quote that is just the value (+currency scrap) carries no location
    # context — the leftover 1-2 chars must not hand a bonus to whichever row
    # happens to share them (ahelpgroup bug: quote "3 600,96 Kč" → ctx "kc")
    segments = [
        seg("Artikel", 40, 100, 100, 115), seg("88,53", 200, 100, 250, 115),
        seg("88,53", 200, 400, 250, 415), seg("XY", 260, 400, 285, 415),
    ]
    res_q = run(segments, {"total": {"value": "88,53", "quote": "88,53 XY"}},
                {"total": {"type": "number"}})
    res_n = run(segments, {"total": "88,53"}, {"total": {"type": "number"}})
    assert res_q["total"].bbox == res_n["total"].bbox


def test_quote_matching_nothing_falls_back_gracefully():
    res = run(REPEATED_TOTAL,
              {"total": {"value": "88,53", "quote": "ZZZ NONSENSE 999"}},
              {"total": {"type": "number"}})
    fr = res["total"]
    assert fr.bbox is not None  # still pinned by the ordinary rules
    assert fr.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_merged_token_id_match_e11():
    segments = [seg("VAT:LV40103567891", 40, 200, 240, 215)]
    res = run(segments, {"vat_number": "LV40103567891"},
              {"vat_number": {"type": "id"}})
    fr = res["vat_number"]
    assert fr.status == Status.VERIFIED
    # sub-box must cover only the id part, not the "VAT:" prefix
    assert fr.bbox[0] > 40 / 600


def test_cross_row_merge_e15():
    segments = [
        seg("IBAN", 40, 300, 80, 315),
        seg("SK73 1100 0000", 40, 320, 160, 335),
        seg("0026 2902 8990", 40, 340, 160, 355),
    ]
    res = run(segments, {"iban": "SK73 1100 0000 0026 2902 8990"},
              {"iban": {"type": "id"}})
    fr = res["iban"]
    assert fr.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert fr.bbox is not None
    # box unions both value rows
    assert fr.bbox[3] - fr.bbox[1] > (30 / 800)


def test_block_address_e27():
    segments = [
        seg("Tyršovo nábrežie 12", 40, 500, 200, 515),
        seg("85101 Bratislava - Petržalka", 40, 520, 240, 535),
    ]
    res = run(segments,
              {"supplier_address": "Tyršovo nábrežie 12, 85101 Bratislava"},
              {"supplier_address": {"type": "block"}})
    assert res["supplier_address"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_block_two_column_no_smear():
    # two-column layout: address in LEFT column, unrelated labels in RIGHT
    # column of the SAME visual rows — the box must stay in the left column
    segments = [
        seg("Račianska 17", 40, 100, 130, 115), seg("Objednávka č.:", 380, 100, 480, 115),
        seg("831 02 Bratislava 3", 40, 120, 170, 135), seg("zo dňa: 0308", 380, 120, 470, 135),
    ]
    res = run(segments, {"supplier_address": "Račianska 17 831 02 Bratislava 3"},
              {"supplier_address": {"type": "block"}})
    fr = res["supplier_address"]
    assert fr.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert fr.bbox is not None
    # page width 600 → right column starts at 380/600≈0.63; box must end well left
    assert fr.bbox[2] < 0.5, f"box smeared into the right column: {fr.bbox}"


def test_block_short_tokens_ignored():
    # the single-char token "3" must not match inside "0308"
    segments = [seg("Konštantný symbol: 0308", 300, 60, 520, 75),
                seg("Hlavná 7", 40, 200, 100, 215)]
    res = run(segments, {"addr": "Hlavná 7 3"}, {"addr": {"type": "block"}})
    fr = res["addr"]
    if fr.bbox is not None:  # if located, it must be at "Hlavná", not "0308"
        assert fr.bbox[1] > 0.2


def test_symbol_only_currency():
    # "£" must match on the page even though canon strips it (real-doc bug)
    segments = [seg("Total", 40, 700, 90, 715), seg("£147.88", 100, 700, 160, 715)]
    res = run(segments, {"currency": "£", "total": "147.88"},
              {"currency": {"type": "text"}, "total": {"type": "number"}})
    assert res["currency"].status == Status.VERIFIED
    assert res["total"].status == Status.VERIFIED


def test_percent_not_matching_year_e21():
    segments = [
        seg("Issued", 40, 100, 90, 115), seg("2023-05-01", 100, 100, 190, 115),
        seg("VAT", 40, 400, 70, 415), seg("23", 80, 400, 100, 415), seg("%", 104, 400, 112, 415),
    ]
    res = run(segments, {"vat_rate": "23"},
              {"vat_rate": {"type": "percent", "anchors": ["vat"]}})
    fr = res["vat_rate"]
    assert fr.status == Status.VERIFIED
    cy = (fr.bbox[1] + fr.bbox[3]) / 2
    assert cy > 0.4, "must pin the VAT row, not the year inside the date"


def test_currency_binds_to_the_pinned_totals_line():
    # 'Kč' prints on many lines; the instance NEXT TO the pinned total is the
    # semantically right one, even when a repeat sits higher on the page
    segs = [
        seg("Cena", 40, 100, 80, 115), seg("Kč", 90, 100, 110, 115),
        seg("Total", 300, 700, 350, 715), seg("146,14", 380, 700, 440, 715),
        seg("Kč", 450, 700, 470, 715),
    ]
    res = run(segs, {"total": "146,14", "currency": "Kč"})
    cur = res["currency"]
    assert cur.bbox is not None
    assert abs(cur.bbox[1] * 800 - 700) < 10, f"currency must sit on the total's line, got y={cur.bbox[1]*800}"
    assert any("total" in n for n in cur.notes)


def test_currency_keeps_topmost_when_no_total_is_pinned():
    segs = [
        seg("Cena", 40, 100, 80, 115), seg("Kč", 90, 100, 110, 115),
        seg("Zaplaceno", 300, 700, 390, 715), seg("Kč", 450, 700, 470, 715),
    ]
    res = run(segs, {"currency": "Kč"})
    cur = res["currency"]
    assert cur.bbox is not None
    assert abs(cur.bbox[1] * 800 - 100) < 10, "no total pinned: reading-order tiebreak stays"


def test_block_two_column_multirow_address_pins_clean():
    # two-column layout, address wraps over TWO rows in the RIGHT column while
    # the left column carries unrelated contact lines — the merged unit must be
    # the right-column cell chain, not the polluted full lines
    segs = [
        seg("Fax: 257 316 659", 40, 100, 170, 115),
        seg("třída Karla IV. 502/23", 400, 100, 560, 115),
        seg("Tel: 495 123 456", 40, 124, 170, 139),
        seg("50002 Hradec Králové", 400, 124, 555, 139),
    ]
    res = run(segs, {"customer_address": "třída Karla IV. 502/23\n50002 Hradec Králové"},
              {"customer_address": {"type": "block"}})
    fr = res["customer_address"]
    assert fr.status != Status.NOT_FOUND, fr.notes
    assert fr.bbox is not None
    assert fr.bbox[0] * 600 > 300, f"box must sit on the right column, got {fr.bbox}"


def test_block_survives_left_column_interleave():
    # Corpus-doc shape: the address's first line shares a printed line with a
    # left-column 'Fax:' entry, and an unrelated left-column line sits BETWEEN
    # the two address lines — the right-column cell chain must carry the match
    segs = [
        seg("Tel: 257 316 658", 40, 88, 168, 96),
        seg("Fax: 257 316 659", 40, 100, 168, 108),
        seg("třída Karla IV. 502/23", 313, 100, 394, 108),
        seg("E-mail: orders@cipa.cz", 40, 112, 139, 119),
        seg("50002 Hradec Králové", 313, 119, 399, 126),
        seg("Web: www.cipa.cz", 40, 131, 139, 138),
    ]
    res = run(segs, {"customer_address":
                     "třída Karla IV. 502/23 50002 Hradec Králové Česká republika"},
              {"customer_address": {"type": "block"}})
    fr = res["customer_address"]
    assert fr.status != Status.NOT_FOUND, fr.notes
    assert fr.bbox is not None
    assert fr.bbox[0] * 600 > 250, f"box must sit on the address column, got {fr.bbox}"


def test_text_name_scattered_over_rows_falls_back_to_block():
    # Jollibee shape: the model glues a name from right-column lines that are
    # not vertically contiguous — contiguous window fails, token union pins
    segs = [
        seg("Apollo Construction Projects Ltd", 40, 100, 300, 112),
        seg("Jollibee", 460, 100, 530, 112),
        seg("31 Beaconsfield Street", 40, 124, 220, 136),
        seg("22 Leicester Square", 460, 124, 610, 136),
        seg("c/o Bee World UK Ltd", 460, 148, 620, 160),
    ]
    res = run(segs, {"customer_name": "Jollibee c/o Bee World UK Ltd"},
              {"customer_name": {"type": "text"}})
    fr = res["customer_name"]
    assert fr.status != Status.NOT_FOUND, fr.notes
    assert fr.bbox is not None
    assert fr.bbox[0] * 600 > 400, f"box must sit on the right column, got {fr.bbox}"


def test_pound_symbol_ocr_read_as_f_still_pins():
    # 123321.png: OCR reads '£952.00' as 'f952.00' — the symbol glyph is
    # confusable, its position is provable; fuzzy pin, never not_found
    segments = [seg("Total", 40, 700, 90, 715),
                seg("f952.00", 100, 700, 160, 715)]
    res = run(segments, {"currency": "£", "total": "952.00"},
              {"currency": {"type": "text"}, "total": {"type": "number"}})
    fr = res["currency"]
    assert fr.status != Status.NOT_FOUND
    assert fr.status == Status.LOW_CONFIDENCE, "confusable glyph is not proof"
    assert fr.bbox is not None and fr.bbox[0] * 600 < 130


def test_confusable_symbol_never_fires_inside_words():
    segments = [seg("ref2024 order", 40, 700, 160, 715)]
    res = run(segments, {"currency": "£"}, {"currency": {"type": "text"}})
    assert res["currency"].status == Status.NOT_FOUND


def test_cyrillic_document_grounds_end_to_end():
    # script-agnostic core: a Russian document (text-layer route — no OCR
    # model involvement) must ground exactly like a Latin one
    segments = [
        seg("Счёт № 12345", 40, 60, 200, 78),
        seg("Итого", 60, 700, 120, 715), seg("15 000,00", 380, 700, 470, 715),
    ]
    res = run(segments, {"invoice_number": "12345", "total": "15 000,00"},
              {"invoice_number": {"type": "id"}, "total": {"type": "number"}})
    assert res["invoice_number"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert res["total"].status == Status.VERIFIED
    assert res["total"].bbox[1] > 0.8


def test_compact_value_never_stitches_across_tokens():
    # real-corpus case: row "... CZ, Kontakt ..." canon-folds to
    # "...czkontakt..." and a 3-char currency code verified on the
    # frankenstein span "CZ, K". Compact single-token values must land
    # inside one token.
    segments = [
        seg("Praha 1, CZ,", 40, 60, 160, 75), seg("Kontakt: obchod", 166, 60, 300, 75),
    ]
    res = run(segments, {"currency": "CZK"}, {"currency": {"type": "text"}})
    assert res["currency"].status == Status.NOT_FOUND


def test_compact_value_still_matches_single_token_prints():
    segments = [seg("Celkem", 40, 700, 100, 715), seg("(CZK)", 110, 700, 160, 715)]
    res = run(segments, {"currency": "CZK"}, {"currency": {"type": "text"}})
    assert res["currency"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert res["currency"].evidence.strip("()") == "CZK"


def test_declared_alias_grounds_symbol_print():
    # generic alias mechanism: the schema declares that value "CZK" may
    # print as "Kč"; the invoice-domain map itself lives in schema hints
    segments = [seg("Celkem", 40, 700, 100, 715), seg("1 234", 120, 700, 170, 715),
                seg("Kč", 176, 700, 200, 715)]
    res = run(segments, {"currency": "CZK"})  # infer_spec + enrich hints path
    assert res["currency"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert res["currency"].evidence == "Kč"


def test_alias_symbol_stripped_by_canon_still_grounds():
    segments = [seg("Total", 40, 700, 90, 715), seg("55,00", 120, 700, 170, 715),
                seg("€", 176, 700, 190, 715)]
    res = run(segments, {"currency": "EUR"})
    assert res["currency"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_alias_never_invents_when_neither_form_prints():
    segments = [seg("Total", 40, 700, 90, 715), seg("55,00", 120, 700, 170, 715)]
    res = run(segments, {"currency": "CZK"})
    assert res["currency"].status == Status.NOT_FOUND


def test_compact_value_never_matches_inside_a_word():
    # alias 'Kč' canon-folds to 'kc', which hides inside 'Rekchek' —
    # compact matches must sit on token boundaries (letter neighbors
    # forbidden; digit neighbors stay legal for glued unit prints)
    segments = [seg("Rekchek", 40, 700, 110, 715), seg("55,00", 120, 700, 170, 715)]
    res = run(segments, {"currency": "CZK"})
    assert res["currency"].status == Status.NOT_FOUND


def test_compact_value_matches_glued_amount_symbol():
    segments = [seg("Celkem", 40, 700, 100, 715), seg("1900,00Kč", 120, 700, 200, 715)]
    res = run(segments, {"currency": "CZK"})
    assert res["currency"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_pattern_grounds_prefixless_id_print():
    # real-corpus case: LLM returns 'IE6356477S', page prints 'VAT No
    # 6356477S' — the schema-declared pattern says the alpha prefix is
    # optional, so the prefix-less print IS the value
    segments = [seg("VAT No", 40, 700, 90, 715), seg("6356477S", 100, 700, 180, 715)]
    res = run(segments, {"vat": "IE6356477S"},
              {"vat": {"type": "id", "pattern": r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"}})
    assert res["vat"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert res["vat"].evidence == "6356477S"


def test_pattern_is_opt_in():
    segments = [seg("VAT No", 40, 700, 90, 715), seg("6356477S", 100, 700, 180, 715)]
    res = run(segments, {"vat": "IE6356477S"}, {"vat": {"type": "id"}})
    assert res["vat"].status == Status.NOT_FOUND


def test_vat_field_names_get_pattern_hint():
    # enrich_spec: vat-family names carry the pattern automatically
    segments = [seg("DIC", 40, 700, 70, 715), seg("32322654", 100, 700, 180, 715)]
    res = run(segments, {"supplierVatId": "DK32322654"})
    assert res["supplierVatId"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_pattern_never_adopts_unrelated_numbers():
    segments = [seg("Tel", 40, 700, 70, 715), seg("420777123456", 100, 700, 200, 715)]
    res = run(segments, {"vat": "IE6356477S"},
              {"vat": {"type": "id", "pattern": r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"}})
    assert res["vat"].status == Status.NOT_FOUND


def test_collision_reassignment_recomputes_status():
    # two fields with equal-looking values collide on one box; the mover gets
    # its next candidate — if that candidate is fuzzy, the status must say so
    segments = [
        seg("Total", 40, 700, 90, 715), seg("100,00", 120, 700, 180, 715),
        seg("Paid", 40, 730, 80, 745), seg("1OO,OO", 120, 730, 180, 745),  # OCR-garbled twin
    ]
    res = run(segments, {"total": "100,00", "paid": "100,01"},
              {"total": {"type": "number"}, "paid": {"type": "number"}})
    for f in res.values():
        if f.bbox is None:
            continue
        # whatever box a field ends on, a non-exact candidate never keeps
        # a verified badge
        exact_evidence = f.evidence and canon_or_digits_match(f.value, f.evidence)
        if f.status == Status.VERIFIED:
            assert exact_evidence, f"{f.name}: verified on {f.evidence!r}"


def canon_or_digits_match(value, evidence) -> bool:
    from paperpin.align.matchers import value_number_set
    doc = value_number_set(evidence)
    want = value_number_set(value)
    return bool(doc and want and {abs(d) for d in doc} & {abs(w) for w in want})


def test_reassigned_field_reports_its_actual_candidate():
    from paperpin.align.aligner import _resolve_shared_instances
    from paperpin.types import Candidate, FieldResult
    box = (0.1, 0.1, 0.3, 0.15)
    alt_box = (0.5, 0.5, 0.7, 0.55)
    a = FieldResult(name="a", value="42", status=Status.VERIFIED, confidence=1.0,
                    page=0, bbox=box, evidence="42",
                    candidates=[Candidate(page=0, bbox=box, score=1.0, evidence="42", exact=True)])
    b = FieldResult(name="b", value="43", status=Status.VERIFIED, confidence=1.0,
                    page=0, bbox=box, evidence="42",
                    candidates=[
                        Candidate(page=0, bbox=box, score=1.0, evidence="42", exact=True),
                        Candidate(page=0, bbox=alt_box, score=0.85, evidence="4E", exact=False)])
    _resolve_shared_instances({"a": a, "b": b})
    assert b.bbox == alt_box
    assert b.status == Status.LOW_CONFIDENCE  # non-exact candidate, honest badge
    assert b.confidence <= 0.85


def test_pattern_never_verifies_a_digit_subset():
    # round-2 review: 'LV40103567891' came back VERIFIED on an unrelated
    # order number '4010356' — a print that drops DIGITS is a different
    # number; only the alpha prefix may go missing
    segments = [seg("Objednavka Nr.", 40, 100, 160, 115),
                seg("4010356", 170, 100, 240, 115)]
    res = run(segments, {"vat": "LV40103567891"},
              {"vat": {"type": "id", "pattern": r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"}})
    assert res["vat"].status == Status.NOT_FOUND


def test_damaged_full_print_beats_truncated_pattern_hit():
    # OCR read O for 0: the fuzzy whole-value hit must win over a partial
    # pattern hit, and the status must say fuzzy
    segments = [seg("PVN reg.nr.", 40, 100, 140, 115),
                seg("LV4O103567891", 150, 100, 300, 115)]
    res = run(segments, {"vat": "LV40103567891"},
              {"vat": {"type": "id", "pattern": r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"}})
    assert res["vat"].status == Status.LOW_CONFIDENCE
    assert "4o10356" in res["vat"].evidence.lower()


def test_short_id_never_matches_inside_a_longer_number():
    segments = [seg("Objednavka", 40, 100, 130, 115),
                seg("20241231-77", 140, 100, 250, 115)]
    res = run(segments, {"year": "2024"}, {"year": {"type": "id"}})
    assert res["year"].status == Status.NOT_FOUND


def test_short_id_never_matches_inside_a_word():
    segments = [seg("Rekchek", 40, 100, 110, 115)]
    res = run(segments, {"code": "KC"}, {"code": {"type": "id"}})
    assert res["code"].status == Status.NOT_FOUND


def test_affinity_bind_recomputes_status_from_the_pinned_candidate():
    from paperpin.align.aligner import _bind_affinities
    from paperpin.types import Candidate, FieldResult, FieldSpec, Status
    target = FieldResult(name="total", value="100", status=Status.VERIFIED,
                         confidence=1.0, page=0, bbox=(0.5, 0.50, 0.6, 0.52))
    exact_c = Candidate(page=0, bbox=(0.1, 0.9, 0.2, 0.92), score=1.0,
                        evidence="EUR", exact=True)
    fuzzy_c = Candidate(page=0, bbox=(0.7, 0.50, 0.8, 0.52), score=0.97,
                        evidence="EUR,", exact=False)
    cur = FieldResult(name="currency", value="EUR", status=Status.VERIFIED,
                      confidence=1.0, page=0, bbox=exact_c.bbox,
                      evidence="EUR", candidates=[exact_c, fuzzy_c])
    specs = {"currency": FieldSpec(name="currency", affinity=["total"]),
             "total": FieldSpec(name="total")}
    _bind_affinities({"currency": cur, "total": target}, specs)
    if cur.bbox == fuzzy_c.bbox:  # bound to the fuzzy on-line candidate
        assert cur.status == Status.LOW_CONFIDENCE
        assert cur.confidence <= fuzzy_c.score


def test_alias_prints_collapse_as_the_same_value():
    # round-2: canon('€') == '' != canon('EUR') defeated the equal-value
    # collapse and shipped AMBIGUOUS whenever a doc printed both forms
    segments = [seg("Total 1200,00", 40, 100, 180, 115), seg("EUR", 190, 100, 230, 115),
                seg("Summe 1200,00", 40, 700, 180, 715), seg("€", 190, 700, 210, 715)]
    res = run(segments, {"currency": "EUR"})
    assert res["currency"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_digit_id_tolerates_letter_prefix_run():
    # IČO '45357366' printing only inside 'CZ45357366' is the id with its
    # country prefix — a letter neighbor extends the label, not the number
    segments = [seg("DIC:", 40, 100, 80, 115), seg("CZ45357366", 90, 100, 200, 115)]
    res = run(segments, {"company_id": "45357366"}, {"company_id": {"type": "id"}})
    assert res["company_id"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_digit_id_survives_ocr_space_and_slash_splits():
    # '1 00018615' and '16611/2025' are the id's own print split by OCR/
    # formatting; nothing digit-extends the runs, so they stay matchable
    segments = [seg("doklad c:", 40, 100, 120, 115),
                seg("1 00018615", 130, 100, 240, 115)]
    res = run(segments, {"vs": "100018615"}, {"vs": {"type": "id"}})
    assert res["vs"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)

    segments2 = [seg("Faktura c.:", 40, 100, 130, 115),
                 seg("16611/2025", 140, 100, 240, 115)]
    res2 = run(segments2, {"vs": "166112025"}, {"vs": {"type": "id"}})
    assert res2["vs"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)


def test_short_number_without_anchor_never_verifies():
    # E-22 lock: mutation testing showed the guard could be deleted with the
    # whole suite green — this run has NO anchor word anywhere, so the guard
    # is the only thing between a bare page number and a verified qty
    segments = [seg("Poznamka rok", 40, 100, 150, 115),
                seg("Strana 2", 40, 760, 110, 775)]
    res = run(segments, {"qty": 2}, {"qty": {"type": "number"}})
    q = res["qty"]
    assert q.status == Status.LOW_CONFIDENCE
    assert q.confidence <= 0.6
    assert any("short value" in n for n in q.notes)


def test_anchor_bonus_outweighs_topmost_tiebreak():
    # §6.5 lock: '100,00' prints twice; only the label next to the lower
    # print says which one is the total. With the anchor bonus zeroed the
    # pin silently reverts to topmost-wins.
    segments = [seg("Cena", 40, 100, 90, 115),
                seg("100,00", 140, 100, 200, 115),
                seg("Celkem k uhrade", 40, 600, 180, 615),
                seg("100,00", 240, 600, 300, 615)]
    res = run(segments, {"total": "100,00"})  # infer_spec attaches anchor hints
    t = res["total"]
    assert t.anchor is not None
    assert t.bbox[1] > 0.5, "anchor bonus lost to the topmost tiebreak"


def test_short_id_never_matches_as_suffix_of_a_longer_number():
    # leading-neighbor twin of the prefix case above: '2024' at the END of a
    # longer digit run is a different number
    segments = [seg("Datum", 40, 100, 90, 115),
                seg("13122024", 140, 100, 230, 115)]
    res = run(segments, {"year": "2024"}, {"year": {"type": "id"}})
    assert res["year"].status == Status.NOT_FOUND


def test_anchor_never_matches_inside_a_word():
    # F4 (round-3): 'Dodavatel' contains 'vat' — a raw substring anchor
    # endorses a street number as a VAT field on every SK/CZ document and
    # disarms the E-22 guard. Short anchors must sit on word boundaries.
    segments = [seg("Danovy doklad c. 2026001", 40, 40, 260, 55),
                seg("Dodavatel: ACME s.r.o., Hlavna 20", 40, 100, 340, 115),
                seg("81102 Bratislava", 40, 140, 190, 155),
                seg("Celkom k uhrade 240,00 EUR", 40, 600, 280, 615)]
    res = run(segments, {"vat_rate": 20})
    v = res["vat_rate"]
    assert v.status != Status.VERIFIED, (v.anchor, v.notes)
    assert v.anchor != "vat"


def test_short_anchor_still_matches_as_a_word():
    segments = [seg("DPH 21 %", 40, 100, 130, 115)]
    res = run(segments, {"vat_rate": 21})
    v = res["vat_rate"]
    assert v.anchor is not None
    assert v.status == Status.VERIFIED


def test_pattern_fallback_never_matches_a_digit_extension():
    # F1 (round-3): the find_all guard rejects '12345678' inside
    # '123456789', then the pattern fallback's containment test re-accepted
    # exactly that span — a DIFFERENT id verified at 1.00
    segments = [seg("Dodavatel ACME s.r.o.", 40, 100, 240, 115),
                seg("IC DPH 123456789", 40, 140, 200, 155)]
    res = run(segments, {"supplier_vat_number": "12345678"})
    assert res["supplier_vat_number"].status != Status.VERIFIED


def test_id_with_dropped_country_prefix_still_matches():
    # the legit case the pattern fallback exists for: the model drops the
    # alpha prefix the print carries
    segments = [seg("IC DPH CZ12345678", 40, 140, 210, 155)]
    res = run(segments, {"supplier_vat_number": "12345678"})
    fr = res["supplier_vat_number"]
    assert fr.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert fr.bbox is not None


def test_lettered_id_never_matches_as_prefix_of_a_longer_id():
    # F2 (round-3): ids longer than 4 chars with letters skipped the
    # run-infix guard entirely — a model that dropped four digits got its
    # truncated id verified at 1.00 on the full print
    segments = [seg("Faktura c. FV2025001234", 40, 40, 260, 55),
                seg("Datum: 01.03.2026", 40, 80, 190, 95)]
    res = run(segments, {"invoice_number": "FV2025001"},
              {"invoice_number": {"type": "id"}})
    assert res["invoice_number"].status == Status.NOT_FOUND


def test_block_wrong_house_number_never_verifies():
    # F7 (round-3): single-char tokens were dropped from the numerator AND
    # the denominator, so 'Hlavna 5' scored a perfect overlap against a
    # printed 'Hlavna 9' — the one distinguishing token never participated
    segments = [seg("Dodavatel: ACME s.r.o.", 40, 100, 240, 115),
                seg("Hlavna 9", 40, 120, 120, 135),
                seg("81102 Bratislava", 40, 140, 190, 155)]
    res = run(segments, {"supplier_address": "Hlavna 5, Bratislava"},
              {"supplier_address": {"type": "block"}})
    assert res["supplier_address"].status != Status.VERIFIED


def test_block_right_house_number_still_verifies():
    segments = [seg("Dodavatel: ACME s.r.o.", 40, 100, 240, 115),
                seg("Hlavna 5", 40, 120, 120, 135),
                seg("81102 Bratislava", 40, 140, 190, 155)]
    res = run(segments, {"supplier_address": "Hlavna 5 Bratislava"},
              {"supplier_address": {"type": "block"}})
    fr = res["supplier_address"]
    assert fr.status == Status.VERIFIED, (fr.status, fr.notes)
    assert "5" in (fr.evidence or "")


def test_digit_id_never_verifies_on_a_date_print():
    # F3 (round-3): canon erases separators, so invoice_number '20260315'
    # matched the printed date '2026-03-15' exactly — the hallucination
    # flag is the correct outcome, low_confidence the acceptable one
    segments = [seg("Danovy doklad", 40, 100, 160, 115),
                seg("Datum vystavenia: 2026-03-15", 40, 140, 280, 155)]
    res = run(segments, {"invoice_number": "20260315"},
              {"invoice_number": {"type": "id"}})
    assert res["invoice_number"].status != Status.VERIFIED


def test_digit_id_never_verifies_on_a_decimal_amount_print():
    segments = [seg("Polozka A", 40, 100, 130, 115),
                seg("Cena za kus 12,34", 40, 140, 200, 155)]
    res = run(segments, {"invoice_number": "1234"},
              {"invoice_number": {"type": "id"}})
    assert res["invoice_number"].status != Status.VERIFIED


def test_digit_id_with_its_own_separators_still_verifies():
    # ids genuinely print with separators ('16611/2025') and OCR splits
    # digit runs ('1 00018615') — those must keep matching exactly
    segments = [seg("Faktura c.: 16611/2025", 40, 100, 240, 115)]
    res = run(segments, {"vs": "166112025"}, {"vs": {"type": "id"}})
    assert res["vs"].status == Status.VERIFIED


def test_disagreeing_fields_on_one_box_are_never_both_clean_verified():
    # F10 (round-3): two fields, same box, different values, no alternative
    # candidate — resolver silently did nothing and both stayed verified
    # with empty notes
    from paperpin.align.aligner import _resolve_shared_instances
    from paperpin.types import FieldResult
    a = FieldResult(name="invoice_number", value="20260315",
                    status=Status.VERIFIED, confidence=1.0, page=0,
                    bbox=(0.2, 0.06, 0.31, 0.08), evidence="2026-03-15")
    b = FieldResult(name="invoice_date", value="2026-03-15",
                    status=Status.VERIFIED, confidence=1.0, page=0,
                    bbox=(0.2, 0.06, 0.31, 0.08), evidence="2026-03-15")
    _resolve_shared_instances({"invoice_number": a, "invoice_date": b})
    assert not (a.status == Status.VERIFIED and not a.notes
                and b.status == Status.VERIFIED and not b.notes), \
        "silent disagreeing share survived"


def test_numerically_equal_variants_share_without_demotion():
    # '90.00' vs '90,00' are the same value in different spellings — the
    # raw-string comparison used to route them into the disagree path
    from paperpin.align.aligner import _resolve_shared_instances
    from paperpin.types import FieldResult
    a = FieldResult(name="unit_price", value="90.00", status=Status.VERIFIED,
                    confidence=1.0, page=0, bbox=(0.3, 0.1, 0.4, 0.12),
                    evidence="90,00")
    b = FieldResult(name="amount", value="90,00", status=Status.VERIFIED,
                    confidence=1.0, page=0, bbox=(0.3, 0.1, 0.4, 0.12),
                    evidence="90,00")
    _resolve_shared_instances({"unit_price": a, "amount": b})
    assert a.status == Status.VERIFIED and b.status == Status.VERIFIED
    assert any("shares its box" in n for n in a.notes)


def test_cross_column_fused_number_needs_a_label():
    # F11 (round-3): '24' (qty) and '158,97' (price) sit a column apart —
    # their fusion into 24158.97 is a plausible reading, not a certain one
    segments = [seg("Kabel HDMI 2m", 40, 100, 150, 115),
                seg("24", 210, 100, 230, 115),
                seg("158,97", 320, 100, 375, 115)]
    res = run(segments, {"total": "24158.97"}, {"total": {"type": "number"}})
    fr = res["total"]
    assert fr.status != Status.VERIFIED
    assert any("fused" in n for n in fr.notes)


def test_lettered_id_printed_with_separators_still_matches():
    # corpus regression 2026-08-21: extending the no-stitch rule to every id
    # broke ids whose PRINT carries separators the value omits — company
    # numbers ('FN 82573g') and every grouped IBAN
    segments = [seg("Handelsgericht Wien FN 82573g", 40, 100, 300, 115)]
    res = run(segments, {"supplier_company_id": "FN82573g"},
              {"supplier_company_id": {"type": "id"}})
    assert res["supplier_company_id"].status == Status.VERIFIED

    iban_segs = [seg("IBAN SK73 1100 0000 0026 2902 8990", 40, 200, 380, 215)]
    res2 = run(iban_segs, {"iban": "SK7311000000002629028990"},
               {"iban": {"type": "id"}})
    assert res2["iban"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
