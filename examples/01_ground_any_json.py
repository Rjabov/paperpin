"""Ground an existing extraction. No model, no API key, fully offline.

This is the core move: you already have JSON from somewhere (your own
prompt, Azure Document Intelligence, a colleague's pipeline, a year-old
batch job). paperpin tells you, for every value, WHERE it is on the page
and whether it can be trusted.

Run from the repo root:  python examples/01_ground_any_json.py
"""
from paperpin import ground

extraction = {
    "supplier_name": "Havel & Kraus Paper s.r.o.",
    "invoice_number": "2026-0847",
    "issue_date": "04.08.2026",
    "iban": "CZ23 2222 0000 0047 1823 0267",
    "total_due": "2 424.54",
    # a list of row objects is grounded per cell, no schema needed:
    "items": [
        {"description": "Kraft envelopes C4 self-seal, 90 g/m² ribbed, box of 250",
         "qty": "10", "amount": "312.50"},
    ],
    # this value is not on the document. Watch what happens to it:
    "approved_by": "M. Sedláčková",
}

result = ground("fixtures/demo/demo_invoice.pdf", extraction=extraction)

for fr in result:                      # iterate FieldResult objects
    print(f"{fr.name:24} {fr.status.value:14} {fr.evidence or '':45}"
          f" bbox={fr.bbox}")

# per-status counts, e.g. {'verified': 9, 'not_found': 1}
print("\nsummary:", result.counts())

# the fabricated field came back as the hallucination flag:
assert result["approved_by"].status.value == "not_found"

# table rows were flattened to items[0].description, items[0].qty, ...
print("row cell:", result["items[0].qty"].status.value,
      result["items[0].qty"].bbox)

# three ways to keep the proof:
result.save("run.json")                # full JSON, versioned, atomic write
result.overlay("run.png")              # the page with status-colored boxes
result.viewer("run.html")              # self-contained interactive viewer
print("\nwrote run.json, run.png, run.html")
