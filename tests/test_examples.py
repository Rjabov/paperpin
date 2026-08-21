"""The offline examples must stay runnable: they are executable docs.
Each runs in a scratch cwd with the demo fixtures mirrored in, so the
artifacts they write (run.json, run.png, ...) never land in the repo."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
OFFLINE = ["01_ground_any_json.py", "03_schemas.py",
           "04_reading_results.py", "05_options_and_cli.py"]

pytestmark = pytest.mark.skipif(not (ROOT / "examples").exists(),
                                reason="examples not present")


# Interactive consoles are UTF-8-capable even on Windows, but subprocess
# pipes there default to cp1252, which cannot carry the demo doc's ² or č.
# Explicit UTF-8 pipes model the real console.
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


@pytest.mark.parametrize("name", OFFLINE)
def test_example_runs_offline(name, tmp_path):
    shutil.copytree(ROOT / "fixtures" / "demo", tmp_path / "fixtures" / "demo")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "examples" / name)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
        encoding="utf-8", env=ENV,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_node_example_runs(tmp_path):
    shutil.copytree(ROOT / "fixtures" / "demo", tmp_path / "fixtures" / "demo")
    env = {**ENV, "PATH": str(Path(sys.executable).parent) + os.pathsep
           + os.environ["PATH"]}
    proc = subprocess.run(
        ["node", str(ROOT / "examples" / "06_from_node.mjs")],
        cwd=tmp_path, capture_output=True, text=True, timeout=120, env=env,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
