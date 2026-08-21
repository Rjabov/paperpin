from paperpin.geometry.segmentize import _char_edges
from paperpin.types import FieldSpec, FieldType
from paperpin.verify.crop_reread import reread_agrees


def spec(t):
    return FieldSpec(name="f", type=t)


def test_reread_number_agrees_despite_ocr_noise():
    assert reread_agrees(spec(FieldType.NUMBER), "146,14", "Suma 146,14 EUR")
    assert reread_agrees(spec(FieldType.NUMBER), "146,14", "146.14")  # sep variant


def test_reread_number_disagrees_on_label_only():
    assert not reread_agrees(spec(FieldType.NUMBER), "32635093", "Company ID:")


def test_reread_id_substring_both_directions():
    assert reread_agrees(spec(FieldType.ID), "SK2022072646", "IC DPH: SK2022072646")
    # re-read crop may be tighter than the evidence
    assert reread_agrees(spec(FieldType.ID), "IC DPH: SK2022072646", "SK2022072646")


def test_reread_number_agrees_on_scrambled_segment_order():
    # skewed photo crops re-read "88,53" as two segments joined "53 88" —
    # same glyphs, wrong order, comma lost (real-doc regression IMG_9140)
    assert reread_agrees(spec(FieldType.NUMBER), "88,53", "53 88")
    assert reread_agrees(spec(FieldType.NUMBER), "24,60", "24 60")


def test_reread_number_still_rejects_different_number():
    assert not reread_agrees(spec(FieldType.NUMBER), "88,54", "53 88")


def test_reread_date_agrees_on_scrambled_fragments():
    assert reread_agrees(spec(FieldType.DATE), "13.09.2025", "2025 13 3.09")


def test_reread_text_agrees_on_reordered_words():
    # dense-scan crops re-read multi-word values in scrambled order
    # ("Svec, Martin" for "Martin Svec, s. r. o.") — same words, right pixels
    assert reread_agrees(spec(FieldType.TEXT), "Martin Svec, s. r. o.", "Svec, Martin")
    assert reread_agrees(spec(FieldType.TEXT), "Senecka cesta 1881", "cesta Senecka 1881")


def test_reread_text_single_stray_word_still_disagrees():
    assert not reread_agrees(spec(FieldType.TEXT), "Martin Svec, s. r. o.", "Dunajska")


def test_reread_text_diacritics_folded():
    assert reread_agrees(spec(FieldType.TEXT), "Bohéma Bar", "bohema bar")


def test_reread_text_tolerates_stray_glyph():
    # padded crops grab neighbors; a near-match must not downgrade text fields
    assert reread_agrees(spec(FieldType.TEXT), "PROVINO s.r.O", "PROVINOS s.r.o.")
    assert not reread_agrees(spec(FieldType.ID), "SK2022072646", "SK2022072647X")


def test_char_edges_weighted():
    # digits get more width than dots — the id slice must start past the label
    text = "ID:12345"
    edges = _char_edges(text, 0.0, 100.0)
    assert len(edges) == len(text) + 1
    assert edges[0] == 0.0 and abs(edges[-1] - 100.0) < 1e-9
    # ':' is narrow → the digit run begins left of uniform 3/8*100=37.5
    assert edges[3] < 37.5
    # monotonic
    assert all(b > a for a, b in zip(edges, edges[1:]))


def test_reread_number_agrees_on_clipped_tail():
    # real demotions: crop clipped the decimal tail — re-read digits are a
    # substantial prefix/subset of the evidence, that's confirmation not doubt
    assert reread_agrees(spec(FieldType.NUMBER), "68,70", "68")
    assert reread_agrees(spec(FieldType.NUMBER), "2097,51", "2097")
    assert reread_agrees(spec(FieldType.NUMBER), "2,400", ",400")


def test_reread_number_clipped_tail_still_rejects_wrong_digits():
    assert not reread_agrees(spec(FieldType.NUMBER), "68,70", "99")
    assert not reread_agrees(spec(FieldType.NUMBER), "68,70", "6")   # 1 digit proves nothing
    assert not reread_agrees(spec(FieldType.NUMBER), "123456", "12")  # <50% coverage


def test_reread_text_agrees_on_token_splits_and_fusions():
    # OCR splits/fuses words across re-reads — same glyphs, same pixels
    assert reread_agrees(spec(FieldType.TEXT), "NUDLICKY ADR.SEM", "NUDLICKY SEM ADR")
    assert reread_agrees(spec(FieldType.TEXT), "TSOVES VLJEM 500g", "TS OVES 500g JEM")


def test_reread_text_agrees_when_padding_grabs_neighbors():
    # padded crops grab neighbor tokens BY DESIGN — extra re-read tokens must
    # not veto agreement when the evidence itself is covered
    assert reread_agrees(spec(FieldType.TEXT), "Tesco Stores CR", "Tesco Stores a.s 100")


def test_reread_text_still_rejects_different_content():
    assert not reread_agrees(spec(FieldType.TEXT), "Celkem k uhrade", "Dodavatel firma")
    assert not reread_agrees(spec(FieldType.TEXT), "Alpha Beta Gamma", "Delta Alpha")


def _vf(name, value):
    from paperpin.types import FieldResult, Status
    return FieldResult(name=name, value=value, status=Status.VERIFIED,
                       confidence=1.0, page=0, bbox=(0.1, 0.1, 0.2, 0.12),
                       evidence=value)


class _SpyBackend:
    def __init__(self):
        self.calls = []

    def recognize(self, img):
        self.calls.append(1)
        return []


def _tiny_image():
    from PIL import Image
    return Image.new("RGB", (200, 200), "white")


def test_arithmetic_proved_fields_skip_the_crop_reread():
    from paperpin.types import FieldSpec
    # subtotal + vat = total holds -> all three carry an independent proof;
    # burning a crop re-read on them is pure noise risk + wasted CPU
    from paperpin.types import Status
    from paperpin.verify.verify import verify_results
    from paperpin.schemas import enrich_spec
    fields = {"subtotal": _vf("subtotal", "100,00"),
              "vat_amount": _vf("vat_amount", "21,00"),
              "total": _vf("total", "121,00")}
    specs = {n: enrich_spec(FieldSpec(name=n, type=FieldType.NUMBER)) for n in fields}
    backend = _SpyBackend()
    verify_results(fields, specs, rows=[], route_by_page={0: "ocr"},
                   page_image_provider=lambda p: _tiny_image(), ocr_backend=backend)
    assert backend.calls == [], "math-proved fields must not be re-cropped"
    assert all(f.status == Status.VERIFIED for f in fields.values())
    assert all(any("arithmetic passed" in n for n in f.notes) for f in fields.values())


def test_unproved_fields_still_get_the_crop_reread():
    from paperpin.types import FieldSpec
    from paperpin.verify.verify import verify_results
    from paperpin.schemas import enrich_spec
    fields = {"subtotal": _vf("subtotal", "100,00"),
              "vat_amount": _vf("vat_amount", "21,00"),
              "total": _vf("total", "999,99")}   # math broken -> no proof
    specs = {n: enrich_spec(FieldSpec(name=n, type=FieldType.NUMBER)) for n in fields}
    backend = _SpyBackend()
    verify_results(fields, specs, rows=[], route_by_page={0: "ocr"},
                   page_image_provider=lambda p: _tiny_image(), ocr_backend=backend)
    assert len(backend.calls) == 3, "unproved fields must still be verified by pixels"


def test_rescue_pins_value_found_only_by_targeted_reread():
    # the page OCR read the label row but not the damaged value region; the
    # targeted re-read recovers it -> low_confidence pin, honest note
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, FieldType, Segment, Status
    from paperpin.verify.rescue import rescue_not_founds

    label_row_segs = [Segment(text="IBAN", x0=40, top=300, x1=80, bottom=315, page=0)]
    rows = build_rows(label_row_segs)
    fr = FieldResult(name="iban", value="LV97HABA0001402047731",
                     status=Status.NOT_FOUND, confidence=0.0)

    class RescueBackend:
        def recognize(self, img):
            # pretend the high-res crop reads the IBAN below the label
            return [Segment(text="LV97HABA0001402047731",
                            x0=30, top=90, x1=520, bottom=130)]

    from PIL import Image
    n = rescue_not_founds({"iban": fr},
                          {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                             anchors=["iban"])},
                          rows, {0: (600.0, 800.0)}, {0: "ocr"},
                          lambda p: Image.new("RGB", (600, 800), "white"),
                          RescueBackend())
    assert n == 1
    assert fr.status == Status.LOW_CONFIDENCE
    assert fr.bbox is not None
    assert any("targeted high-resolution re-read" in x for x in fr.notes)


def test_rescue_leaves_hopeless_fields_honest():
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, FieldType, Segment, Status
    from paperpin.verify.rescue import rescue_not_founds

    rows = build_rows([Segment(text="IBAN", x0=40, top=300, x1=80, bottom=315, page=0)])
    fr = FieldResult(name="iban", value="LV97HABA0001402047731",
                     status=Status.NOT_FOUND, confidence=0.0)

    class BlindBackend:
        def recognize(self, img):
            return []

    from PIL import Image
    n = rescue_not_founds({"iban": fr},
                          {"iban": FieldSpec(name="iban", type=FieldType.ID)},
                          rows, {0: (600.0, 800.0)}, {0: "ocr"},
                          lambda p: Image.new("RGB", (600, 800), "white"),
                          BlindBackend())
    assert n == 0
    assert fr.status == Status.NOT_FOUND


def test_custom_domain_relation_proves_fields():
    # relations are schema declarations — a payslip proves gross = net + tax
    # with zero engine knowledge of payslips
    from paperpin.verify.arithmetic import run_arithmetic
    specs = {"gross": FieldSpec(name="gross", type=FieldType.NUMBER,
                                proof={"sum": ["net", "tax"]}),
             "net": FieldSpec(name="net", type=FieldType.NUMBER),
             "tax": FieldSpec(name="tax", type=FieldType.NUMBER)}
    notes = run_arithmetic({"gross": "1 210,00", "net": "1 000,00", "tax": "210,00"}, specs)
    assert all(any("arithmetic passed" in x for x in notes.get(f, []))
               for f in ("gross", "net", "tax"))
    notes_bad = run_arithmetic({"gross": "9 999,99", "net": "1 000,00", "tax": "210,00"}, specs)
    assert any("⚠" in x for x in notes_bad.get("gross", []))


def test_arithmetic_never_proves_hallucinated_operands():
    # round-2: relations ran over ASSERTED values, stamping
    # method='arithmetic' on not_found fields and exempting the located
    # one from its pixel check
    from paperpin.types import FieldResult, FieldSpec, Status
    from paperpin.verify.verify import verify_results

    results = {
        "total": FieldResult(name="total", value="120.00", status=Status.VERIFIED,
                             confidence=1.0, page=0, bbox=(0.1, 0.1, 0.2, 0.12),
                             evidence="120,00"),
        "subtotal": FieldResult(name="subtotal", value="100.00",
                                status=Status.NOT_FOUND, confidence=0.0),
        "vat_amount": FieldResult(name="vat_amount", value="20.00",
                                  status=Status.NOT_FOUND, confidence=0.0),
    }
    specs = {"total": FieldSpec(name="total", proof={"sum": ["subtotal", "vat_amount"]}),
             "subtotal": FieldSpec(name="subtotal"),
             "vat_amount": FieldSpec(name="vat_amount")}
    verify_results(results, specs, rows=[], route_by_page={0: "textlayer"})
    assert results["subtotal"].method != "arithmetic"
    assert not any("arithmetic passed" in n for n in results["subtotal"].notes)
    assert not any("arithmetic passed" in n for n in results["total"].notes)


def test_nl_vat_format():
    from paperpin.verify.checksums import vat_check
    ok, note = vat_check("NL001234567B01")   # 9 digits + B + 2
    assert ok is True
    bad, _ = vat_check("NL0012345678B01")    # 10 digits — invalid
    assert bad is False


def test_quote_check_tolerates_one_confusable():
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, Status
    from paperpin.verify.verify import _quote_check
    from paperpin.types import Segment

    printed = "Brivibas iela 12 dzivoklis 7 Riga LV1010 Latvija SIA Piegades"
    ocr = printed.replace("dzivoklis", "dzivokIis")  # one I-for-l confusion
    rows = build_rows([Segment(text=ocr, x0=0, top=0, x1=500, bottom=12, conf=1)])
    fr = FieldResult(name="a", value="x", status=Status.VERIFIED, confidence=1.0,
                     quote=printed)
    _quote_check(fr, rows)
    assert fr.status == Status.VERIFIED, fr.notes


def test_arithmetic_holds_over_any_reading_combination():
    # '1,234' reads 1.234 AND 1234 — the relation must try combinations,
    # not silently pick the smallest reading
    from paperpin.verify.arithmetic import evaluate_relation
    holds, _ = evaluate_relation({"sum": ["subtotal", "vat_amount"]},
                                 "1,234", {"subtotal": "1,000", "vat_amount": "234"})
    assert holds is True


def test_hallucinated_quote_demotes_a_verified_field():
    # §6.6.5 lock: the whole point of the quote check is the DEMOTION — a
    # quote matching nothing on the page marks the transcription suspect
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, Segment, Status
    from paperpin.verify.verify import _quote_check

    rows = build_rows([Segment(text="Faktura c. 2026001 Celkem 240,00",
                               x0=0, top=0, x1=500, bottom=12, conf=1)])
    fr = FieldResult(name="a", value="240,00", status=Status.VERIFIED,
                     confidence=1.0, quote="completely different words")
    _quote_check(fr, rows)
    assert fr.status == Status.LOW_CONFIDENCE
    assert any("quote" in n for n in fr.notes)


def test_canonical_recheck_demotes_mismatched_id_evidence():
    # verify step 1 lock: evidence that shares nothing with the value can
    # never stay verified
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, FieldType, Segment, Status
    from paperpin.verify.verify import verify_results

    rows = build_rows([Segment(text="XYZ999", x0=0, top=0, x1=60, bottom=12,
                               conf=1)])
    fr = FieldResult(name="code", value="ABC123", status=Status.VERIFIED,
                     confidence=1.0, evidence="XYZ999", page=0,
                     bbox=(0.1, 0.1, 0.2, 0.12))
    verify_results({"code": fr}, {"code": FieldSpec(name="code", type=FieldType.ID)},
                   rows, {0: "textlayer"})
    assert fr.status == Status.LOW_CONFIDENCE
    assert any("re-comparison" in n for n in fr.notes)


def test_ambiguous_survives_canonical_recheck_mismatch():
    # e39c3dd lock: recheck demotes VERIFIED only — an AMBIGUOUS field must
    # not collapse to low_confidence just because one reading disagrees
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, FieldType, Segment, Status
    from paperpin.verify.verify import verify_results

    rows = build_rows([Segment(text="XYZ999", x0=0, top=0, x1=60, bottom=12,
                               conf=1)])
    fr = FieldResult(name="code", value="ABC123", status=Status.AMBIGUOUS,
                     confidence=0.5, evidence="XYZ999", page=0,
                     bbox=(0.1, 0.1, 0.2, 0.12))
    verify_results({"code": fr}, {"code": FieldSpec(name="code", type=FieldType.ID)},
                   rows, {0: "textlayer"})
    assert fr.status == Status.AMBIGUOUS


def test_far_future_date_never_stays_verified():
    # 5429f23 lock: date plausibility follows the clock (±10y window)
    from paperpin.align.rows import build_rows
    from paperpin.types import FieldResult, FieldSpec, FieldType, Segment, Status
    from paperpin.verify.verify import verify_results

    rows = build_rows([Segment(text="2039-03-15", x0=0, top=0, x1=90, bottom=12,
                               conf=1)])
    fr = FieldResult(name="d", value="2039-03-15", status=Status.VERIFIED,
                     confidence=1.0, evidence="2039-03-15", page=0,
                     bbox=(0.1, 0.1, 0.2, 0.12))
    verify_results({"d": fr}, {"d": FieldSpec(name="d", type=FieldType.DATE)},
                   rows, {0: "textlayer"})
    assert fr.status == Status.LOW_CONFIDENCE
    assert any("date" in n.lower() for n in fr.notes)


def test_reread_subset_of_digits_is_not_agreement():
    # F9 (round-3): the clipped-tail rule accepted any digit-multiset
    # subset, so a box that drifted onto '20,00' CONFIRMED evidence
    # '240,00' — the defense against drifted boxes accepted the drift
    from paperpin.types import FieldSpec, FieldType
    from paperpin.verify.crop_reread import reread_agrees
    s = FieldSpec(name="total", type=FieldType.NUMBER)
    assert reread_agrees(s, "240,00", "20,00") is False
    assert reread_agrees(s, "1 234,00", "234") is False
    assert reread_agrees(s, "1 234,00", "100") is False


def test_reread_clipped_decimal_tail_still_agrees():
    from paperpin.types import FieldSpec, FieldType
    from paperpin.verify.crop_reread import reread_agrees
    s = FieldSpec(name="total", type=FieldType.NUMBER)
    assert reread_agrees(s, "68,70", "68") is True
    assert reread_agrees(s, "1 234,00", "1 234") is True


def test_checksum_never_raises_confidence_of_a_fuzzy_location():
    # F13 (round-3): a passing IBAN checksum proves the VALUE; it rewrote
    # the numeric confidence to 0.99 on a box that was only a fuzzy guess
    from paperpin.types import FieldResult, FieldSpec, FieldType, Status
    from paperpin.verify.verify import _checksum_pass
    fr = FieldResult(name="iban", value="LV80BANK0000435195001",
                     status=Status.LOW_CONFIDENCE, confidence=0.79,
                     evidence="LV8OBANKOOO0435195001", page=0,
                     bbox=(0.1, 0.1, 0.4, 0.12),
                     notes=["fuzzy match — human should glance"])
    _checksum_pass(FieldSpec(name="iban", type=FieldType.ID, checksum="iban"),
                   fr)
    assert fr.proof == "checksum"
    assert fr.confidence <= 0.79
    assert fr.status == Status.LOW_CONFIDENCE
