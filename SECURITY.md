# Security

## Reporting a vulnerability

Please use **GitHub's private vulnerability reporting** for this
repository (Security tab → "Report a vulnerability"). Reports go straight
to the maintainer, privately. Please do not open a public issue for
anything exploitable.

You can expect an acknowledgement within a few days. Once a fix ships,
the report and credit (if you want it) appear in the release notes.

## Scope worth knowing

- paperpin's core never makes network calls. Model adapters call only the
  provider you explicitly configured; API keys are passed through, never
  stored or logged.
- The Lab binds to localhost and requires a per-start token for every API
  call. Treat it as a local tool, not a deployable service.
- OCR text is cached locally under `~/.paperpin/cache/`. If you process
  sensitive documents, that directory is yours to protect or purge.
- Documents are parsed with `pdfplumber`/`pypdfium2` and Pillow. Opening
  untrusted files inherits those parsers' risk surface; keep the
  dependencies current.
