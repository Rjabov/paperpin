from paperpin.verify.checksums import (ean_check_digit, iban_check, iban_mod97,
                                       vat_check)


def test_iban_valid():
    assert iban_mod97("SK7311000000002629028990")
    assert iban_mod97("SK73 1100 0000 0026 2902 8990".replace(" ", ""))


def test_iban_spaced_input():
    ok, repaired, note = iban_check("SK73 1100 0000 0026 2902 8990")
    assert ok is True and repaired is None


def test_iban_single_digit_corruption_fails():
    assert not iban_mod97("SK7311000000002629028991")


def test_iban_confusable_repair_e14():
    ok, repaired, note = iban_check("SK73 11OO 0000 0026 2902 8990")  # O for 0
    assert ok is True
    assert repaired == "SK7311000000002629028990"
    assert "repair" in note


def test_ean13():
    assert ean_check_digit("8586024770485") is True
    assert ean_check_digit("8586024770486") is False
    assert ean_check_digit("12345") is None  # not an EAN shape


def test_ean8():
    assert ean_check_digit("40170725") is True


def test_sk_vat_checksum():
    ok, note = vat_check("SK2022072646")   # divisible by 11
    assert ok is True and "passed" in note
    ok, note = vat_check("SK2022072647")
    assert ok is False


def test_vat_format_only_countries():
    ok, note = vat_check("LV40103567891")
    assert ok is True and "format" in note


def test_unknown_country_none():
    ok, note = vat_check("XX123")
    assert ok is None


def test_upc_and_gtin_share_the_check_family():
    from paperpin.verify.checksums import ean_check_digit
    assert ean_check_digit("036000291452") is True     # UPC-A
    assert ean_check_digit("036000291453") is False
    assert ean_check_digit("00012345600012") is True   # GTIN-14
    assert ean_check_digit("4006381333931") is True    # EAN-13 still
    assert ean_check_digit("96385074") is True         # EAN-8 still


def test_non_iban_shape_is_not_a_checksum_failure():
    from paperpin.verify.checksums import iban_check
    passed, repaired, note = iban_check("CUSTOMER-REF-9912837XX")
    assert passed is False and "shape" in note
