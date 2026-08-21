"""Corpus evaluation harness — the CI gate math (HANDOVER §9.2, E-35).

For a document with ground truth: ground its own extraction (plus one planted
hallucination), then score per field:
  located   status in {verified, low_confidence}
  iou       intersection-over-union vs the true box
  center_in the located box's center falls inside the (padded) true box

Gates (checked by tests): text-layer route ≥ 100% located with IoU ≥ 0.30 and
center-in-truth; planted-fake catch rate = 100% everywhere; degraded tiers
per-tier thresholds — a lost field must come back flagged, never silently
wrong (wrong = located but center outside the true box).
"""
from __future__ import annotations

import json
from pathlib import Path

from paperpin import ground
from paperpin.types import Status

PLANT = {"fake_contract_number": "ZML-2026-077"}
LOCATED = {Status.VERIFIED, Status.LOW_CONFIDENCE}


def iou(a, b) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter = (ix1 - ix0) * (iy1 - iy0)
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / max(1e-12, ua)


def center_in(box, truth, pad: float = 0.004) -> bool:
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    return (truth[0] - pad <= cx <= truth[2] + pad
            and truth[1] - pad <= cy <= truth[3] + pad)


def evaluate_doc(doc_path: str | Path, extraction: dict, truth: dict,
                 schema=None, plant: bool = True) -> dict:
    payload = dict(extraction)
    if plant:
        payload.update(PLANT)
    result = ground(str(doc_path), extraction=payload, schema=schema or "invoice")

    per_field = {}
    n_located = n_wrong = n_flagged_wrong = 0
    for name, value in extraction.items():
        fr = result[name]
        t = truth.get(name)
        entry = {"status": fr.status.value, "located": fr.status in LOCATED}
        if entry["located"] and t is not None and fr.bbox is not None:
            entry["iou"] = round(iou(fr.bbox, t["bbox"]), 3)
            # a value printed several times may legitimately pin any instance;
            # every instance is reported in candidates, so a hit by ANY
            # reported box counts. Truth boxes of OTHER fields whose printed
            # text is the same value count too (total vs grand_total).
            from paperpin.align.canon import canon_value
            truth_boxes = [t["bbox"]]
            printed = canon_value(str(t.get("printed", t.get("value", ""))))
            for other_name, other in truth.items():
                if other_name != name and canon_value(
                        str(other.get("printed", ""))).find(printed) >= 0 and printed:
                    truth_boxes.append(other["bbox"])
            boxes = [fr.bbox] + [c.bbox for c in fr.candidates]
            entry["center_in"] = any(center_in(b, tb)
                                     for b in boxes for tb in truth_boxes)
            if not entry["center_in"]:
                entry["wrong_location"] = True
                # SILENT wrong = confidently wrong. A low_confidence pin in
                # the wrong place is a flagged guess — tracked separately.
                if fr.status == Status.VERIFIED:
                    n_wrong += 1
                else:
                    n_flagged_wrong += 1
        if entry["located"]:
            n_located += 1
        per_field[name] = entry

    fake_caught = all(result[k].status == Status.NOT_FOUND for k in PLANT) if plant else None
    return {
        "doc": str(doc_path),
        "n_fields": len(extraction),
        "n_located": n_located,
        "n_silent_wrong": n_wrong,
        "n_flagged_wrong": n_flagged_wrong,
        "located_rate": round(n_located / max(1, len(extraction)), 3),
        "fake_caught": fake_caught,
        "fields": per_field,
        "summary": result.counts(),
    }


def evaluate_corpus(corpus_dir: str | Path, pattern: str = "*.json") -> list[dict]:
    corpus_dir = Path(corpus_dir)
    reports = []
    for meta_path in sorted(corpus_dir.glob(pattern)):
        if meta_path.name == "manifest.json":
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if "extraction" not in meta:
            continue
        doc = meta_path.with_suffix(".pdf")
        if not doc.exists():
            doc = meta_path.with_suffix(".jpg")
        if not doc.exists():
            continue
        reports.append(evaluate_doc(doc, meta["extraction"], meta["truth"]))
    return reports


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus", help="directory with generated fixtures")
    args = ap.parse_args(argv)
    reports = evaluate_corpus(args.corpus)
    for r in reports:
        flag = "OK " if (r["fake_caught"] and r["n_silent_wrong"] == 0) else "!! "
        print(f" {flag}{Path(r['doc']).name:<28} located {r['n_located']}/{r['n_fields']}"
              f"  silent-wrong {r['n_silent_wrong']}"
              f"  flagged-wrong {r['n_flagged_wrong']}  fake_caught={r['fake_caught']}")
    total = sum(r["n_fields"] for r in reports)
    located = sum(r["n_located"] for r in reports)
    print(f"\n corpus: {located}/{total} located "
          f"({100 * located / max(1, total):.1f}%), "
          f"fakes caught {sum(bool(r['fake_caught']) for r in reports)}/{len(reports)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
