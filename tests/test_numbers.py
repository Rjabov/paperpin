"""E-21 / E-24: the number-format ambiguity matrix."""
from decimal import Decimal

from paperpin.align.matchers import (number_interpretations, value_number_set)


def d(s):
    return Decimal(s)


def test_comma_decimal_unambiguous():
    assert number_interpretations("146,14") == {d("146.14")}


def test_comma_could_be_thousands():
    assert number_interpretations("1,234") == {d("1.234"), d("1234")}


def test_six_decimal_unit_price():
    assert number_interpretations("4,950407") == {d("4.950407")}


def test_mixed_european():
    assert number_interpretations("1.234,56") == {d("1234.56")}


def test_mixed_english():
    assert number_interpretations("1,234.56") == {d("1234.56")}


def test_pure_thousands_dots():
    assert d("1234567") in number_interpretations("1.234.567")


def test_bad_grouping_rejected():
    # "12,3456" is neither a valid decimal-comma nor thousands reading? It IS
    # a valid comma-decimal (12.3456); the thousands reading must be absent.
    assert number_interpretations("12,3456") == {d("12.3456")}


def test_plain_integer():
    assert number_interpretations("23") == {d("23")}


def test_value_side_currency_stripping():
    assert value_number_set("146,14 EUR") == {d("146.14")}
    assert value_number_set("€ 99.50") == {d("99.5")}


def test_value_side_accounting_negative():
    assert value_number_set("(123.45)") == {d("-123.45")}
    assert value_number_set("-123,45") == {d("-123.45")}


def test_value_side_float_and_int():
    assert value_number_set(146.14) == {d("146.14")}
    assert value_number_set(23) == {d("23")}


def test_value_side_spaced_thousands():
    assert d("5227.85") in value_number_set("5 227,85")


def test_value_number_set_strips_unit_suffixes():
    # real-doc regression (Faktura 2510014314, image.jpg): qty "50kg" and
    # price "1 900,00 Kč" produced an EMPTY interpretation set — every such
    # cell was unmatchable regardless of geometry
    assert d("50") in value_number_set("50kg")
    assert d("1.81") in value_number_set("1,81kg")
    assert d("16") in value_number_set("16 ks")
    assert d("1900.00") in value_number_set("1 900,00 Kč")
    assert d("100") in value_number_set("100,-")


def test_value_number_set_multi_number_strings_stay_empty():
    # two separate numbers in one value = ambiguous, no guessing
    assert value_number_set("12 kg 34") == set()


def test_negative_value_with_sign_lost_by_ocr_pins_fuzzy():
    # receipts print '-22,90' but OCR reads '22,90k' — the digits are there,
    # the sign is not provable: pin as fuzzy (low confidence), never not_found
    from paperpin.align.matchers import match_number
    from paperpin.align.rows import build_rows
    from paperpin.types import Segment
    rows = build_rows([Segment(text="RAD.BIRELL 051 PLE 22,90k",
                               x0=40, top=200, x1=340, bottom=214, page=0)])
    ms = match_number(rows, "-22,90")
    assert ms, "sign-lost negative must still produce a candidate"
    assert all(not m.exact for m in ms), "sign unproven -> fuzzy, not verified"


def test_negative_value_prefers_the_signed_print():
    from paperpin.align.matchers import match_number
    from paperpin.align.rows import build_rows
    from paperpin.types import Segment
    rows = build_rows([
        Segment(text="CC LIP OSTIEPOK PLA -11,00", x0=40, top=200, x1=340, bottom=214, page=0),
        Segment(text="CCLIP VRCHA.JEM.OV 11,00", x0=40, top=230, x1=340, bottom=244, page=0),
    ])
    ms = match_number(rows, "-11,00")
    best = max(ms, key=lambda m: (m.exact, m.score))
    assert best.exact and "-11,00" in best.row.text


def test_spaced_thousands_stop_at_the_decimal_tail():
    # 'obaloveho odpadu. 2 044.43 245.34' — the glue must produce '2 044.43'
    # and STOP: a number that already has decimals cannot grow another group
    from paperpin.align.matchers import match_number
    from paperpin.align.rows import build_rows
    from paperpin.types import Segment
    rows = build_rows([Segment(text="obaloveho odpadu. 2 044.43 245.34",
                               x0=40, top=200, x1=340, bottom=214, page=0)])
    assert any(m.exact for m in match_number(rows, "2 044.43"))
    assert any(m.exact for m in match_number(rows, "245.34"))
    rows2 = build_rows([Segment(text="celkem 1 234 567,89 EUR",
                                x0=40, top=200, x1=340, bottom=214, page=0)])
    assert any(m.exact for m in match_number(rows2, "1 234 567,89"))


def test_positive_value_never_verifies_on_a_negative_print():
    # round-2 review: the sign guard only fired when EVERY reading was
    # negative, so total=1200.00 verified on a printed '-1 200,00'
    from paperpin.align.matchers import match_number
    from paperpin.align.rows import build_rows
    from paperpin.types import Segment

    rows = build_rows([Segment(text="Total", x0=0, top=0, x1=50, bottom=10, conf=1),
                       Segment(text="-1 200,00", x0=60, top=0, x1=150, bottom=10, conf=1),
                       Segment(text="EUR", x0=160, top=0, x1=190, bottom=10, conf=1)])
    hits = match_number(rows, 1200.00)
    assert hits, "the digits are on the page — a fuzzy hit is fine"
    assert all(not m.exact for m in hits), "sign mismatch must never be exact"


def test_negative_value_on_negative_print_stays_exact():
    from paperpin.align.matchers import match_number
    from paperpin.align.rows import build_rows
    from paperpin.types import Segment

    rows = build_rows([Segment(text="Credit -1 200,00", x0=0, top=0, x1=150,
                               bottom=10, conf=1)])
    hits = match_number(rows, -1200.00)
    assert any(m.exact for m in hits)
