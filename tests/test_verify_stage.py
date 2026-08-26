"""The verification stage, on hand-built results.

`test_verify_units.py` next door tests the individual checks. This tests what
the stage does with their answers, which is the whole five-status promise: a
check that disagrees must DEMOTE a `verified` field, never leave it standing
and never silently re-label an `ambiguous` one. Every branch here was
previously reachable only through a full document run, so the demotion paths —
the ones that matter most — went unexercised.
"""
import pytest

from paperpin.align.rows import build_rows
from paperpin.types import (FieldResult, FieldSpec, FieldType, Segment,
                            Status)
from paperpin.verify import verify_results

# A mod-97 valid Slovak IBAN and its broken twin (last digit bumped).
GOOD_IBAN = "SK4611000000002612345670"
BAD_IBAN = "SK4611000000002612345671"


def rows_saying(*texts, page=0):
    segments = [Segment(text=t, x0=10.0, top=20.0 + 20 * i, x1=200.0,
                        bottom=34.0 + 20 * i, conf=1.0, page=page)
                for i, t in enumerate(texts)]
    return build_rows(segments)


def field(name="f", value="x", status=Status.VERIFIED, **kw):
    base = dict(name=name, value=value, status=status, confidence=0.9,
                page=0, bbox=(0.1, 0.2, 0.3, 0.25), evidence=str(value))
    base.update(kw)
    return FieldResult(**base)


def run(results, specs=None, rows=None, routes=None, **kw):
    verify_results(results, specs or {}, rows if rows is not None else [],
                   routes or {0: "textlayer"}, **kw)
    return results


def notes_of(fr):
    return " | ".join(fr.notes)


# --------------------------------------------------------------- checksum ---

def test_a_failing_iban_checksum_demotes_a_verified_field():
    fr = field("iban", BAD_IBAN)

    run({"iban": fr}, {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                         checksum="iban")})

    assert fr.status == Status.LOW_CONFIDENCE, "a value that fails its own " \
        "checksum must not keep a verified status"
    assert "⚠" in notes_of(fr) and "mod-97" in notes_of(fr)
    assert fr.proof is None


def test_a_passing_iban_checksum_is_recorded_as_proof():
    fr = field("iban", GOOD_IBAN)

    run({"iban": fr}, {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                         checksum="iban")})

    assert fr.status == Status.VERIFIED
    assert fr.proof == "checksum"
    assert fr.confidence >= 0.99


def test_a_checksum_never_promotes_an_ambiguous_field():
    """The checksum proves the VALUE; `ambiguous` is a statement about the
    LOCATION, and no amount of arithmetic resolves which box is right."""
    fr = field("iban", GOOD_IBAN, status=Status.AMBIGUOUS)

    run({"iban": fr}, {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                         checksum="iban")})

    assert fr.status == Status.AMBIGUOUS
    assert fr.proof == "checksum"


def test_a_confusable_repair_proves_the_value_but_lowers_confidence():
    """OCR read an O for a 0. The repaired value checksums, so it is proof —
    but the pixels were misread, so a human should still glance."""
    fr = field("iban", GOOD_IBAN[:2] + GOOD_IBAN[2:].replace("0", "O", 1))

    run({"iban": fr}, {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                         checksum="iban")})

    assert fr.proof == "checksum"
    assert fr.status == Status.LOW_CONFIDENCE
    assert fr.repaired_value is not None
    assert "confusable repair" in notes_of(fr)


def test_a_value_too_short_to_be_an_iban_is_left_alone():
    fr = field("iban", "SK73 1100")

    run({"iban": fr}, {"iban": FieldSpec(name="iban", type=FieldType.ID,
                                         checksum="iban")})

    assert fr.status == Status.VERIFIED
    assert fr.proof is None
    assert fr.notes == []


@pytest.mark.parametrize("value,expect_proof,expect_status", [
    ("4006381333931", True, Status.VERIFIED),      # valid EAN-13
    ("4006381333932", False, Status.LOW_CONFIDENCE),
])
def test_the_ean_check_digit_promotes_or_demotes(value, expect_proof, expect_status):
    fr = field("ean", value)

    run({"ean": fr}, {"ean": FieldSpec(name="ean", type=FieldType.ID,
                                       checksum="ean")})

    assert fr.status == expect_status
    assert (fr.proof == "checksum") is expect_proof


def test_a_malformed_vat_id_demotes_and_says_so():
    fr = field("vat", "SK99")

    run({"vat": fr}, {"vat": FieldSpec(name="vat", type=FieldType.ID,
                                       checksum="vat")})

    assert fr.status == Status.LOW_CONFIDENCE
    assert "⚠" in notes_of(fr)


# ------------------------------------------------------ canonical recheck ---

def test_evidence_that_is_not_the_value_demotes_the_field():
    """The aligner already guarantees this; the stage re-checks defensively,
    because a box over the wrong number is the one unacceptable outcome."""
    fr = field("total", "2424.54", evidence="1 111.11")

    run({"total": fr}, {"total": FieldSpec(name="total", type=FieldType.NUMBER)})

    assert fr.status == Status.LOW_CONFIDENCE
    assert "canonical re-comparison failed" in notes_of(fr)


def test_a_number_printed_with_other_separators_still_agrees():
    fr = field("total", "2424.54", evidence="2 424,54")

    run({"total": fr}, {"total": FieldSpec(name="total", type=FieldType.NUMBER)})

    assert fr.status == Status.VERIFIED
    assert "canonical re-comparison failed" not in notes_of(fr)


def test_a_field_with_no_evidence_is_not_rechecked():
    fr = field("note", "anything", evidence=None)

    run({"note": fr}, {"note": FieldSpec(name="note", type=FieldType.ID)})

    assert fr.status == Status.VERIFIED


def test_an_implausible_date_is_demoted():
    fr = field("issue_date", "1902-04-01", evidence="1902-04-01")

    run({"issue_date": fr},
        {"issue_date": FieldSpec(name="issue_date", type=FieldType.DATE)})

    assert fr.status == Status.LOW_CONFIDENCE
    assert "plausible window" in notes_of(fr)


# ------------------------------------------------------------ quote check ---

def test_a_quote_that_matches_nothing_demotes_the_field():
    """The value was located, but the model's own citation is invented. That
    is a transcription the reader should not trust."""
    fr = field("total", "2 424.54", quote="Total payable on delivery: 2 424.54")

    run({"total": fr}, rows=rows_saying("Grand total 2 424.54"))

    assert fr.status == Status.LOW_CONFIDENCE
    assert "quote matches nothing" in notes_of(fr)


def test_a_quote_present_on_the_page_leaves_the_field_alone():
    fr = field("total", "2 424.54", quote="Grand total 2 424.54")

    run({"total": fr}, rows=rows_saying("Grand total 2 424.54", "VAT 21%"))

    assert fr.status == Status.VERIFIED
    assert fr.notes == []


def test_a_quote_spanning_two_printed_lines_is_accepted():
    """Addresses wrap. Searching row by row would flag every multi-line quote
    as invented, so the page text is searched as a whole too."""
    fr = field("addr", "Havel & Kraus", quote="Havel & Kraus Paper s.r.o. Praha 6")

    run({"addr": fr}, rows=rows_saying("Havel & Kraus Paper s.r.o.", "Praha 6"))

    assert fr.status == Status.VERIFIED


def test_a_quote_of_pure_punctuation_is_not_a_hallucination_signal():
    """Canon strips it to nothing. Nothing is not evidence of invention."""
    fr = field("total", "1", quote="—  ·  …")

    run({"total": fr}, rows=rows_saying("nothing alike"))

    assert fr.status == Status.VERIFIED
    assert fr.notes == []


def test_an_empty_quote_is_not_a_hallucination_signal():
    fr = field("total", "1", quote="")

    run({"total": fr}, rows=rows_saying("nothing alike"))

    assert fr.status == Status.VERIFIED
    assert fr.notes == []


def test_an_unlocated_field_still_gets_its_quote_checked():
    """`not_found` cannot be demoted further, but the note explains WHY the
    model believed a value that is not there."""
    fr = field("approved_by", "M. Sedláčková", status=Status.NOT_FOUND,
               page=None, bbox=None, evidence=None,
               quote="Approved by M. Sedláčková")

    run({"approved_by": fr}, rows=rows_saying("Grand total 2 424.54"))

    assert fr.status == Status.NOT_FOUND
    assert "quote matches nothing" in notes_of(fr)


# ------------------------------------------------------------- arithmetic ---

def test_document_math_that_holds_becomes_proof():
    spec = FieldSpec(name="total", type=FieldType.NUMBER,
                     proof={"sum": ["net", "vat"]})
    results = {"total": field("total", "121.00"),
               "net": field("net", "100.00"), "vat": field("vat", "21.00")}

    run(results, {"total": spec})

    assert results["total"].proof == "arithmetic"
    assert results["total"].status == Status.VERIFIED


def test_document_math_that_disagrees_says_so_without_moving_the_box():
    """The value IS on the page — the sum is what is wrong. The status must
    reflect doubt, and the note must name the disagreement."""
    spec = FieldSpec(name="total", type=FieldType.NUMBER,
                     proof={"sum": ["net", "vat"]})
    results = {"total": field("total", "999.00"),
               "net": field("net", "100.00"), "vat": field("vat", "21.00")}

    run(results, {"total": spec})

    assert "⚠" in notes_of(results["total"])
    assert "document math disagrees" in notes_of(results["total"])
    assert results["total"].proof is None


def test_invented_operands_cannot_prove_anything():
    """An equation whose operands match nothing on the page is self-consistent
    and worthless — the model can invent a net and a vat that sum to its
    invented total."""
    spec = FieldSpec(name="total", type=FieldType.NUMBER,
                     proof={"sum": ["net", "vat"]})
    results = {"total": field("total", "121.00"),
               "net": field("net", "100.00", status=Status.NOT_FOUND,
                            page=None, bbox=None, evidence=None),
               "vat": field("vat", "21.00", status=Status.NOT_FOUND,
                            page=None, bbox=None, evidence=None)}

    run(results, {"total": spec})

    assert results["total"].proof is None


# ----------------------------------------------------------------- method ---

def test_how_a_field_was_located_is_filled_in_from_its_page():
    fr = field("total", "1", method=None)

    run({"total": fr}, routes={0: "ocr"})

    assert fr.method == "ocr"


def test_an_unlocated_field_gets_no_method():
    fr = field("ghost", "1", status=Status.NOT_FOUND, page=None, bbox=None)

    run({"ghost": fr}, routes={0: "ocr"})

    assert fr.method is None


# -------------------------------------------------------- crop re-read -----

class FakeBackend:
    name = "fake"

    def recognize(self, image):        # never called: reread_crop is faked
        return []


def ocr_run(results, reread, specs=None, monkeypatch=None):
    monkeypatch.setattr("paperpin.verify.crop_reread.reread_crop",
                        lambda *a, **k: reread)
    return run(results, specs, routes={0: "ocr"},
               page_image_provider=lambda idx: None,
               ocr_backend=FakeBackend())


def test_pixels_that_read_back_as_something_else_demote_the_field(monkeypatch):
    """§6.6.2. On an OCR route the box is re-read: if the pixels under it do
    not say what the match said, the pin is not trustworthy."""
    fr = field("supplier", "Havel & Kraus", evidence="Havel & Kraus")

    ocr_run({"supplier": fr}, "Novak Trading s.r.o.", monkeypatch=monkeypatch)

    assert fr.status == Status.LOW_CONFIDENCE
    assert "crop re-read disagreed" in notes_of(fr)


def test_pixels_that_read_back_the_same_confirm_the_box(monkeypatch):
    fr = field("supplier", "Havel & Kraus", evidence="Havel & Kraus")

    ocr_run({"supplier": fr}, "Havel & Kraus", monkeypatch=monkeypatch)

    assert fr.status == Status.VERIFIED
    assert "crop re-read confirmed" in notes_of(fr)


def test_a_checksum_proved_field_skips_the_crop_re_read(monkeypatch):
    """A mod-97 proof is stronger than a second OCR pass, and re-reading costs
    CPU and risks a false demotion on a noisy scan."""
    fr = field("iban", GOOD_IBAN)
    spec = {"iban": FieldSpec(name="iban", type=FieldType.ID, checksum="iban")}

    ocr_run({"iban": fr}, "utter nonsense", specs=spec, monkeypatch=monkeypatch)

    assert fr.status == Status.VERIFIED
    assert "crop re-read" not in notes_of(fr)
