# Contributing

Thank you for looking under the hood.

## The fastest way to help

**Open an issue with a document where a status lied.** A `verified` that
points at the wrong place, a `not_found` for a value that is plainly on
the page, a box that drifts: these are the bugs that matter. Use the bug
template; it asks for the paperpin version, the document kind, and what
each field returned. **Never attach a real person's or company's
document**. Redact it, or reproduce the failure on a synthetic one
(`fixtures/demo/demo_invoice.py` shows how we build those).

Feature ideas are welcome too. Statuses are the contract: anything that
would make paperpin guess silently instead of saying `not_found` /
`ambiguous` out loud is out of scope by design.

## Pull requests

Please open an issue first so the approach can be agreed before you spend
an evening on it. Small, focused PRs merge fast; drive-by refactors don't.

## Running the tests

```bash
pip install -e ".[full,dev]"
pytest -q                 # fast gate, must stay green
pytest -q -m slow         # degraded gate: OCR degradation matrix (slower)
pytest -q --cov           # with the coverage floor CI enforces
```

Every test belongs to exactly one layer, so you can run just one:

```bash
pytest -q -m unit         # deterministic, hand-built input
pytest -q -m integration  # a real document through the pipeline, or the HTTP stack
pytest -q -m contract     # result JSON, CLI I/O, goldens, examples
pytest -q -m e2e          # graded corpus gates
pytest -q -m security     # each test pins a demonstrated fix
```

The layers live in one mapping in `tests/conftest.py`; a new test module
that is not in it fails collection rather than quietly belonging to
nothing.

Coverage is enforced **twice**, over the data from every CI job combined
(the matrix and the OCR gates both publish theirs — OCR-only code reads
35% from the fast suite and 79% from the degraded one, and counting only
the first made tested code look untested):

- the **core engine** — align, verify, geometry, intake, outputs, types,
  schemas, api — is held to a higher floor than the package average, so a
  gain in the periphery can never hide the core rotting;
- the **whole package** has its own floor.

Both ratchet up as gaps close. Lowering either is something to argue for
in a PR, not a fix for a red build. Locally `pytest --cov` checks the
package floor only, against the fast suite; the core floor lives in
`.github/workflows/ci.yml` alongside the combine step.

Both gates green = safe to push. The fast gate runs the synthetic corpus
end-to-end (`fixtures/corpus`), so matcher and schema changes are
exercised on every run.

`fixtures/golden/` holds committed samples of exactly what the engine
emits, down to every bbox, so an accidental drift shows up as a diff. When
a change moves them on purpose:

```bash
PAPERPIN_UPDATE_GOLDEN=1 pytest tests/test_golden_result.py
git diff fixtures/golden      # read this before committing it
```

For the Lab frontend:

```bash
cd lab/web && npm ci && npm run build
```

## Code shape

- Python ≥ 3.10, no new runtime dependencies without discussion.
- The core stays domain-free: document-type guessing lives only in
  schema enrichment, never in intake/align/verify.
- Every behavior change comes with a test that fails without it.
- Laws that must hold for *every* input — a bbox surviving a transform
  chain, a checksum catching a typo — belong in `tests/test_properties.py`
  as Hypothesis properties, not as three hand-picked examples.
