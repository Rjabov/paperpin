## What & why

<!-- One or two sentences. Link the issue this was discussed in. -->

Closes #

## Checklist

- [ ] An issue exists and the approach was agreed there
- [ ] `pytest -q` green (fast gate)
- [ ] `pytest -q -m slow` green if intake/OCR/matcher changed (degraded gate)
- [ ] New behavior has a test that fails without the change
- [ ] No real personal/company documents in fixtures or the diff
