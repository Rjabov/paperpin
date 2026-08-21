"""Schemas: presets, your own field lists, and what each knob buys you.

A schema is optional. Without one, field types are inferred from names
and values. With one, you get guided recall (the model is asked for
exactly these fields) and stronger verification (checksums, arithmetic
proofs, anchors).

Run from the repo root:  python examples/03_schemas.py
"""
from paperpin import ground

# --- 1. presets ----------------------------------------------------------
# Two ship today: "invoice" and "receipt".
# result = extract("doc.pdf", schema="invoice", model="gemini/gemini-2.5-flash")

# --- 2. your own schema: a plain dict ------------------------------------
# Each key is a field name; each value describes the field. Everything is
# optional except that the dict key names the field.
schema = {
    # type steers matching: text | number | date | id | percent | block | table
    "invoice_number": {"type": "id",
                       # anchors: label words printed near the value. They
                       # break ties when the same string appears twice.
                       "anchors": ["invoice", "no."]},

    "issue_date": {"type": "date", "anchors": ["issue date"]},

    # id + checksum: a checksum-valid match becomes a recorded proof.
    # Supported: "iban" | "ean" | "vat"
    "iban": {"type": "id", "checksum": "iban"},

    # block: multi-line values (addresses). Matched as a token set, so
    # line-wrap differences don't break it.
    "supplier_address": {"type": "block"},

    # number + proof: arithmetic relations verify the value against other
    # fields. {"sum": [...]} | {"product": [...]} | {"percent_of": [base, rate]}
    "total_excl_vat": {"type": "number"},
    "vat_total": {"type": "number"},
    "total_due": {"type": "number",
                  "proof": {"sum": ["total_excl_vat", "vat_total"]}},

    # aliases: the document may print an alternate literal for the value
    # ("CZK" printed as "Kč"). Matching any alias grounds the field.
    "currency": {"type": "text", "aliases": {"EUR": ["€"]}},

    # table: per-cell grounding for arrays of row objects.
    "items": {"type": "table", "columns": {
        "description": {"type": "text"},
        "qty": {"type": "number"},
        "amount": {"type": "number"},
    }},
}

extraction = {
    "invoice_number": "2026-0847",
    "issue_date": "04.08.2026",
    "iban": "CZ23 2222 0000 0047 1823 0267",
    "supplier_address": "Přístavní 1478/24, 170 00 Praha 7, Czech Republic",
    "total_excl_vat": "2 038.40",
    "vat_total": "386.14",
    "total_due": "2 424.54",
    "currency": "EUR",
    "items": [{"description": "Offset paper A3 100 g/m², natural white, 250-sheet ream",
               "qty": "6", "amount": "110.40"}],
}

result = ground("fixtures/demo/demo_invoice.pdf",
                extraction=extraction, schema=schema)

for fr in result:
    proof = f"  proof={fr.proof}" if fr.proof else ""
    print(f"{fr.name:24} {fr.status.value:14}{proof}")

# the IBAN passed its checksum, the grand total passed its arithmetic:
assert result["iban"].proof == "checksum"
assert result["total_due"].proof == "arithmetic"

# --- 3. a schema can also live in a JSON file ----------------------------
# result = ground("doc.pdf", extraction=..., schema="my_schema.json")
