"""CLI contract: what lands on stdout, what lands on stderr.

`-o -` exists so other languages can pipe paperpin without a temp file, which
only works if stdout carries the result JSON and absolutely nothing else.
"""
import json
from pathlib import Path

import pytest

from paperpin.cli import main

DEMO = Path(__file__).parent.parent / "fixtures" / "demo"

pytestmark = pytest.mark.skipif(not (DEMO / "demo_invoice.pdf").exists(),
                                reason="demo doc not generated")


def _ground_argv(*extra):
    return ["ground", str(DEMO / "demo_invoice.pdf"),
            "--extraction", str(DEMO / "demo_extraction.json"), *extra]


def test_dash_out_puts_only_json_on_stdout(capsys):
    assert main(_ground_argv("-o", "-")) == 0
    captured = capsys.readouterr()

    payload = json.loads(captured.out)  # parses, so nothing else is in there
    assert payload["fields"]["approved_by"]["status"] == "not_found"
    assert captured.out.endswith("\n")


def test_dash_out_moves_the_summary_to_stderr(capsys):
    assert main(_ground_argv("-o", "-")) == 0
    captured = capsys.readouterr()

    assert "located" in captured.err
    assert "approved_by" in captured.err
    assert "located" not in captured.out


def test_quiet_drops_the_summary_but_keeps_the_json(capsys):
    assert main(_ground_argv("-o", "-", "--quiet")) == 0
    captured = capsys.readouterr()

    assert captured.err == ""
    assert json.loads(captured.out)["summary"]["not_found"] == 1


def test_file_output_still_reports_on_stdout(capsys, tmp_path):
    out = tmp_path / "result.json"
    assert main(_ground_argv("-o", str(out))) == 0
    captured = capsys.readouterr()

    assert f"saved → {out}" in captured.out
    assert "located" in captured.out
    assert json.loads(out.read_text("utf-8"))["fields"]["approved_by"]["status"] == "not_found"


def test_to_json_matches_what_save_writes(tmp_path):
    from paperpin import ground

    result = ground(DEMO / "demo_invoice.pdf",
                    extraction=json.loads((DEMO / "demo_extraction.json").read_text("utf-8")))
    out = tmp_path / "result.json"
    result.save(out)

    assert result.to_json() == out.read_text("utf-8")
