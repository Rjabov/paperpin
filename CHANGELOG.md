# Changelog

All notable changes to paperpin are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `-o -` writes the result JSON to stdout (summary moves to stderr) and
  `--quiet` drops the summary, so other languages can pipe paperpin
  without a temp file.
- `paperpin pages` and `result.page_image()`: the page rasters a viewer
  draws normalized boxes on.
- [`docs/result.schema.json`](docs/result.schema.json): the result JSON
  described as JSON Schema, with `paperpin.schema` now carrying the
  version of the payload shape.

## [0.1.0] - 2026-08-21

First public release.

### Added

- `ground()`: pin any existing extraction (any model's JSON, or your own)
  to exact page locations, fully offline.
- `extract()`: model read + grounding in one call; schema-free by
  default (the model names the fields), optional field lists for guided
  recall. Adapters: Gemini, OpenAI(-compatible), OpenRouter, DeepSeek,
  Ollama.
- Five-status contract per field: `verified` / `low_confidence` /
  `ambiguous` (all candidate locations returned) / `not_found` (the
  hallucination flag) / `not_present`. Never a silent guess.
- Verification proofs: IBAN/VAT checksums and totals arithmetic recorded
  in `proof` on the fields they confirm.
- PDF text-layer and CPU-only OCR intake (RapidOCR), HEIC/TIFF/photo
  support, EXIF-safe geometry on original-page coordinates.
- `result.overlay(png)` and `result.viewer(html)` outputs.
- The Lab (`paperpin lab`): local, token-guarded demo UI: drop a
  document, run a model or paste any model's JSON, see every pin and
  every flag. Session-scoped; zero telemetry.
- Synthetic end-to-end corpus and degraded-OCR gates in `fixtures/corpus`
  + `bench/`; synthetic demo invoice in `fixtures/demo` with a guard test
  that keeps the README screenshots honest.

[Unreleased]: https://github.com/Rjabov/paperpin/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Rjabov/paperpin/releases/tag/v0.1.0
