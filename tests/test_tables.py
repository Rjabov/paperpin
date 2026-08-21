"""Line-item assignment on hand-built segments — duplicated rows must ALL pin.

Real-doc regression (METRO scan, 2026-08-18): identical products repeat as
separate rows; the old greedy claim dropped every duplicate as not_found.
"""
from paperpin.align.rows import build_rows
from paperpin.align.tables import align_table
from paperpin.types import FieldSpec, FieldType, Segment, Status

PAGE = {0: (600.0, 800.0)}

SPEC = FieldSpec(name="line_items", type=FieldType.TABLE, columns={
    "description": FieldSpec(name="description", type=FieldType.TEXT),
    "qty": FieldSpec(name="qty", type=FieldType.NUMBER),
    "unit_price": FieldSpec(name="unit_price", type=FieldType.NUMBER),
    "amount": FieldSpec(name="amount", type=FieldType.NUMBER),
})


def table_rows(lines, y0=200, dy=24):
    segments = []
    for i, cells in enumerate(lines):
        x = 40
        for text in cells:
            w = max(20, 8 * len(text))
            segments.append(Segment(text=text, x0=x, top=y0 + i * dy,
                                    x1=x + w, bottom=y0 + i * dy + 14,
                                    conf=1.0, page=0))
            x += w + 14
    return build_rows(segments)


def item(desc, qty, unit, amount):
    return {"description": desc, "qty": qty, "unit_price": unit, "amount": amount}


def test_exact_duplicate_rows_both_pinned_in_document_order():
    rows = table_rows([
        ["GRANA", "PADANO", "1,160", "18,990", "22,03"],
        ["GRANA", "PADANO", "1,160", "18,990", "22,03"],
    ])
    items = [item("GRANA PADANO", "1,160", "18,990", "22,03"),
             item("GRANA PADANO", "1,160", "18,990", "22,03")]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    a0, a1 = res["line_items[0].amount"], res["line_items[1].amount"]
    assert a0.status != Status.NOT_FOUND, "first duplicate row dropped"
    assert a1.status != Status.NOT_FOUND, "second duplicate row dropped"
    assert a0.bbox is not None and a1.bbox is not None
    assert a0.bbox != a1.bbox, "duplicates pinned to the same doc row"
    assert a0.bbox[1] < a1.bbox[1], "duplicates must map top-to-bottom in order"


def test_five_near_duplicates_each_pin_their_own_row():
    qa = [("10,102", "45,36"), ("10,931", "49,08"), ("10,554", "47,39"),
          ("10,541", "47,33"), ("10,044", "45,10")]
    rows = table_rows([["KURACIE", "REZNE", q, "4,490", a] for q, a in qa])
    items = [item("KURACIE REZNE", q, "4,490", a) for q, a in qa]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    tops = []
    for i in range(len(qa)):
        fr = res[f"line_items[{i}].amount"]
        assert fr.status != Status.NOT_FOUND, f"row {i} dropped"
        assert fr.bbox is not None
        tops.append(fr.bbox[1])
    assert tops == sorted(tops), "rows must keep document order"
    assert len(set(tops)) == len(tops), "every row needs its own doc row"


def test_single_item_on_tied_rows_prefers_first():
    # two equally-scoring doc rows — the earlier one must win (stable behavior)
    rows = table_rows([
        ["Alpha", "10,00"],
        ["Alpha", "10,00"],
    ])
    items = [{"description": "Alpha", "amount": "10,00"}]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    fr = res["line_items[0].amount"]
    assert fr.bbox is not None
    assert fr.bbox[1] < 0.27, f"tie must resolve to the FIRST matching row, got y={fr.bbox[1]}"


def test_weak_single_short_hit_does_not_claim_a_row():
    # an item whose only "match" is a bare short number somewhere on the page
    # must stay not_found — a located-but-wrong pin is worse than honest absence
    rows = table_rows([
        ["Seite", "1"],
        ["Alpha", "10,00"],
    ])
    items = [{"description": "ZZZZZZ", "qty": "1", "amount": "77,77"}]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    assert res["line_items[0].qty"].status == Status.NOT_FOUND
    assert res["line_items[0].description"].status == Status.NOT_FOUND


def test_unique_rows_still_pin():
    rows = table_rows([
        ["Mattoni", "0.7l", "6", "17,97", "107,82"],
        ["Pepsi", "plech", "24", "11,87", "284,88"],
        ["Fidorka", "modra", "30", "14,50", "435,00"],
    ])
    items = [item("Mattoni 0.7l", "6", "17,97", "107,82"),
             item("Pepsi plech", "24", "11,87", "284,88"),
             item("Fidorka modra", "30", "14,50", "435,00")]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    for i in range(3):
        assert res[f"line_items[{i}].amount"].status != Status.NOT_FOUND


def test_wrapped_description_matches_across_rows():
    # real-doc regression (wgo.pdf): the description column wraps — code +
    # name-start share the row with the numbers, the name's tail lands on the
    # next printed line. The glued extraction value must still pin.
    rows = table_rows([
        ["WD4810:Well", "Done", "2ks", "14,355", "35,31"],
        ["Odmastovac", "za", "studena", "5l"],
    ])
    items = [{"description": "WD4810:Well Done Odmastovac za studena 5l",
              "qty": "2ks", "amount": "35,31"}]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    fr = res["line_items[0].description"]
    assert fr.status != Status.NOT_FOUND, fr.notes
    assert fr.bbox is not None


def aligned_rows(lines, y0=200, dy=24):
    """Rows with EXPLICIT per-cell x positions: lines = [[(text, x), ...], ...].
    Column-band tests need header words sitting above their data cells."""
    segments = []
    for i, cells in enumerate(lines):
        for text, x in cells:
            w = max(20, 8 * len(text))
            segments.append(Segment(text=text, x0=x, top=y0 + i * dy,
                                    x1=x + w, bottom=y0 + i * dy + 14,
                                    conf=1.0, page=0))
    return build_rows(segments)


def test_qty1_twin_pins_each_price_to_its_own_column():
    # the qty=1 disease: unit_price == amount, both printed; the cell matcher
    # must use the header's column x-bands, not the first matching span
    rows = aligned_rows([
        [("Popis", 40), ("Množství", 200), ("Cena za MJ", 300), ("Celkem", 450)],
        [("Widget", 40), ("1", 200), ("7 000", 300), ("7 000", 450)],
    ])
    items = [item("Widget", "1", "7 000", "7 000")]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    up, am = res["line_items[0].unit_price"], res["line_items[0].amount"]
    assert up.bbox is not None and am.bbox is not None
    assert up.bbox != am.bbox, "twin cells pinned to the same span"
    assert up.bbox[0] < am.bbox[0], "unit price must sit left of amount"
    assert abs(up.bbox[0] * 600 - 300) < 80, f"unit_price off its column: {up.bbox}"
    assert abs(am.bbox[0] * 600 - 450) < 80, f"amount off its column: {am.bbox}"


def test_equal_twin_cells_never_share_a_span_without_header():
    # no header row: exclusivity + printed order still separates the twins
    rows = aligned_rows([
        [("Widget", 40), ("1", 200), ("22,03", 300), ("22,03", 450)],
    ])
    items = [item("Widget", "1", "22,03", "22,03")]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    up, am = res["line_items[0].unit_price"], res["line_items[0].amount"]
    assert up.bbox is not None and am.bbox is not None
    assert up.bbox != am.bbox, "twin cells pinned to the same span"
    assert up.bbox[0] < am.bbox[0], "printed order: unit price left, amount right"


def test_distinct_cells_unchanged_by_column_bands():
    # regression guard: distinct values pin exactly as before
    rows = aligned_rows([
        [("Popis", 40), ("Množství", 200), ("Cena za MJ", 300), ("Celkem", 450)],
        [("Widget", 40), ("2", 200), ("10,00", 300), ("20,00", 450)],
    ])
    items = [item("Widget", "2", "10,00", "20,00")]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    for col, x in (("unit_price", 300), ("amount", 450)):
        fr = res[f"line_items[0].{col}"]
        assert fr.bbox is not None
        assert abs(fr.bbox[0] * 600 - x) < 80


def test_long_wrapped_description_reaches_beyond_one_neighbor_row():
    # image(17) class: the model glues a description that wraps over SEVERAL
    # printed lines ('0185:LA - 90 Primitivo ... basic, Puglia ... Šarže ...');
    # a one-row merge window can never cover it
    rows = table_rows([
        ["0185:LA-90Primitivo", "Rosso0,75L"],
        ["basic,", "Puglia", "cervene"],
        ["Sarze", "25035"],
        ["2", "189,00", "378,00"],
    ])
    items = [{"description":
              "0185:LA - 90 Primitivo Rosso 0,75L, basic, Puglia cervene Sarze 25035",
              "qty": "2", "unit_price": "189,00", "amount": "378,00"}]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    fr = res["line_items[0].description"]
    assert fr.status != Status.NOT_FOUND, fr.notes
    assert fr.bbox is not None


def test_cells_found_in_the_items_own_territory():
    # METRO layout: one item spans several printed lines — category header,
    # description line, EAN line, numbers line. Assignment lands on ONE of
    # them; the other cells live between the neighboring items' rows and must
    # be found there, not declared missing.
    rows = table_rows([
        ["KOSMETIKA", "DROGERIE"],
        ["MPRO", "TEK.", "MYDLO", "500ml"],
        ["4337182200376"],
        ["1", "27,90", "27,90"],
        ["UKLIDOVE", "PROSTREDKY"],
        ["ARO", "UBR.1V30X30CM", "100KS"],
        ["8595562906034"],
        ["5", "35,00", "175,00"],
    ])
    spec = FieldSpec(name="line_items", type=FieldType.TABLE, columns={
        "description": FieldSpec(name="description", type=FieldType.TEXT),
        "ean": FieldSpec(name="ean", type=FieldType.ID),
        "qty": FieldSpec(name="qty", type=FieldType.NUMBER),
        "unit_price": FieldSpec(name="unit_price", type=FieldType.NUMBER),
        "amount": FieldSpec(name="amount", type=FieldType.NUMBER),
    })
    items = [{"description": "MPRO TEK. MYDLO 500ml", "ean": "4337182200376",
              "qty": "1", "unit_price": "27,90", "amount": "27,90"},
             {"description": "ARO UBR.1V30X30CM 100KS", "ean": "8595562906034",
              "qty": "5", "unit_price": "35,00", "amount": "175,00"}]
    res = align_table("line_items", spec, items, rows, PAGE)
    for i in (0, 1):
        for col in ("description", "ean", "amount"):
            fr = res[f"line_items[{i}].{col}"]
            assert fr.status != Status.NOT_FOUND, f"[{i}].{col}: {fr.notes}"
            assert fr.bbox is not None
    # territory discipline: item 0's EAN must be the first EAN print, item 1's the second
    assert res["line_items[0].ean"].bbox[1] < res["line_items[1].ean"].bbox[1]


def test_cell_specs_keep_all_declarations():
    # pattern/aliases/proof must reach table cells — the hand-copied spec
    # used to drop them (a vat id inside a line item lost prefix tolerance)
    from paperpin.align.rows import build_rows
    from paperpin.align.tables import align_table
    from paperpin.types import FieldSpec, FieldType, Segment, Status

    def seg(text, x0, top, x1, bottom):
        return Segment(text=text, x0=x0, top=top, x1=x1, bottom=bottom, conf=1.0)

    segments = [
        seg("supplier", 40, 100, 120, 115), seg("vat", 400, 100, 460, 115),
        seg("Acme", 40, 130, 100, 145), seg("6356477S", 400, 130, 480, 145),
    ]
    spec = FieldSpec(name="parties", type=FieldType.TABLE, columns={
        "supplier": FieldSpec(name="supplier", type=FieldType.TEXT),
        "vat": FieldSpec(name="vat", type=FieldType.ID,
                         pattern=r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"),
    })
    rows = build_rows(segments)
    res = align_table("parties", spec, [{"supplier": "Acme", "vat": "IE6356477S"}],
                      rows, {0: (600.0, 800.0)})
    assert res["parties[0].vat"].status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    assert res["parties[0].vat"].evidence == "6356477S"


def test_bare_short_qty_never_verifies_without_band_or_anchor():
    # round-2: qty=1 verified on the '1' inside 'Produkt A1'
    from paperpin.align.rows import build_rows
    from paperpin.align.tables import align_table
    from paperpin.types import FieldSpec, FieldType, Segment, Status

    def seg(text, x0, top, x1, bottom):
        return Segment(text=text, x0=x0, top=top, x1=x1, bottom=bottom, conf=1.0)

    segments = [seg("Produkt A1", 40, 100, 140, 115),
                seg("1", 200, 100, 208, 115),
                seg("10,00", 260, 100, 310, 115),
                seg("10,00", 360, 100, 410, 115)]
    spec = FieldSpec(name="items", type=FieldType.TABLE, columns={
        "description": FieldSpec(name="description", type=FieldType.TEXT),
        "qty": FieldSpec(name="qty", type=FieldType.NUMBER),
        "unit_price": FieldSpec(name="unit_price", type=FieldType.NUMBER),
        "amount": FieldSpec(name="amount", type=FieldType.NUMBER)})
    res = align_table("items", spec,
                      [{"description": "Produkt A1", "qty": 1,
                        "unit_price": 10.0, "amount": 10.0}],
                      build_rows(segments), {0: (600.0, 800.0)})
    q = res["items[0].qty"]
    # located is fine — but a bare '1' with no band/anchor never wears verified
    assert q.status != Status.VERIFIED or q.evidence != "1" or q.bbox[0] > 0.3


def test_short_only_cells_survive_the_pair_prefilter():
    # dishboard regression 2026-08-20: taxDetail rows asserting only the
    # 2-char rate (base/tax null) lost their row — a <3-char canon has no
    # trigram, so the prefilter's fallback gram could never intersect the
    # row's trigram set and every doc row was pruned before matching.
    spec = FieldSpec(name="taxDetail", type=FieldType.TABLE, columns={
        "base": FieldSpec(name="base", type=FieldType.NUMBER),
        "rate": FieldSpec(name="rate", type=FieldType.PERCENT),
        "tax": FieldSpec(name="tax", type=FieldType.NUMBER)})
    rows = table_rows([
        ["Sazba", "Zaklad", "Dan"],
        ["21 %", "0,00", "0,00"],
        ["12 %", "51,79", "6,21"],
    ])
    items = [{"base": "0", "rate": "21", "tax": "0"},
             {"base": "51.79", "rate": "12", "tax": "6.21"}]
    res = align_table("taxDetail", spec, items, rows, PAGE)
    for name in ("taxDetail[0].rate", "taxDetail[0].base", "taxDetail[0].tax"):
        fr = res[name]
        assert fr.status not in (Status.NOT_FOUND, Status.NOT_PRESENT), \
            f"{name} pruned by trigram prefilter ({fr.notes})"
        assert fr.bbox is not None
    assert res["taxDetail[1].rate"].status not in (Status.NOT_FOUND,
                                                   Status.NOT_PRESENT)


def test_first_items_territory_never_reaches_the_letterhead():
    # F5 (round-3): the first item's territory started at doc row 0, so a
    # missing cell was hunted through the page header — a phone-number
    # fragment '905' three rows above the table became a verified
    # unit_price at 1.00
    rows = table_rows([
        ["ACME s.r.o.  tel. +421 905 123 456"],
        ["Hlavna 15, 81102 Bratislava"],
        ["Popis", "Mnozstvo", "Cena", "Suma"],
        ["Konzultacia", "2", "240,00"],
        ["Doprava", "1", "10,00", "10,00"],
    ])
    res = align_table("line_items", SPEC, [
        item("Konzultacia", "2", "905", "240,00"),
        item("Doprava", "1", "10,00", "10,00")], rows, PAGE)
    up = res["line_items[0].unit_price"]
    assert up.status == Status.NOT_FOUND, (up.status, up.bbox, up.evidence)


def test_failed_row_arithmetic_flags_every_located_operand():
    # F5b: the ⚠ landed only on the proof target — the bogus operand kept a
    # clean verified while the correctly-located amount wore the warning
    from paperpin.schemas import resolve_schema
    proof_spec = resolve_schema({"line_items": {"type": "table", "columns": {
        "description": {"type": "text"}, "qty": {"type": "number"},
        "unit_price": {"type": "number"}, "amount": {"type": "number"},
    }}})["line_items"]
    rows = table_rows([
        ["Popis", "Mnozstvo", "Cena", "Suma"],
        ["Konzultacia", "2", "905,00", "240,00"],
    ])
    res = align_table("line_items", proof_spec,
                      [item("Konzultacia", "2", "905,00", "240,00")],
                      rows, PAGE)
    flagged = [n for n in ("line_items[0].qty", "line_items[0].unit_price",
                           "line_items[0].amount")
               if any("arithmetic" in x and "⚠" in x for x in res[n].notes)]
    assert "line_items[0].amount" in flagged
    assert "line_items[0].unit_price" in flagged, \
        [res["line_items[0].unit_price"].notes]


def test_wrapped_name_box_stays_in_its_column():
    # Corpus regression (2026-08-21): a two-line item name merged through the
    # full-row view got a first-to-last bbox spanning every other column.
    rows = table_rows([
        ["nduja", "di", "spilinga", "cca", "400g", "449403", "2,050kg",
         "395,00", "12%", "809,75"],
        ["pikantni", "roztiratelna", "veprova", "klobasa"],
        ["melasa", "450g", "sklo", "445002", "5,000ks", "260,00", "12%", "1.300,00"],
    ])
    items = [{"description": "nduja di spilinga cca 400g pikantni "
                             "roztiratelna veprova klobasa",
              "qty": "2,050", "unit_price": "395,00", "amount": "809,75"},
             {"description": "melasa 450g sklo", "qty": "5,000",
              "unit_price": "260,00", "amount": "1.300,00"}]
    res = align_table("line_items", SPEC, items, rows, PAGE)
    d = res["line_items[0].description"]
    assert d.status in (Status.VERIFIED, Status.LOW_CONFIDENCE)
    # name words end well before the numeric columns (~x300/600); a box that
    # reaches the amount column proves the full-stretch bug
    assert d.bbox is not None and d.bbox[2] < 0.62, d.bbox


def test_quantity_string_matches_the_item_row_not_a_decoy():
    # Corpus regression (2026-08-21): '2,050' typed as TEXT missed the glued
    # '2,050kg' cell and a fuzzy window on a delivery-note line above won.
    from paperpin.api import _infer_table_spec
    rows = table_rows([
        ["Dodaci", "list", "2025 - 01011006,", "ze", "dne", "03.04.2025"],
        ["nduja", "di", "spilinga", "cca", "400g", "449403", "2,050kg",
         "395,00", "12%", "809,75"],
        ["melasa", "450g", "sklo", "445002", "5,000ks", "260,00", "12%", "1.300,00"],
    ])
    items = [{"item_name": "nduja di spilinga cca 400g", "quantity": "2,050",
              "unit_price": "395,00", "amount": "809,75"},
             {"item_name": "melasa 450g sklo", "quantity": "5,000",
              "unit_price": "260,00", "amount": "1.300,00"}]
    spec = _infer_table_spec("line_items", items)
    res = align_table("line_items", spec, items, rows, PAGE)
    q = res["line_items[0].quantity"]
    assert q.evidence == "2,050", (q.status, q.evidence)
    assert q.status == Status.VERIFIED
