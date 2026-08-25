"""Core data structures for paperpin.

Coordinate convention (used EVERYWHERE outside backend internals):
bounding boxes are (x0, y0, x1, y1), normalized 0..1, origin at the TOP-LEFT
of the upright original page/image — i.e. the file exactly as the user's
viewer displays it (after EXIF orientation is applied, before any of our
own preprocessing). Backends produce coordinates in whatever processed space
they work in; the geometry transform chain maps them back here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from dataclasses import fields as _dataclass_fields
from enum import Enum
from pathlib import Path
from typing import Any, Optional


class Status(str, Enum):
    """Exactly five statuses. Never collapse them (HANDOVER §4.1).

    verified       located exactly and every verification check that ran
                   agreed (a checksum/arithmetic proof, when one exists, is
                   recorded in `proof`; checks can only demote)
    low_confidence located with doubt — a fuzzy match, or an exact match a
                   verification check demoted; a human should glance
    ambiguous      multiple equally-plausible locations; all candidates reported
    not_found      the model asserted a value that matches nothing on the page
                   (the hallucination flag)
    not_present    the model itself returned null — field isn't on the document
    """

    # str(status) must be the wire value on every supported Python — the
    # pre-3.11 enum mixin and 3.11+ disagree about __format__/__str__
    __str__ = str.__str__

    VERIFIED = "verified"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    NOT_PRESENT = "not_present"


class FieldType(str, Enum):
    TEXT = "text"
    NUMBER = "number"
    DATE = "date"
    ID = "id"          # invoice numbers, IBANs, VAT ids, EANs — exact-ish strings
    PERCENT = "percent"
    BLOCK = "block"    # multi-line values (addresses) — token-set matching
    TABLE = "table"    # array of row objects (line items) — per-cell grounding


@dataclass
class FieldSpec:
    name: str
    type: FieldType = FieldType.TEXT
    anchors: list[str] = field(default_factory=list)  # label words near the value
    checksum: Optional[str] = None  # "iban" | "ean" | "vat" — enables checksum proof
    columns: Optional[dict[str, "FieldSpec"]] = None  # TABLE only: per-cell specs
    affinity: list[str] = field(default_factory=list)  # fields whose pinned line
    # this field's value prints on (e.g. a currency mark beside its total)
    proof: Optional[dict] = None  # arithmetic relation proving this field:
    # {"sum": [..]} | {"product": [..]} | {"percent_of": [base, rate]} (§6.6.4)
    aliases: Optional[dict[str, list[str]]] = None  # canonical value ->
    # alternate literal prints ("CZK" -> ["Kč"]): the document may print any
    # of them for that value; matching one grounds the field
    pattern: Optional[str] = None  # regex over the canonical value (ID fields):
    # a page print that fullmatches the pattern and shares its core with the
    # value IS the value — e.g. VAT ids print without the country prefix

    @classmethod
    def coerce(cls, name: str, spec: Any) -> "FieldSpec":
        from .errors import SchemaError

        def field_type(raw: Any) -> FieldType:
            try:
                return FieldType(raw)
            except ValueError:
                raise SchemaError(
                    f"field {name!r}: unknown type {raw!r} — one of "
                    f"{[t.value for t in FieldType]}") from None

        if isinstance(spec, FieldSpec):
            return spec
        if isinstance(spec, str):
            return cls(name=name, type=field_type(spec))
        if isinstance(spec, dict):
            columns = None
            if spec.get("columns"):
                if not isinstance(spec["columns"], dict):
                    raise SchemaError(
                        f"field {name!r}: columns must be a dict of "
                        f"column -> spec, got {type(spec['columns']).__name__}")
                columns = {c: FieldSpec.coerce(c, s) for c, s in spec["columns"].items()}
            return cls(
                name=name,
                type=field_type(spec.get("type", "text")),
                anchors=list(spec.get("anchors", [])),
                checksum=spec.get("checksum"),
                columns=columns,
                affinity=list(spec.get("affinity", [])),
                proof=spec.get("proof"),
                aliases=spec.get("aliases"),
                pattern=spec.get("pattern"),
            )
        raise SchemaError(f"field {name!r}: cannot build a FieldSpec from "
                          f"{type(spec).__name__} ({spec!r})")


@dataclass
class Segment:
    """One piece of located text, in the coordinate space declared by its page.

    x0/top/x1/bottom are in processed-space pixels (or PDF points for the text
    layer); `quad` keeps the raw 4-point polygon for rotated text (E-19).
    `char_boxes`, when present, is a per-character list of (x0, top, x1, bottom)
    aligned with `text` — the text layer provides these for exact sub-boxes.
    """

    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    conf: float = 1.0
    page: int = 0
    quad: Optional[list[tuple[float, float]]] = None
    char_boxes: Optional[list[tuple[float, float, float, float]]] = None

    @property
    def height(self) -> float:
        return self.bottom - self.top

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Candidate:
    """A possible location for a field value."""

    page: int
    bbox: tuple[float, float, float, float]  # normalized original space
    score: float
    evidence: str            # exact document text matched
    exact: bool = True       # False → fuzzy/partial match
    fused: bool = False      # evidence glued from separate printed runs
    anchor: Optional[str] = None
    anchor_score: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return self.score + self.anchor_score


@dataclass
class FieldResult:
    name: str
    value: Any
    status: Status
    confidence: float = 0.0
    page: Optional[int] = None            # 0-based
    bbox: Optional[tuple[float, float, float, float]] = None
    evidence: Optional[str] = None        # exact document text it matched
    method: Optional[str] = None          # HOW LOCATED: "textlayer" | "ocr"
    proof: Optional[str] = None           # strongest proof: "checksum" | "arithmetic"
    anchor: Optional[str] = None          # label text found near it
    quote: Optional[str] = None           # model-provided source quote, if any
    repaired_value: Optional[str] = None  # checksum-driven confusable repair (E-14)
    candidates: list[Candidate] = field(default_factory=list)  # all, for ambiguous
    notes: list[str] = field(default_factory=list)             # e.g. "⚠ arithmetic"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class PageInfo:
    """Registry entry for one page: original dimensions + processing route."""

    index: int
    width: float           # original-space width  (pixels for images, points for PDF pages)
    height: float          # original-space height
    route: str             # "textlayer" | "ocr"
    dpi: Optional[float] = None      # render resolution used for raster work on PDF pages
    px_width: Optional[int] = None   # rendered raster dims, when a raster exists
    px_height: Optional[int] = None

    def to_dict(self) -> dict:
        return asdict(self)


class GroundResult:
    """Mapping-ish container for all field results + document metadata."""

    def __init__(self, fields: dict[str, FieldResult], pages: list[PageInfo],
                 source: str = "", meta: Optional[dict] = None):
        self._fields = fields
        self.pages = pages
        self.source = source
        self.meta = meta or {}

    def __getitem__(self, name: str) -> FieldResult:
        return self._fields[name]

    def __contains__(self, name: str) -> bool:
        return name in self._fields

    def __iter__(self):
        return iter(self._fields.values())

    def __len__(self) -> int:
        return len(self._fields)

    def keys(self):
        return self._fields.keys()

    def items(self):
        return self._fields.items()

    def get(self, name: str, default=None):
        return self._fields.get(name, default)

    def __repr__(self) -> str:
        c = ", ".join(f"{k}={v}" for k, v in sorted(self.counts().items()))
        return f"<GroundResult {self.source!r}: {len(self)} fields ({c})>"

    @property
    def fields(self) -> dict[str, FieldResult]:
        return self._fields

    @classmethod
    def from_dict(cls, data: dict, source: str = "",
                  meta: Optional[dict] = None) -> "GroundResult":
        """Rebuild a result from save()'s JSON. Tolerant: unknown keys are
        ignored so newer files load in older versions."""
        def pick(cls_, d: dict) -> dict:
            names = {f.name for f in _dataclass_fields(cls_)}
            return {k: v for k, v in d.items() if k in names}

        fields: dict[str, FieldResult] = {}
        for name, fr in (data.get("fields") or {}).items():
            kw = pick(FieldResult, fr)
            kw["name"] = name
            kw["status"] = Status(fr["status"])
            if kw.get("bbox"):
                kw["bbox"] = tuple(kw["bbox"])
            kw["candidates"] = [
                Candidate(**{**pick(Candidate, c), "bbox": tuple(c["bbox"])})
                for c in fr.get("candidates", []) if c.get("bbox")]
            fields[name] = FieldResult(**kw)
        pages = [PageInfo(**pick(PageInfo, p)) for p in data.get("pages", [])]
        return cls(fields=fields, pages=pages,
                   source=source or data.get("source", ""),
                   meta=meta if meta is not None else data.get("meta") or {})

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for f in self:
            out[f.status.value] = out.get(f.status.value, 0) + 1
        return out

    def to_dict(self) -> dict:
        return {
            "paperpin": {"version": _version(), "coordinate_space":
                         "normalized 0..1, origin top-left, upright original page"},
            "source": self.source,
            "pages": [p.to_dict() for p in self.pages],
            "fields": {name: fr.to_dict() for name, fr in self._fields.items()},
            "summary": self.counts(),
            # keys starting with "_" are runtime-only (image providers etc.)
            # and never serialized; exports must stay key- and object-free (E-37)
            "meta": {k: v for k, v in self.meta.items() if not k.startswith("_")},
        }

    def to_json(self, indent: int = 1) -> str:
        """The exact text `save()` writes — for callers that pipe or POST the
        result instead of writing a file. NaN/Infinity leave as null: legal
        for json.loads, invalid JSON for every other consumer."""
        payload = _json_safe(self.to_dict())
        return json.dumps(payload, indent=indent, ensure_ascii=False,
                          allow_nan=False)

    def save(self, path: str) -> None:
        """Write JSON atomically: serialize fully, then replace the target —
        a mid-serialization failure (lone surrogate, absurd nesting) must
        not truncate a previous good file."""
        text = self.to_json()
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(text, encoding="utf-8", errors="strict")
        tmp.replace(target)

    def page_image(self, page: int = 0, width: Optional[int] = None):
        """The page raster a bbox is normalized against: multiply a bbox by
        this image's (width, height) for pixels. `width` scales proportionally
        — any width works, the boxes are normalized. Returns a PIL Image."""
        if not 0 <= page < len(self.pages):
            raise IndexError(
                f"no page {page} — this result has {len(self.pages)} page(s)")
        from .outputs.common import fit_width, get_page_images
        return fit_width(get_page_images(self, [page])[page], width)

    def overlay(self, path: str, page: Optional[int] = None) -> None:
        from .outputs.overlay import render_overlay
        render_overlay(self, path, page=page)

    def viewer(self, path: str) -> None:
        from .outputs.viewer import render_viewer
        render_viewer(self, path)


def _json_safe(v: Any) -> Any:
    """json.loads happily accepts NaN/Infinity and lone surrogates; emitting
    them back produces a file JSON.parse and serde reject. Scrub on save."""
    if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
        return None
    if isinstance(v, str):
        return v.encode("utf-8", "replace").decode("utf-8")
    if isinstance(v, dict):
        return {_json_safe(k): _json_safe(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_safe(x) for x in v]
    return v


def _version() -> str:
    try:
        from importlib.metadata import version
        return version("paperpin")
    except Exception:
        return "0.1.0"
