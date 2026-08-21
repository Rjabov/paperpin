# Examples

Every example runs against the synthetic demo invoice in
[`fixtures/demo/`](../fixtures/demo/), so 01 and 03 to 05 work offline
with no API key. Run them from the repo root.

| file | shows |
|---|---|
| [01_ground_any_json.py](01_ground_any_json.py) | the core move: ground any existing JSON, catch the fabricated field, save/overlay/viewer outputs |
| [02_extract_with_a_model.py](02_extract_with_a_model.py) | model extraction (schema-free by default), prompt steering, timeouts, token usage, page truncation |
| [03_schemas.py](03_schemas.py) | presets, custom field lists, every FieldSpec knob: types, anchors, checksums, arithmetic proofs, aliases, tables |
| [04_reading_results.py](04_reading_results.py) | the full result surface: statuses, bbox to pixels, ambiguous candidates, proofs, a triage pattern, JSON round-trip |
| [05_options_and_cli.py](05_options_and_cli.py) | backend/cache/progress knobs, env vars, OpenAI-compatible endpoints, the whole CLI |
| [06_from_node.mjs](06_from_node.mjs) | using paperpin from Node/React: spawn the CLI, read the JSON, position pins from normalized boxes |

The complete parameter reference lives in the README's [Every knob](../README.md#every-knob) section.
