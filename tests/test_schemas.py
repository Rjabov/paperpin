"""Schema-less type inference (BYO-JSON, E-40) — names + values decide types.

Real-doc regression: 'vat_amount' matched the 'vat' id-keyword before the
amount-ness of the name was considered, so schema-less runs verified numeric
values with strict id substring compares (false crop re-read demotions).
"""
from paperpin.schemas import infer_spec
from paperpin.types import FieldType


def test_money_names_with_numeric_values_are_numbers():
    assert infer_spec("vat_amount", "12,81").type == FieldType.NUMBER
    assert infer_spec("total", "1 900,00").type == FieldType.NUMBER
    assert infer_spec("unit_price", "7 000").type == FieldType.NUMBER


def test_rate_names_with_numeric_values_are_percent():
    assert infer_spec("vat_rate", "21").type == FieldType.PERCENT
    assert infer_spec("discount_percent", "5").type == FieldType.PERCENT


def test_id_names_stay_ids():
    assert infer_spec("invoice_number", "2026001").type == FieldType.ID
    assert infer_spec("vat_number", "CZ25545671").type == FieldType.ID
    assert infer_spec("iban", "LV97HABA0001402047731").type == FieldType.ID
    assert infer_spec("variable_symbol", "2611435").type == FieldType.ID


def test_dates_and_blocks_unchanged():
    assert infer_spec("invoice_date", "2026-05-20").type == FieldType.DATE
    assert infer_spec("supplier_address", "Row 1\nRow 2").type == FieldType.BLOCK


def test_malformed_proof_fails_at_schema_resolution():
    import pytest

    from paperpin.schemas import resolve_schema
    with pytest.raises(ValueError, match="percent_of"):
        resolve_schema({"vat": {"type": "number",
                                "proof": {"percent_of": ["only_one"]}}})
    with pytest.raises(ValueError, match="unknown proof"):
        resolve_schema({"x": {"type": "number", "proof": {"magic": ["a", "b"]}}})


def test_string_alias_value_is_rejected():
    import pytest

    from paperpin.schemas import resolve_schema
    with pytest.raises(ValueError, match="aliases"):
        resolve_schema({"currency": {"type": "text",
                                     "aliases": {"CZK": "Kč"}}})


def test_error_family_covers_the_shape_abuse_paths():
    # B-P3 batch (round-3): raw TypeError/ValueError/AttributeError leaked
    # from the public surface — `except PaperpinError` must catch these
    import pytest

    from paperpin.errors import PaperpinError
    from paperpin.schemas import infer_spec, resolve_schema
    from paperpin.types import FieldSpec

    with pytest.raises(PaperpinError):
        infer_spec(1, "x")                       # non-str field name
    with pytest.raises(PaperpinError):
        resolve_schema({"a": {"type": "text", "aliases": "CZK"}})
    with pytest.raises(PaperpinError):
        resolve_schema({"a": {"type": "nope"}})
    with pytest.raises(PaperpinError):
        resolve_schema({"a": {"type": "table", "columns": 5}})
    with pytest.raises(PaperpinError):
        resolve_schema({"a": 5})
    with pytest.raises(PaperpinError):
        resolve_schema({"a": {"type": "id", "checksum": "luhn"}})
    with pytest.raises(PaperpinError):
        resolve_schema({"a": {"type": "id", "pattern": "([a-"}})


def test_extraction_shape_abuse_is_typed():
    import pytest

    from paperpin.adapters.base import load_byo_extraction
    from paperpin.errors import PaperpinError

    with pytest.raises(PaperpinError):
        load_byo_extraction(5)
    with pytest.raises(PaperpinError):
        load_byo_extraction('[{"total": "5"}]')  # array, not object
    with pytest.raises(PaperpinError):
        load_byo_extraction("5")


def test_document_source_abuse_is_typed():
    import pytest

    from paperpin.errors import PaperpinError
    from paperpin.intake.loader import load_document

    with pytest.raises(PaperpinError):
        load_document(12345)
    with pytest.raises(PaperpinError):
        load_document("fixtures")  # a directory


def test_quantity_strings_infer_as_numbers():
    from paperpin.schemas import infer_spec
    from paperpin.types import FieldType
    assert infer_spec("quantity", "2,050").type == FieldType.NUMBER
    assert infer_spec("quantity", "8,000").type == FieldType.NUMBER
    # id-looking names keep id typing even for digit values
    assert infer_spec("invoice_number", "0308").type == FieldType.ID


def test_account_like_names_never_type_as_numbers():
    # 'count' inside bankAccount flipped 25 corpus fields to not_found when
    # it briefly joined the number keywords (2026-08-21)
    from paperpin.schemas import infer_spec
    from paperpin.types import FieldType
    assert infer_spec("bankAccount", "2600123456/0100").type != FieldType.NUMBER
    assert infer_spec("country", "420").type != FieldType.NUMBER
