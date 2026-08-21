"""Line formation (§6.3 rewrite): rows must be true printed lines.

Real-doc regressions 2026-08-19: greedy expanding-band clustering chain-merged
~51 METRO lines into 24 mega-rows (one tall det box bridges two lines and the
grown band then swallows everything after), split skewed photo lines, and
smeared two-column layouts into one row.
"""
import math

from paperpin.align.rows import build_rows
from paperpin.types import Segment


def seg(text, x0, top, x1, bottom, quad=None):
    return Segment(text=text, x0=x0, top=top, x1=x1, bottom=bottom,
                   conf=1.0, page=0, quad=quad)


def full_lines(rows):
    return [r for r in rows if not getattr(r, "is_cell", False)]


def test_tall_bridge_segment_does_not_chain_merge_lines():
    # four clean lines + one tall det box bleeding over lines 1-2: the old
    # expanding band swallowed line 2 entirely and kept growing
    segments = [
        seg("Alpha", 40, 0, 120, 8), seg("11,00", 400, 0, 460, 8),
        seg("TALL", 200, 2, 260, 26),                      # fused det box
        seg("Beta", 40, 12, 120, 20), seg("22,00", 400, 12, 460, 20),
        seg("Gamma", 40, 24, 120, 32), seg("33,00", 400, 24, 460, 32),
        seg("Delta", 40, 36, 120, 44), seg("44,00", 400, 36, 460, 44),
    ]
    rows = full_lines(build_rows(segments))
    texts = [r.text for r in rows]
    alpha = next(t for t in texts if "Alpha" in t)
    assert "Beta" not in alpha, f"lines chain-merged: {alpha!r}"
    assert "Gamma" not in alpha
    # heights stay line-sized: no row spans 3 printed lines
    med_h = 8.0
    for r in rows:
        if "TALL" in r.text:
            continue  # the bridge segment itself is legitimately tall
        assert (r.bottom - r.top) <= 2.2 * med_h, f"mega-row: {r.text!r}"


def test_skewed_line_stays_one_row():
    # one printed line at ~2.3 degrees: left end y=0, right end y=20 — the
    # quads carry the tilt, so deskewed centers land on one baseline
    slope = 0.04
    segments = []
    for i, (text, x0, x1) in enumerate([("Gesamtbetrag", 40, 200),
                                        ("88,53", 300, 360),
                                        ("EUR", 400, 440)]):
        top = slope * x0
        bottom = top + 12
        q = [(x0, slope * x0), (x1, slope * x1),
             (x1, slope * x1 + 12), (x0, slope * x0 + 12)]
        segments.append(seg(text, x0, top, x1, bottom, quad=q))
    # a second real line well below, same tilt
    for text, x0, x1 in [("Netto", 40, 100), ("74,39", 300, 360)]:
        top = 40 + slope * x0
        q = [(x0, top), (x1, 40 + slope * x1),
             (x1, 40 + slope * x1 + 12), (x0, top + 12)]
        segments.append(seg(text, x0, top, x1, top + 12, quad=q))
    rows = full_lines(build_rows(segments))
    joined = [r.text for r in rows]
    line1 = next(t for t in joined if "Gesamtbetrag" in t)
    assert "88,53" in line1 and "EUR" in line1, f"skewed line split: {joined}"
    assert "Netto" not in line1


def test_two_column_row_yields_column_cells():
    # left column address + right column labels on the same baseline: the
    # full row still exists, but column cells become matchable rows too
    segments = [
        seg("Hlavna", 40, 100, 100, 112), seg("7", 106, 100, 112, 112),
        seg("Objednavka:", 400, 100, 500, 112), seg("0308", 506, 100, 546, 112),
    ]
    rows = build_rows(segments)
    cells = [r for r in rows if getattr(r, "is_cell", False)]
    cell_texts = [r.text for r in cells]
    assert any(t == "Hlavna 7" for t in cell_texts), f"no left cell: {cell_texts}"
    assert any("Objednavka" in t and "Hlavna" not in t for t in cell_texts)
    # the full line is still there for cross-column matching
    assert any("Hlavna" in r.text and "0308" in r.text for r in full_lines(rows))


def _rot_to_original(k, w_orig, h_orig, x, y):
    """Invert the original->processed rotate90 mapping: given a point in the
    PROCESSED (upright) frame, return its original-space location."""
    if k == 1:   # forward: (x,y)->(y, w-x)
        return (w_orig - y, x)
    if k == 2:   # forward: (x,y)->(w-x, h-y)
        return (w_orig - x, h_orig - y)
    if k == 3:   # forward: (x,y)->(h-y, x)
        return (y, h_orig - x)
    return (x, y)


def _rotated_page_segments(k, w_orig, h_orig):
    # two clean lines laid out in the UPRIGHT frame, then placed on the
    # original page as the orientation search would find them
    upright = [
        ("MwSt", 40, 100, 90, 112), ("19", 300, 100, 320, 112),
        ("Total", 40, 400, 90, 412), ("146,14", 300, 400, 360, 412),
    ]
    segs = []
    for text, x0, top, x1, bottom in upright:
        corners = [_rot_to_original(k, w_orig, h_orig, x, y)
                   for x, y in ((x0, top), (x1, top), (x1, bottom), (x0, bottom))]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        segs.append(seg(text, min(xs), min(ys), max(xs), max(ys)))
    return segs


def test_rotated_page_rows_follow_orientation():
    # rot270 regression (degradation gate): on a sideways page the printed
    # lines run vertically in original space — rows must be built in the
    # orientation-normalized frame or lines fuse and anchors endorse wrong pins
    for k in (1, 3):
        w_orig, h_orig = 800, 600  # original page is landscape
        segs = _rotated_page_segments(k, w_orig, h_orig)
        rows = full_lines(build_rows(segs, orientations={0: k},
                                     page_sizes={0: (w_orig, h_orig)}))
        texts = [r.text for r in rows]
        assert "MwSt 19" in texts, f"k={k}: {texts}"
        assert "Total 146,14" in texts, f"k={k}: {texts}"


def test_textlayer_two_lines_unchanged():
    segments = [
        seg("Invoice", 40, 40, 100, 55), seg("20260461", 140, 40, 220, 55),
        seg("Total", 380, 700, 430, 715), seg("146,14", 480, 700, 540, 715),
    ]
    rows = full_lines(build_rows(segments))
    assert len(rows) == 2
    assert rows[0].text == "Invoice 20260461"
    assert rows[1].text == "Total 146,14"


def test_ctc_collapse_keeps_emission_timesteps():
    import numpy as np
    from paperpin.backends.rapidocr_backend import _ctc_collapse
    #                blank blank 'a'  'a'  blank 'b'
    idxs = np.array([0,    0,    5,   5,   0,    7])
    probs = np.array([.9,  .9,   .8,  .7,  .9,   .6])
    out = _ctc_collapse(idxs, probs)
    assert out == [(5, 2, pytest_approx(.8)), (7, 5, pytest_approx(.6))] or \
           [(c, t) for c, t, _ in out] == [(5, 2), (7, 5)]


def pytest_approx(x):
    return x


def test_quad_slice_upright_and_rotated():
    from paperpin.backends.rapidocr_backend import _char_slices
    upright = [(100.0, 50.0), (200.0, 50.0), (200.0, 70.0), (100.0, 70.0)]
    (box,) = _char_slices(upright, [(0.5, 0.75)])
    assert box == (150.0, 50.0, 175.0, 70.0)
    # 90°-ish rotated quad (point order encodes the frame): slice follows
    # the tl->tr reading edge, not the axis-aligned bbox
    rot = [(50.0, 200.0), (50.0, 100.0), (70.0, 100.0), (70.0, 200.0)]
    (sl,) = _char_slices(rot, [(0.0, 0.5)])
    x0, y0, x1, y1 = sl
    assert y0 == 150.0 and y1 == 200.0 and x0 == 50.0 and x1 == 70.0


def test_staircase_of_tall_boxes_groups_by_seed_band():
    # METRO chain-merge lock: grouping references the FIRST member of a line
    # (fixed seed), so a staircase of overlapping tall boxes cannot chain
    # into one mega-row. With a growing band the twelve segments collapse
    # into a single row.
    segments = [Segment(text=f"L{i}", x0=10.0 + 30 * i, top=float(4 * i),
                        x1=35.0 + 30 * i, bottom=float(4 * i + 8), conf=1.0)
                for i in range(12)]
    rows = build_rows(segments)
    assert len(rows) == 6, [r.text for r in rows]


def test_char_slice_touching_segment_edges_uses_det_bounds():
    # Kaufland photo regression (2026-08-21): CTC char boxes start inside the
    # det box, shaving the leading '1' off a full-segment match's bbox.
    from paperpin.align.rows import _slice_segment
    from paperpin.types import Segment
    seg = Segment(text="106,96", x0=1557.0, top=10.0, x1=1742.0, bottom=40.0,
                  char_boxes=[(1576, 12, 1600, 38), (1602, 12, 1625, 38),
                              (1627, 12, 1650, 38), (1652, 12, 1665, 38),
                              (1667, 12, 1690, 38), (1692, 12, 1724, 38)])
    full = _slice_segment(seg, 0, 6)
    assert full[0] == 1557.0 and full[2] == 1742.0
    inner = _slice_segment(seg, 1, 5)  # interior slice keeps char precision
    assert inner[0] == 1602 and inner[2] == 1690
