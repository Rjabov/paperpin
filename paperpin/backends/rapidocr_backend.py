"""RapidOCR backend — the default OCR engine (bundled ONNX PP-OCR models).

Proven in the prototype: full marks on a crumpled sideways phone photo,
including the white-on-blue IBAN block and marker-highlighted text.
Supports both the classic `rapidocr_onnxruntime` package and the newer
`rapidocr` 2.x package.
"""
from __future__ import annotations

import os
import threading
from typing import Any, Optional

import numpy as np
from PIL import Image

from ..types import Segment

_ENGINE: Optional[tuple[Any, str]] = None  # (engine, "classic" | "v2")
_CROP_ENGINE: Optional[Any] = None
_ENGINE_LOCK = threading.Lock()


def _ocr_thread_cap() -> Optional[int]:
    """PAPERPIN_OCR_THREADS: cap onnxruntime intra-op threads so grounding can
    run beside a working user (1 thread ~2x slower, machine stays responsive).
    Unset/0/garbage -> None (no cap, full speed)."""
    raw = os.environ.get("PAPERPIN_OCR_THREADS", "")
    try:
        n = int(raw)
    except ValueError:
        return None
    # clamp: 5000 hangs thread-pool construction, 2**31 raises a raw
    # TypeError inside onnxruntime — junk must degrade, not crash
    return min(n, 256) if n > 0 else None


def _capped_session_options(threads: int):
    import onnxruntime as ort

    class CappedSessionOptions(ort.SessionOptions):
        def __init__(self):
            super().__init__()
            self.intra_op_num_threads = threads

    return CappedSessionOptions


def _apply_thread_cap() -> None:
    """rapidocr-onnxruntime 1.2.x accepts intra_op_num_threads but never puts
    it into its SessionOptions — replace the symbol it constructs instead.
    Guarded like every other 1.2.x internal pin: on any drift, OCR just runs
    uncapped."""
    cap = _ocr_thread_cap()
    if cap is None:
        return
    try:
        from rapidocr_onnxruntime import utils as _rutils
        _rutils.SessionOptions = _capped_session_options(cap)
    except Exception:
        pass


def _load_engine() -> tuple[Any, str]:
    """Engine and its style as ONE value — a second thread must never see a
    constructed engine paired with an unset style (that combination read
    whole pages as empty)."""
    global _ENGINE
    if _ENGINE is not None:
        return _ENGINE
    with _ENGINE_LOCK:
        if _ENGINE is not None:
            return _ENGINE
        threads = _ocr_thread_cap() or os.cpu_count() or 2
        try:
            from rapidocr_onnxruntime import RapidOCR  # classic package
            _apply_thread_cap()
            _ENGINE = (RapidOCR(intra_op_num_threads=threads), "classic")
        except ImportError:
            try:
                from rapidocr import RapidOCR  # 2.x package
                _ENGINE = (RapidOCR(), "v2")
            except ImportError as e:
                raise ImportError(
                    "OCR needs RapidOCR — install with: pip install paperpin[ocr]"
                ) from e
        return _ENGINE


def _ctc_collapse(idxs, probs) -> list[tuple[int, int, float]]:
    """CTC greedy collapse keeping emission timesteps: (char_index, t, prob)."""
    out: list[tuple[int, int, float]] = []
    for t, ci in enumerate(idxs):
        if ci == 0 or (t > 0 and idxs[t - 1] == ci):
            continue
        out.append((int(ci), t, float(probs[t])))
    return out


def _char_slices(quad: list[tuple[float, float]],
                 fracs: list[tuple[float, float]]
                 ) -> list[tuple[float, float, float, float]]:
    """Axis-aligned boxes of the quad's [f0..f1] slices along its READING
    edge. rapidocr rot90s any crop with height/width >= 1.5 before
    recognition, so for tall quads the CTC timesteps run down the left
    edge (tl->bl), not across the top — slicing tl->tr there produced
    full-height slivers at the wrong end of the text."""
    (tlx, tly), (trx, try_), (brx, bry), (blx, bly) = quad
    # mirror rapidocr's get_rotate_crop_image: crop width/height are the
    # p0->p1 and p0->p3 EDGE lengths (the quad's point order encodes its
    # own reading frame), and the rot90 kicks in at ratio 1.5
    w = ((trx - tlx) ** 2 + (try_ - tly) ** 2) ** 0.5
    h = ((blx - tlx) ** 2 + (bly - tly) ** 2) ** 0.5
    tall = w > 0 and h / max(w, 1e-6) >= 1.5
    out = []
    for f0, f1 in fracs:
        if tall:  # reading axis runs down the quad
            pts = [(tlx + (blx - tlx) * f, tly + (bly - tly) * f) for f in (f0, f1)]
            pts += [(trx + (brx - trx) * f, try_ + (bry - try_) * f) for f in (f0, f1)]
        else:
            pts = [(tlx + (trx - tlx) * f, tly + (try_ - tly) * f) for f in (f0, f1)]
            pts += [(blx + (brx - blx) * f, bly + (bry - bly) * f) for f in (f0, f1)]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    return out


def _rec_batch_with_positions(rec, crops: list) -> list:
    """The TextRecognizer batch loop, capturing per-char x-fraction spans
    (fractions of each crop's own width; right-side batch padding excluded)."""
    if not crops:
        return []
    width_list = [c.shape[1] / float(c.shape[0]) for c in crops]
    indices = np.argsort(np.array(width_list))
    results: list = [None] * len(crops)
    batch_num = rec.rec_batch_num
    for beg in range(0, len(crops), batch_num):
        end = min(len(crops), beg + batch_num)
        max_ratio = 0.0
        for i in range(beg, end):
            h, w = crops[indices[i]].shape[:2]
            max_ratio = max(max_ratio, w / h)
        batch = [rec.resize_norm_img(crops[indices[i]], max_ratio)
                 for i in range(beg, end)]
        preds = rec.session(np.asarray(batch).astype(np.float32))[0]
        for bi, i in enumerate(range(beg, end)):
            pred = preds[bi]
            emitted = _ctc_collapse(pred.argmax(axis=1), pred.max(axis=1))
            T = pred.shape[0]
            h, w = crops[indices[i]].shape[:2]
            valid = min(1.0, (w / h) / max(max_ratio, 1e-6))
            chars, fracs, confs = [], [], []
            for ci, t, p in emitted:
                chars.append(rec.postprocess_op.character[ci])
                fracs.append((min(1.0, (t / T) / valid),
                              min(1.0, ((t + 1) / T) / valid)))
                confs.append(p)
            text = "".join(chars)
            conf = float(np.mean(confs)) if confs else 0.0
            results[indices[i]] = (text, conf, fracs)
    return results


class RapidOcrBackend:
    name = "rapidocr"

    def flipped_majority(self, image: Image.Image, max_lines: int = 12
                         ) -> Optional[bool]:
        """True when most detected lines read 180°-flipped in this image.

        The rec model reads upside-down text with near-identical confidence
        (its internal angle classifier silently un-flips each line), so an
        orientation search scoring recognition output cannot tell 0 from 180 —
        a rotated page then gets a chain that reverses every character box.
        The angle classifier itself is the purpose-built discriminator; ask it
        directly. Returns None when the engine internals are unavailable."""
        try:
            engine, style = _load_engine()
            if style != "classic":
                return None
            arr = np.asarray(image.convert("RGB"))[:, :, ::-1]
            dt_boxes, _ = engine.text_detector(arr)
            if dt_boxes is None or len(dt_boxes) == 0:
                return None
            crops = engine.get_crop_img_list(arr, engine.sorted_boxes(dt_boxes)[:max_lines])
            _imgs, cls_res, _ = engine.text_cls(crops)
            if not cls_res:
                return None
            # only votes the classifier itself trusts — the same gate the
            # recognition loop applies; a 180-flip decision reverses every
            # per-char box on the page
            thresh = getattr(engine.text_cls, "cls_thresh", 0.9)
            flipped = sum(1 for label, score in cls_res
                          if "180" in str(label) and score > thresh)
            confident = sum(1 for _label, score in cls_res if score > thresh)
            return confident > 0 and flipped * 2 > confident
        except Exception:
            return None

    def recognize_line(self, image: Image.Image) -> list[Segment]:
        """Crop re-read path: the crop comes from an orientation-normalized
        page, so the per-line 180° classifier is dead weight — a dedicated
        no-cls engine cuts the per-crop cost (det already self-skips on
        short crops via rapidocr's min_height)."""
        global _CROP_ENGINE
        _engine, style = _load_engine()
        if style != "classic":
            return self.recognize(image)
        if _CROP_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR
            with _ENGINE_LOCK:
                if _CROP_ENGINE is None:
                    # the crop IS a single printed line — running detection on
                    # it is the expensive part of every re-read (photo crops
                    # exceed the min_height det skip); rec-only reads the
                    # whole crop directly
                    _CROP_ENGINE = RapidOCR(
                        intra_op_num_threads=_ocr_thread_cap() or os.cpu_count() or 2,
                        use_angle_cls=False, use_text_det=False)
        arr = np.asarray(image.convert("RGB"))[:, :, ::-1]
        result, _elapse = _CROP_ENGINE(arr)
        return self._to_segments(result or [])

    def recognize(self, image: Image.Image) -> list[Segment]:
        engine, style = _load_engine()
        arr = np.asarray(image.convert("RGB"))[:, :, ::-1]  # PIL RGB -> BGR
        if style == "classic":
            segments = self._recognize_with_char_boxes(engine, arr)
            if segments is not None:
                return segments
            result, _elapse = engine(arr)
            rows = result or []
        else:
            out = engine(arr)
            rows = []
            if out is not None and getattr(out, "boxes", None) is not None:
                for box, txt, score in zip(out.boxes, out.txts, out.scores):
                    rows.append([box, txt, score])
        return self._to_segments(rows)

    def _recognize_with_char_boxes(self, engine, arr) -> Optional[list[Segment]]:
        """The engine's own det→cls→rec loop, run piecewise so the rec model's
        CTC timesteps yield true per-char x-positions (§6.4b) — ~4× tighter
        sub-boxes than proportional slicing. Falls back to the stock pipeline
        (returns None) if the engine's internals ever change shape."""
        try:
            h, w = arr.shape[:2]
            use_limit_ratio = (engine.width_height_ratio != -1
                               and w / h > engine.width_height_ratio)
            if not engine.use_text_det or h <= engine.min_height or use_limit_ratio:
                dt_boxes, crops = engine.get_boxes_img_without_det(arr, h, w)
            else:
                dt_boxes, _ = engine.text_detector(arr)
                if dt_boxes is None or len(dt_boxes) == 0:
                    return []
                dt_boxes = engine.sorted_boxes(dt_boxes)
                crops = engine.get_crop_img_list(arr, dt_boxes)
            flipped = [False] * len(crops)
            if engine.use_angle_cls:
                cls = engine.text_cls
                crops, cls_res, _ = cls(crops)
                for i, (label, score) in enumerate(cls_res):
                    if "180" in str(label) and score > cls.cls_thresh:
                        flipped[i] = True
            recs = _rec_batch_with_positions(engine.text_recognizer, crops)
        except Exception:
            return None
        segments: list[Segment] = []
        for quad_raw, flip, rec in zip(dt_boxes, flipped, recs):
            if rec is None:
                continue
            text, conf, fracs = rec
            if not text or not str(text).strip() or conf < engine.text_score:
                continue
            quad = [(float(p[0]), float(p[1])) for p in quad_raw]
            if flip:  # positions refer to the 180°-rotated crop
                fracs = [(1.0 - f1, 1.0 - f0) for f0, f1 in fracs]
            char_boxes = _char_slices(quad, fracs)
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            segments.append(Segment(
                text=text, x0=min(xs), top=min(ys), x1=max(xs), bottom=max(ys),
                conf=conf, quad=quad, char_boxes=char_boxes,
            ))
        return segments

    @staticmethod
    def _to_segments(rows: list) -> list[Segment]:
        segments: list[Segment] = []
        for row in rows:
            quad_raw, text, conf = row[0], row[1], float(row[2])
            if not text or not str(text).strip():
                continue
            quad = [(float(p[0]), float(p[1])) for p in quad_raw]
            xs = [p[0] for p in quad]
            ys = [p[1] for p in quad]
            segments.append(Segment(
                text=str(text), x0=min(xs), top=min(ys), x1=max(xs), bottom=max(ys),
                conf=conf, quad=quad,
            ))
        return segments
