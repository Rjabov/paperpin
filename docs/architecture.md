# paperpin architecture

*(kept current as the codebase evolves — this describes what is BUILT, not plans)*

## The one rule

**Never ask the model for coordinates.** The model reads; deterministic OCR /
text-layer geometry locates; an alignment algorithm links them; verification
proves or flags every field. A box produced this way cannot be hallucinated.

## Pipeline

```
INTAKE → GEOMETRY → EXTRACTION → ALIGNMENT → VERIFICATION → OUTPUTS
```

| stage | module(s) | what happens |
|---|---|---|
| INTAKE | `paperpin/intake/` | PDF/image/HEIC loading, EXIF transpose, per-PAGE route choice (text layer vs OCR) with a garbage-text-layer check |
| GEOMETRY | `paperpin/geometry/`, `paperpin/backends/` | Segments (text + boxes + confidence) per page. OCR route: best-of-4 orientation search, optional small-text rescue pass, per-char boxes built in processed space and mapped back through the invertible transform chain. Cached by document sha256 |
| EXTRACTION | `paperpin/adapters/` | `byo` (any JSON — no model), `gemini`, any OpenAI-compatible endpoint. Quote-then-extract prompting: every field returns `{value, quote}` |
| ALIGNMENT | `paperpin/align/` | Canonical maps (accent-folded, offset-indexed), visual rows with char→segment source maps, type-aware matchers with interpretation candidate sets, cross-row merging, anchor lexicon + position priors for disambiguation |
| VERIFICATION | `paperpin/verify/` | Canonical re-comparison, checksums (IBAN mod-97 incl. confusable repair, EAN, VAT formats + SK/PL checksums), invoice arithmetic cross-checks, model-quote existence check |
| OUTPUTS | `paperpin/outputs/` | JSON, overlay PNG on the ORIGINAL image, self-contained HTML viewer |

## Statuses (exactly five — never collapse)

`verified` · `low_confidence` · `ambiguous` · `not_found` (hallucination flag)
· `not_present` (model said null — honest absence; never confuse with not_found)

## Coordinate convention

Bounding boxes are `(x0, y0, x1, y1)`, normalized 0..1, origin top-left of the
**upright original page** — the file as the user's viewer displays it. All
coordinate math lives in `paperpin/geometry/transform.py` (invertible affine
chain); nothing is converted inline anywhere else. PDF pages use points,
images use pixels; normalization makes outputs uniform.

## Key design decisions

- **Interpretation candidate sets, not single parses.** `146,14` reads only as
  146.14; `1,234` reads as both 1.234 and 1234; `24 158,97` is either one
  spaced-thousands number or a qty next to a price — all readings become
  candidates and anchors/priors disambiguate. Same idea for day/month order in
  dates.
- **Same value printed N times is not ambiguity** — the strongest instance is
  pinned, all instances are reported. `ambiguous` is reserved for ties between
  locations with different content.
- **Short numbers need anchors.** A 1–2 digit value with no supporting label
  nearby never passes as `verified` (E-22) — too easy to pin the wrong glyph.
- **Sub-boxes are cut in processed space.** Char boxes are built where the
  text is guaranteed horizontal (OCR's view) and mapped back through the
  chain, so slicing works on rotated pages. The text layer provides exact
  per-char boxes for free.
- **Honest degradation.** When quality drops, fields come back
  `low_confidence`/`not_found`, never silently wrong. The evaluation harness
  counts a verified-but-wrong-location pin as the one forbidden failure.

## Testing

- `tests/` — unit (canonicalizers, number/date matrices, transforms,
  checksums, aligner semantics) + text-layer e2e on the synthetic corpus.
- `bench/generate_corpus.py` — parameterized invoice generator (SK/LV/EN/DE,
  layouts, formats) emitting ground-truth boxes.
- `bench/degrade.py` — degradation matrix (clean/rot270/blur+jpeg/photo-sim)
  with truth boxes mapped through the same transform chain.
- `bench/evaluate.py` — located-rate, IoU, center-in-truth, planted-fake
  catch; the CI gates in `tests/test_degraded_e2e.py` enforce per-tier
  thresholds and silent-wrong = 0.
- Every real-document bug becomes a synthetic fixture reproducing the trait.

## Performance posture (CPU magic)

Default install runs on a CPU-only laptop. RapidOCR (bundled ONNX PP-OCR
models) is the only backend; heavy GPU backends can only ever be optional
extras. OCR results are cached by file
hash, so re-running with a new prompt/schema never re-OCRs. Lazy imports keep
`import paperpin` fast; CI asserts the import-time budget.
