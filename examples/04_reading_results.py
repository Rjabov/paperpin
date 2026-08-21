"""Everything a GroundResult gives you, and a triage pattern for pipelines.

Run from the repo root:  python examples/04_reading_results.py
"""
from paperpin import ground

result = ground("fixtures/demo/demo_invoice.pdf", extraction={
    "supplier_name": "Havel & Kraus Paper s.r.o.",
    "variable_symbol": "20260847",          # printed twice on this doc
    "total_due": "2 424.54",
    "made_up_field": "does not exist",      # planted fake
})

# --- the container behaves like a mapping --------------------------------
fr = result["total_due"]                # one FieldResult
"total_due" in result                   # membership
len(result)                             # field count
list(result.keys())                     # names
for name, field_result in result.items():
    pass
# .fields gives the plain dict if you prefer

# --- one FieldResult -----------------------------------------------------
print("status:   ", fr.status.value)    # verified / low_confidence /
                                        # ambiguous / not_found / not_present
print("page:     ", fr.page)            # 0-based page index
print("bbox:     ", fr.bbox)            # (x0, y0, x1, y1) normalized 0..1,
                                        # origin top-left, on the UPRIGHT
                                        # original page (EXIF already applied)
print("evidence: ", fr.evidence)        # exact document text that matched
print("proof:    ", fr.proof)           # "checksum" | "arithmetic" | None

# --- pixels, when you need them ------------------------------------------
page = result.pages[fr.page]            # PageInfo with original dimensions
x0, y0, x1, y1 = fr.bbox
print("pixel box:", (round(x0 * page.width), round(y0 * page.height),
                     round(x1 * page.width), round(y1 * page.height)))

# --- ambiguous fields carry every candidate ------------------------------
for f in result:
    if f.status.value == "ambiguous":
        print(f"{f.name}: {len(f.candidates)} tied places")
        for c in f.candidates:          # each has .page and .bbox
            print("   candidate on page", c.page, c.bbox)

# --- a triage pattern for real pipelines ---------------------------------
auto, review, reject = [], [], []
for f in result:
    s = f.status.value
    if s == "verified":
        auto.append(f.name)             # safe to post automatically
    elif s in ("low_confidence", "ambiguous"):
        review.append(f.name)           # show a human, pre-pointed at the spot
    elif s == "not_found":
        reject.append(f.name)           # the model made it up. Do NOT post it
    # not_present: the model itself said the document lacks the field

print("\nauto:", auto, "\nreview:", review, "\nreject:", reject)

# --- persistence round-trip ----------------------------------------------
result.save("run.json")
from paperpin import GroundResult
import json
loaded = GroundResult.from_dict(json.load(open("run.json", encoding="utf-8")))
assert loaded["total_due"].bbox == result["total_due"].bbox
print("\nround-trip ok; summary:", loaded.counts())
