"""Performance gates.

Correctness gates say the answer is right; these say it still arrives. They
are built to survive a noisy CI runner: the scaling gate compares a run
against another run on the same machine, the work-count gates involve no
clock at all, and the one wall-clock budget carries an order of magnitude of
headroom over the measured baseline.

Baselines measured 2026-08-26 on the development machine. Like the
degraded-tier thresholds, they are raised only with a reason.
"""
import json
import time
from pathlib import Path

import pytest

from paperpin.align.tables import align_table
from test_tables import SPEC, item, table_rows

DEMO = Path(__file__).parent.parent / "fixtures" / "demo"

#: A doubling of line items currently costs ~3.9x — the quadratic cell matching
#: the README documents as a known limit. This gate does not demand the fix; it
#: refuses to let it get worse (cubic would be ~8x), and it will pass with an
#: order of magnitude to spare once the matching is made linear.
QUADRATIC_CEILING = 5.0


def _table(n: int):
    lines = [[f"ITEM{i:04d}", "DESC", f"{i % 9 + 1},000",
              f"{(i % 50) + 10},500", f"{i % 700 + 11},03"] for i in range(n)]
    items = [item(f"ITEM{i:04d} DESC", f"{i % 9 + 1},000",
                  f"{(i % 50) + 10},500", f"{i % 700 + 11},03") for i in range(n)]
    return table_rows(lines, y0=40, dy=12), items, {0: (600.0, 40 + n * 12 + 60)}


def _best_of(n: int, repeats: int = 2) -> float:
    """Fastest of a few runs. The minimum is the least noisy timing statistic:
    scheduling can only ever make a run slower."""
    rows, items, page = _table(n)
    return min(_time_once(rows, items, page) for _ in range(repeats))


def _time_once(rows, items, page) -> float:
    start = time.perf_counter()
    align_table("line_items", SPEC, items, rows, page)
    return time.perf_counter() - start


def test_table_matching_does_not_grow_worse_than_it_already_does():
    """Doubling the rows must not more than QUADRATIC_CEILING the time. A
    ratio measured against another run on the same machine survives a slow
    runner; an absolute second count would not."""
    small, large = _best_of(40), _best_of(80)
    ratio = large / small

    assert ratio <= QUADRATIC_CEILING, (
        f"line-item matching now costs {ratio:.1f}x per doubling "
        f"({small:.3f}s at 40 rows, {large:.3f}s at 80) — worse than the "
        f"quadratic baseline of ~3.9x this gate was set from")


# A one-page text-layer invoice is the common path; it has to stay cheap
# enough to run beside a working user.

@pytest.fixture(scope="module")
def profile():
    if not (DEMO / "demo_invoice.pdf").exists():
        pytest.skip("demo doc not generated")
    from paperpin import ground
    extraction = json.loads((DEMO / "demo_extraction.json").read_text("utf-8"))
    return ground(DEMO / "demo_invoice.pdf",
                  extraction=extraction).meta["profile"]


def test_grounding_stays_inside_a_generous_wall_clock_budget(profile):
    """Baseline 0.05s of pipeline time; the budget is 2s, so this fires on a
    real regression rather than on a busy runner."""
    assert profile["total_s"] < 2.0, profile


def test_the_pipeline_does_not_start_doing_more_work(profile):
    """No clock: a stage that suddenly emits far more segments or rows is a
    performance regression that shows up here identically on every machine,
    and long before the wall-clock budget notices."""
    assert profile["n_segments"] == pytest.approx(338, rel=0.25)
    assert profile["n_rows"] == pytest.approx(164, rel=0.25)
    assert profile["n_fields"] == 20


def test_alignment_stays_the_dominant_cost(profile):
    """Intake, geometry and verification are meant to be cheap on a text
    layer. If one of them starts rivalling alignment, something regressed into
    re-reading or re-rendering the page."""
    others = profile["intake_s"] + profile["geometry_s"] + profile["verify_s"]

    assert others < profile["align_s"], profile
