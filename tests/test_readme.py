"""The README is the PyPI front page.

GitHub resolves relative links; PyPI does not. `hatch-fancy-pypi-readme`
rewrites them at build time from a pattern list in `pyproject.toml`, and a new
link to a directory nobody added to that list renders as a 404 on the page
every visitor sees first. This applies the project's own configured
substitutions and fails if anything relative survives.
"""
import re
from pathlib import Path

import pytest

tomllib = pytest.importorskip("tomllib", reason="stdlib TOML parsing is 3.11+")

ROOT = Path(__file__).parent.parent

pytestmark = pytest.mark.skipif(not (ROOT / "pyproject.toml").exists(),
                                reason="running outside the repo")


def rendered_readme() -> str:
    """README.md with the substitutions the build actually applies."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    hook = config["tool"]["hatch"]["metadata"]["hooks"]["fancy-pypi-readme"]
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for rule in hook["substitutions"]:
        text = re.sub(rule["pattern"], rule["replacement"], text)
    return text


def test_no_link_stays_relative_on_the_pypi_page():
    relative = [target for target in re.findall(r"\]\(([^)]+)\)", rendered_readme())
                if not target.startswith(("http://", "https://", "#", "mailto:"))]

    assert not relative, (
        f"these README links render as 404s on PyPI: {relative} — add their "
        "prefix to the fancy-pypi-readme substitution list in pyproject.toml")


def test_no_image_stays_relative_on_the_pypi_page():
    relative = [src for src in re.findall(r'src="([^"]+)"', rendered_readme())
                if not src.startswith(("http://", "https://", "data:"))]

    assert not relative, f"these README images render broken on PyPI: {relative}"
