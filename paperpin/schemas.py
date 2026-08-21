"""Schema presets and coercion. A schema maps field name → FieldSpec
(type, optional anchors, optional checksum kind). Accepts: preset name,
dict of specs, or a path to a JSON file with the same shape.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Union

from .errors import SchemaError
from .types import FieldSpec, FieldType

INVOICE_PRESET: dict[str, dict] = {
    "invoice_number": {"type": "id"},
    "invoice_date": {"type": "date"},
    "due_date": {"type": "date"},
    "supplier_name": {"type": "text"},
    "supplier_address": {"type": "block"},
    "supplier_reg_number": {"type": "id", "anchors": ["ico", "reg"]},
    "supplier_vat_number": {"type": "id", "checksum": "vat"},
    "customer_name": {"type": "text"},
    "customer_address": {"type": "block"},
    "customer_vat_number": {"type": "id", "checksum": "vat"},
    "iban": {"type": "id", "checksum": "iban"},
    "swift": {"type": "id"},
    "variable_symbol": {"type": "id"},
    "line_items": {"type": "table", "columns": {
        "description": {"type": "text"},
        "qty": {"type": "number"},
        "unit_price": {"type": "number"},
        "amount": {"type": "number"},
        "ean": {"type": "id", "checksum": "ean"},
    }},
    "subtotal": {"type": "number"},
    "vat_rate": {"type": "percent"},
    "vat_amount": {"type": "number"},
    "total": {"type": "number"},
    "currency": {"type": "text"},
}

RECEIPT_PRESET: dict[str, dict] = {
    "merchant_name": {"type": "text"},
    "merchant_address": {"type": "block"},
    "receipt_date": {"type": "date"},
    "total": {"type": "number"},
    "vat_amount": {"type": "number"},
    "payment_method": {"type": "text"},
}

PRESETS = {"invoice": INVOICE_PRESET, "receipt": RECEIPT_PRESET}

SchemaLike = Union[str, dict, None]

# ---------------------------------------------------------------------------
# THE domain-guess layer (E-40). The core engine never interprets field
# names — it reads FieldSpec.anchors / affinity / checksum declarations.
# Everything name-based (presets, BYO-JSON inference, user schemas without
# explicit anchors) is enriched HERE and only here; a new domain means new
# hint data or an explicit schema, never core changes.
# ---------------------------------------------------------------------------

FIELD_ANCHOR_HINTS: dict[str, list[str]] = {
    "total": ["total", "celkom", "celkova", "celková", "suma", "summa", "kopā", "kopa",
              "summe", "gesamtbetrag", "spolu", "itogo", "amount due", "grand total",
              "kopsumma", "apmaksai", "k uhrade", "na uhradu", "fakturovana suma"],
    "subtotal": ["subtotal", "zaklad", "základ", "bez dph", "bez pvn", "netto", "net",
                 "summa bez", "medzisucet", "starpsumma"],
    "vat_amount": ["vat", "dph", "pvn", "mwst", "tax", "nodoklis", "dan", "daň"],
    "vat_rate": ["vat", "dph", "pvn", "mwst", "%", "sazba", "likme", "satz", "rate"],
    "invoice_number": ["invoice", "faktura", "faktúra", "rekins", "rēķins", "rechnung",
                       "cislo", "číslo", "nr", "no", "num", "n."],
    "invoice_date": ["date", "datum", "dátum", "datums", "vyhotovenia", "izrakstisanas",
                     "issued", "vystavenia", "rechnungsdatum"],
    "due_date": ["due", "splatnosti", "splatnost", "apmaksas termins", "termins",
                 "fallig", "fällig", "zahlbar", "payment due"],
    "delivery_date": ["delivery", "dodania", "dodavky", "piegades", "lieferdatum"],
    "iban": ["iban", "konts", "účet", "ucet", "account", "konto", "bankas"],
    "swift": ["swift", "bic"],
    "variable_symbol": ["variabilny symbol", "variabilný symbol", "vs", "variable symbol"],
    "supplier_name": ["dodavatel", "dodávateľ", "piegadatajs", "piegādātājs", "supplier",
                      "seller", "verkaufer", "verkäufer", "predajca", "izsniedza"],
    "customer_name": ["odberatel", "odberateľ", "sanemejs", "saņēmējs", "customer",
                      "buyer", "kaufer", "käufer", "pircejs", "pircējs", "klients",
                      "bill to", "fakturacne udaje"],
    # stem entries: any supplier_*/customer_* field inherits its party's label
    "supplier": ["dodavatel", "dodávateľ", "piegadatajs", "supplier", "seller",
                 "verkaufer", "verkäufer"],
    "customer": ["odberatel", "odberateľ", "sanemejs", "customer", "buyer",
                 "kaufer", "käufer", "bill to"],
    "address": ["adresa", "address", "adrese", "anschrift", "sidlo", "sídlo"],
    "order_number": ["order", "objednavka", "objednávka", "pasutijums", "pasūtījums",
                     "bestellung", "po number"],
    "reg_number": ["ico", "ičo", "reg", "registracijas", "reģistrācijas", "company id"],
    "vat_number": ["ic dph", "ič dph", "dic", "dič", "pvn", "vat", "ust", "nip"],
    # note: unit markers (ks, gab, pcs) are NOT anchors — they trail every
    # quantity-like number, so as anchors they endorse stray digits (E-22)
    "qty": ["qty", "quantity", "mnozstvo", "množstvo", "daudzums", "menge"],
    "unit_price": ["price", "cena", "cena za", "preis", "za mj", "unit"],
    "amount": ["amount", "celkem", "celkom", "suma", "summa", "summe", "betrag",
               "total", "spolu", "kopā", "kopa", "castka", "částka"],
    "ean": ["ean", "barcode", "kods", "kód", "kod"],
    "currency": ["currency", "mena", "valuta", "valūta", "wahrung", "währung", "eur"],
}

FIELD_AFFINITY_HINTS: dict[str, list[str]] = {
    # a currency mark prints beside its amount — bind to the pinned total
    "currency": ["total", "grand_total", "amount_due", "total_with_vat"],
}

FIELD_ALIAS_HINTS: dict[str, dict[str, list[str]]] = {
    # extractors return the ISO code; documents print the local mark.
    # (kr serves DKK/SEK/NOK alike — the asserted code disambiguates.)
    "currency": {
        "CZK": ["Kč", "Kc"], "EUR": ["€"], "GBP": ["£"], "USD": ["$"],
        "PLN": ["zł", "zl"], "CHF": ["Fr", "SFr"], "DKK": ["kr"],
        "SEK": ["kr"], "NOK": ["kr"], "HUF": ["Ft"],
    },
}

FIELD_PROOF_HINTS: dict[str, dict] = {
    # the invoice equation web; any other domain declares its own `proof`
    "total": {"sum": ["subtotal", "vat_amount"]},
    "vat_amount": {"percent_of": ["subtotal", "vat_rate"]},
    "amount": {"product": ["qty", "unit_price"]},
    "line_amount": {"product": ["qty", "unit_price"]},
}

FIELD_CHECKSUM_HINTS = (("iban", "iban"), ("ean", "ean"), ("barcode", "ean"),
                        ("vat_number", "vat"), ("vat_id", "vat"),
                        ("ic_dph", "vat"), ("tax_id", "vat"))
# the kinds verify.py implements — a typo'd declaration silently dropped the
# proof; now it's a SchemaError at resolve time
_CHECKSUM_KINDS = {"iban", "ean", "vat"}

# EU VAT ids: optional 2-letter country prefix, digit core, rare letter tail.
# Extractors return the prefixed canonical form; many documents print only
# the national core.
_VAT_PATTERN = r"(?:[a-z]{2})?\d{7,12}[a-z]{0,2}"
FIELD_PATTERN_HINTS = (("vat_number", _VAT_PATTERN), ("vat_id", _VAT_PATTERN),
                       ("vatid", _VAT_PATTERN), ("ic_dph", _VAT_PATTERN))


def _anchor_hints(name: str) -> list[str]:
    key = name.lower()
    if key in FIELD_ANCHOR_HINTS:
        return FIELD_ANCHOR_HINTS[key]
    out: list[str] = []
    for stem, words in FIELD_ANCHOR_HINTS.items():
        if stem in key:
            out.extend(words)
    return out


def _validate_proof(name: str, proof) -> None:
    if not isinstance(proof, dict) or len(proof) != 1:
        raise SchemaError(f"field {name!r}: proof must be one relation, "
                         f"got {proof!r}")
    kind, operands = next(iter(proof.items()))
    if kind not in ("sum", "product", "percent_of"):
        raise SchemaError(f"field {name!r}: unknown proof relation {kind!r}")
    if (not isinstance(operands, (list, tuple))
            or not all(isinstance(o, str) for o in operands)
            or (kind == "percent_of" and len(operands) != 2)
            or (kind in ("sum", "product") and len(operands) < 2)):
        raise SchemaError(f"field {name!r}: {kind} needs a list of field names"
                         + (" [base, rate]" if kind == "percent_of" else ""))


def enrich_spec(spec: FieldSpec) -> FieldSpec:
    """Fill unset declarations from the name hints; explicit ones stay."""
    if spec.proof is not None:
        _validate_proof(spec.name, spec.proof)
    if spec.aliases is not None:
        if not isinstance(spec.aliases, dict) or not all(
                isinstance(v, (list, tuple)) for v in spec.aliases.values()):
            raise SchemaError(f"field {spec.name!r}: aliases must be a dict of "
                              "value -> list of alternate prints")
    if spec.checksum is not None and spec.checksum not in _CHECKSUM_KINDS:
        raise SchemaError(
            f"field {spec.name!r}: unknown checksum {spec.checksum!r} — "
            f"one of {sorted(_CHECKSUM_KINDS)}")
    if spec.pattern is not None:
        try:
            re.compile(spec.pattern)
        except re.error as e:
            raise SchemaError(
                f"field {spec.name!r}: pattern does not compile: {e}") from e
    if not spec.anchors:
        hints = _anchor_hints(spec.name)
        if hints:
            spec.anchors = list(hints)
    if not spec.affinity:
        aff = FIELD_AFFINITY_HINTS.get(spec.name.lower())
        if aff:
            spec.affinity = list(aff)
    if spec.proof is None:
        proof = FIELD_PROOF_HINTS.get(spec.name.lower())
        if proof:
            spec.proof = dict(proof)
    if spec.aliases is None:
        aliases = FIELD_ALIAS_HINTS.get(spec.name.lower())
        if aliases:
            spec.aliases = {k: list(v) for k, v in aliases.items()}
    if spec.checksum is None:
        n = spec.name.lower()
        for key, kind in FIELD_CHECKSUM_HINTS:
            if key in n or n == key:
                spec.checksum = kind
                break
    if spec.pattern is None:
        n = spec.name.lower()
        for key, pat in FIELD_PATTERN_HINTS:
            if key in n or n == key:
                spec.pattern = pat
                break
    if spec.columns:
        for col in spec.columns.values():
            enrich_spec(col)
    return spec


def resolve_schema(schema: SchemaLike) -> dict[str, FieldSpec]:
    if schema is None:
        return {}
    if isinstance(schema, str):
        if schema in PRESETS:
            raw = PRESETS[schema]
        else:
            p = Path(schema)
            if not p.exists():
                raise SchemaError(f"unknown schema {schema!r} — presets: "
                                 f"{sorted(PRESETS)} or a JSON file path")
            raw = json.loads(p.read_text(encoding="utf-8"))
    elif isinstance(schema, dict):
        raw = schema
    else:
        raise TypeError(f"cannot use {type(schema).__name__} as a schema")
    from dataclasses import replace as _replace
    out = {}
    for name, spec in raw.items():
        coerced = FieldSpec.coerce(name, spec)
        if isinstance(spec, FieldSpec):  # never mutate a caller-owned spec
            coerced = _replace(coerced)
        out[name] = enrich_spec(coerced)
    return out


def infer_spec(name: str, value) -> FieldSpec:
    """Best-effort typing for schemaless ground() calls (BYO-JSON, E-40)."""
    if not isinstance(name, str):
        raise SchemaError(
            f"field names must be strings, got {type(name).__name__}: {name!r}")
    n = name.lower()
    if isinstance(value, bool):
        return enrich_spec(FieldSpec(name=name, type=FieldType.TEXT))
    if isinstance(value, (int, float)):
        if "rate" in n or "percent" in n:
            return enrich_spec(FieldSpec(name=name, type=FieldType.PERCENT))
        return enrich_spec(FieldSpec(name=name, type=FieldType.NUMBER))
    if isinstance(value, str):
        from .align.matchers import value_date_set, value_number_set
        dates = value_date_set(value)
        if "date" in n or dates:
            if dates:
                return enrich_spec(FieldSpec(name=name, type=FieldType.DATE))
        # money/rate-ness of the NAME outranks id keywords: 'vat_amount' is a
        # number that happens to contain 'vat', not an id (E-40 regression)
        if value_number_set(value):
            if "rate" in n or "percent" in n:
                return enrich_spec(FieldSpec(name=name, type=FieldType.PERCENT))
            # "count" would be the natural next keyword but it hides inside
            # bankAccount/country — 25 corpus fields flipped to not_found
            # when it briefly shipped (2026-08-21)
            if any(k in n for k in ("total", "amount", "price", "sum", "qty",
                                    "quantit")):
                return enrich_spec(FieldSpec(name=name, type=FieldType.NUMBER))
        if any(k in n for k in ("iban", "vat", "number", "symbol", "ean", "id",
                                "code", "swift", "reference")):
            return enrich_spec(FieldSpec(name=name, type=FieldType.ID))
        if "address" in n or "\n" in value:
            return enrich_spec(FieldSpec(name=name, type=FieldType.BLOCK))
    return enrich_spec(FieldSpec(name=name, type=FieldType.TEXT))
