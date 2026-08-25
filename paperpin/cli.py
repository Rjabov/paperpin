"""paperpin CLI (§4.2). Import stays light: heavy modules load per-command."""
from __future__ import annotations

import argparse
import json
import sys


def main(argv=None) -> int:
    # Legacy Windows consoles decode as cp1252; the status glyphs (✓ → ⚠)
    # would crash every print. Degrade unencodable characters instead.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    parser = argparse.ArgumentParser(
        prog="paperpin",
        description="Pin every extracted value to the exact spot on the page "
                    "it came from — and flag the ones that aren't there.")
    sub = parser.add_subparsers(dest="command")

    p_ground = sub.add_parser("ground", help="ground an existing extraction JSON")
    p_ground.add_argument("file")
    p_ground.add_argument("--extraction", required=True,
                          help="JSON file (or inline JSON) with field values")
    p_ground.add_argument("--schema", default=None,
                          help="preset name (invoice, receipt) or JSON schema file")
    p_ground.add_argument("--backend", default="auto")
    p_ground.add_argument("--no-cache", action="store_true")
    p_ground.add_argument("-o", "--out", default=None,
                          help="output JSON path, or - for stdout")
    p_ground.add_argument("--quiet", action="store_true",
                          help="suppress the field summary")
    p_ground.add_argument("--overlay", default=None, help="also render overlay PNG")
    p_ground.add_argument("--view", default=None, help="also render HTML viewer")

    p_extract = sub.add_parser("extract", help="extract with a model, then ground")
    p_extract.add_argument("file")
    p_extract.add_argument("--schema", default=None,
                           help="preset name (invoice, receipt) or JSON schema "
                                "file; default: infer from field names")
    p_extract.add_argument("--model", default="byo",
                           help="byo | gemini/<model> | openai/<model> | ollama/<model>")
    p_extract.add_argument("--extraction", default=None,
                           help="required for --model byo: existing JSON to ground")
    p_extract.add_argument("--prompt", default=None,
                           help="extra instructions appended to the base prompt")
    p_extract.add_argument("--backend", default="auto")
    p_extract.add_argument("--no-cache", action="store_true")
    p_extract.add_argument("-o", "--out", default=None,
                           help="output JSON path, or - for stdout")
    p_extract.add_argument("--quiet", action="store_true",
                           help="suppress the field summary")
    p_extract.add_argument("--overlay", default=None)
    p_extract.add_argument("--view", default=None)

    p_overlay = sub.add_parser("overlay", help="render overlay PNG from a result JSON")
    p_overlay.add_argument("file", help="the original document")
    p_overlay.add_argument("result", help="paperpin result JSON")
    p_overlay.add_argument("-o", "--out", default="proof.png")
    p_overlay.add_argument("--page", type=int, default=None)

    p_view = sub.add_parser("view", help="render self-contained HTML viewer")
    p_view.add_argument("file", help="the original document")
    p_view.add_argument("result", help="paperpin result JSON")
    p_view.add_argument("-o", "--out", default="proof.html")

    p_pages = sub.add_parser("pages", help="write page rasters for a viewer")
    p_pages.add_argument("file", help="the original document")
    p_pages.add_argument("-o", "--out", default="pages",
                         help="output directory (created if missing)")
    p_pages.add_argument("--width", type=int, default=None,
                         help="scale each page to this pixel width")
    p_pages.add_argument("--page", type=int, default=None,
                         help="only this page (0-based)")
    p_pages.add_argument("--format", default="png", choices=("png", "jpg"),
                         help="png (lossless) or jpg (small, for scans)")

    p_lab = sub.add_parser("lab", help="start the local Lab web app")
    p_lab.add_argument("--port", type=int, default=8377)
    p_lab.add_argument("--no-browser", action="store_true")

    sub.add_parser("version", help="print version")
    from .types import _version
    parser.add_argument("--version", action="version", version=_version())

    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 1
    from .env import load_dotenv
    load_dotenv()
    commands = {"ground": _cmd_ground, "extract": _cmd_extract,
                "overlay": _cmd_overlay, "view": _cmd_view,
                "pages": _cmd_pages, "lab": _cmd_lab,
                "version": _cmd_version}
    try:
        return commands[args.command](args)
    except (ValueError, FileNotFoundError, RuntimeError, ImportError,
            OSError, IndexError, KeyError, AttributeError, TypeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


def _print_summary(result, stream) -> None:
    from .types import Status
    icons = {Status.VERIFIED: "✓", Status.LOW_CONFIDENCE: "~",
             Status.AMBIGUOUS: "?", Status.NOT_FOUND: "✗", Status.NOT_PRESENT: "·"}
    for f in result:
        icon = icons.get(f.status, " ")
        loc = f"p{f.page + 1}" if f.page is not None else "--"
        line = f" {icon} {f.name:<24} {str(f.value)[:36]:<38} {f.status.value:<15} {loc}"
        print(line, file=stream)
        for note in f.notes:
            if note.startswith("⚠"):
                print(f"      {note}", file=stream)
    counts = result.counts()
    total_located = counts.get("verified", 0) + counts.get("low_confidence", 0)
    print(f"\n {total_located} located · " +
          " · ".join(f"{v} {k}" for k, v in sorted(counts.items())), file=stream)
    if counts.get("not_found"):
        print(" ⚠ NOT FOUND fields are values the model asserted that match "
              "nothing on the document.", file=stream)


def _finish(result, out, overlay, view, quiet: bool = False) -> int:
    # `-o -` makes stdout the result JSON, so every human-facing line moves
    # to stderr: `paperpin ground … -o - | jq` must receive JSON and nothing
    # else, while the person watching still sees the summary.
    piping = out == "-"
    chatter = sys.stderr if piping else sys.stdout
    if piping:
        sys.stdout.write(result.to_json() + "\n")
    elif out:
        result.save(out)
        print(f" saved → {out}", file=chatter)
    if overlay:
        result.overlay(overlay)
        print(f" overlay → {overlay}", file=chatter)
    if view:
        result.viewer(view)
        print(f" viewer → {view}", file=chatter)
    if not quiet:
        _print_summary(result, chatter)
    return 0


def _cmd_ground(args) -> int:
    from .api import ground
    result = ground(args.file, extraction=args.extraction, schema=args.schema,
                    backend=args.backend, use_cache=not args.no_cache)
    return _finish(result, args.out, args.overlay, args.view, args.quiet)


def _cmd_extract(args) -> int:
    from .api import extract
    result = extract(args.file, schema=args.schema, model=args.model,
                     prompt=args.prompt, extraction=args.extraction,
                     backend=args.backend, use_cache=not args.no_cache)
    return _finish(result, args.out, args.overlay, args.view, args.quiet)


def _load_result_json(doc_path: str, result_path: str):
    """Rebuild a renderable result from saved JSON + the original document."""
    from .intake.loader import load_document
    from .types import GroundResult

    with open(result_path, encoding="utf-8") as fh:
        data = json.load(fh)
    doc = load_document(doc_path)
    return GroundResult.from_dict(
        data, source=doc_path,
        meta={"_document": doc,
              "_page_image_provider": lambda i: doc.pages[i].raster()})


def _cmd_overlay(args) -> int:
    result = _load_result_json(args.file, args.result)
    from .outputs.overlay import render_overlay
    render_overlay(result, args.out, page=args.page)
    print(f" overlay → {args.out}")
    return 0


def _cmd_view(args) -> int:
    result = _load_result_json(args.file, args.result)
    from .outputs.viewer import render_viewer
    render_viewer(result, args.out)
    print(f" viewer → {args.out}")
    return 0


def _cmd_pages(args) -> int:
    """Page rasters as files — the pixels a JS/HTML viewer draws boxes on.
    Boxes are normalized, so any --width renders them correctly."""
    from pathlib import Path

    from .intake.loader import load_document
    from .outputs.common import fit_width

    doc = load_document(args.file)
    if args.page is not None and not 0 <= args.page < len(doc.pages):
        raise IndexError(f"no page {args.page} — {args.file} has "
                         f"{len(doc.pages)} page(s)")
    wanted = [args.page] if args.page is not None else range(len(doc.pages))

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for idx in wanted:
        image = fit_width(doc.pages[idx].raster(), args.width)
        path = out_dir / f"page-{idx}.{args.format}"
        image.convert("RGB").save(path)
        print(f" page {idx} → {path}  ({image.width}x{image.height})")
    return 0


def _cmd_lab(args) -> int:
    if not (0 < args.port <= 65535):
        print(f"error: --port must be 1-65535, got {args.port}",
              file=__import__("sys").stderr)
        return 1
    try:
        import uvicorn
    except ImportError:
        print("the Lab needs extras — install with: pip install paperpin[lab]")
        return 1
    import importlib.util
    if importlib.util.find_spec("lab") is None:
        print("the Lab demo app ships with the repository, not the wheel — \n"
              "clone https://github.com/Rjabov/paperpin and run from the repo root")
        return 1
    # the token gates the API against other local processes; it only ever
    # travels through this printed URL, never a file or the network
    from lab.server.app import LAB_TOKEN
    url = f"http://127.0.0.1:{args.port}/?token={LAB_TOKEN}"
    print(f" paperpin lab → {url}  (local only, zero telemetry)")
    if not args.no_browser:
        import threading, webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    uvicorn.run("lab.server.app:app", host="127.0.0.1", port=args.port,
                log_level="warning")
    return 0


def _cmd_version(args) -> int:
    from .types import _version
    print(_version())
    return 0


if __name__ == "__main__":
    sys.exit(main())
