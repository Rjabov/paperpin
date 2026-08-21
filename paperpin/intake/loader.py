"""Document intake: PDFs, images, HEIC; per-page route decision (HANDOVER §6.1).

Route decision happens per PAGE, not per file (E-5): a "digital" PDF can
contain scanned pages and vice versa. A text layer is only trusted when it
has enough words AND the extracted text passes a garbage check (broken
encodings / cid: glyphs fall back to OCR of the rendered page).
"""
from __future__ import annotations

import hashlib
import io
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

from ..errors import DocumentError
from ..types import PageInfo, Segment

MIN_TEXTLAYER_WORDS = 5  # fewer → treat page as scanned (E-5)

# pdfplumber renders through pypdfium2, and pdfium is not thread-safe:
# concurrent renders storm PdfiumError("Failed to load document") and can
# SIGSEGV outright (reproduced 2026-08-21 via the Lab's page endpoint,
# which rasters on a request threadpool). Every pdfium entry serializes here.
_PDFIUM_LOCK = threading.Lock()

_HEIF_REGISTERED = False


def _ensure_heif() -> None:
    global _HEIF_REGISTERED
    if _HEIF_REGISTERED:
        return
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
        _HEIF_REGISTERED = True
    except ImportError:
        pass  # only fatal if the user actually feeds us HEIC


# Scan PDFs sometimes declare the page at pixel dimensions expressed as
# points (2500+ pt "paper"). A fixed dpi then renders 30-90MP rasters, which
# segfault the OCR det model past ~25MP. Cap the long side and trade dpi —
# the underlying scan rarely holds more real detail than this anyway.
MAX_RASTER_LONG_SIDE = 4200
# multi-frame image bounds: pages beyond these are dropped rather than
# decoded — a compressed TIFF can expand thousands of times over its file size
MAX_IMAGE_PAGES = 50
MAX_IMAGE_TOTAL_PIXELS = 400_000_000


@dataclass
class Page:
    """One page of a document, with lazy raster access.

    `size` is the upright-original space every published bbox is normalized
    against: pixels for image files, PDF points for PDF pages.
    """

    index: int
    size: tuple[float, float]
    route: str                        # "textlayer" | "ocr"
    pdf_page: Optional[object] = None  # pdfplumber page (kept while doc is open)
    image: Optional[Image.Image] = None  # upright original raster (images route)
    render_dpi: float = 150.0
    _raster: Optional[Image.Image] = field(default=None, repr=False)
    text_segments: Optional[list[Segment]] = None  # filled by textlayer backend

    def raster(self, dpi: Optional[float] = None) -> Image.Image:
        """Raster of the upright original page (render PDFs, pass images
        through). Oversized camera images are downscaled to the same cap as
        PDF renders — `size` stays the original space, so published bboxes
        are unaffected; only the working raster shrinks."""
        if self.image is not None:
            long_px = max(self.image.size)
            if long_px <= MAX_RASTER_LONG_SIDE:
                return self.image
            if self._raster is None:
                f = MAX_RASTER_LONG_SIDE / long_px
                self._raster = self.image.resize(
                    (max(1, round(self.image.width * f)),
                     max(1, round(self.image.height * f))), Image.LANCZOS)
            return self._raster
        dpi = dpi or self.render_dpi
        long_px = max(self.size) * dpi / 72.0
        if long_px > MAX_RASTER_LONG_SIDE:
            dpi = MAX_RASTER_LONG_SIDE * 72.0 / max(self.size)
        if self._raster is not None and abs(dpi - self.render_dpi) < 1e-6:
            return self._raster
        with _PDFIUM_LOCK:
            page_img = self.pdf_page.to_image(resolution=dpi)
        self._raster = page_img.original.convert("RGB")
        self.render_dpi = dpi
        return self._raster

    def info(self) -> PageInfo:
        raster = self._raster or self.image
        return PageInfo(
            index=self.index,
            width=self.size[0],
            height=self.size[1],
            route=self.route,
            dpi=(self.render_dpi
                 if self.pdf_page is not None and self._raster is not None
                 else None),  # only a dpi that actually rendered something
            px_width=raster.width if raster is not None else None,
            px_height=raster.height if raster is not None else None,
        )


@dataclass
class Document:
    source: str
    kind: str                 # "pdf" | "image"
    pages: list[Page]
    sha256: str
    pages_dropped: int = 0    # frames past the multi-page bounds, undecoded
    _pdf: Optional[object] = None  # pdfplumber PDF handle, closed via .close()

    def close(self) -> None:
        """Release the PDF handle AND every held raster — a 100-page scan
        otherwise keeps ~100 decompressed page images alive for the
        result object's lifetime."""
        if self._pdf is not None:
            self._pdf.close()
            self._pdf = None
        for page in self.pages:
            page.pdf_page = None
            page.image = None
            page._raster = None
            page.text_segments = None

    def __enter__(self) -> "Document":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


_GARBAGE_CID = re.compile(r"\(cid:\d+\)")


def text_layer_is_sane(words: list[dict]) -> bool:
    """Reject broken text layers (E-5): cid: glyphs, control-char soup,
    implausibly low letter ratio."""
    if len(words) < MIN_TEXTLAYER_WORDS:
        return False
    joined = " ".join(w["text"] for w in words[:400])
    if _GARBAGE_CID.search(joined):
        return False
    if not joined:
        return False
    printable = sum(1 for c in joined if c.isprintable())
    if printable / len(joined) < 0.95:
        return False
    alnum = sum(1 for c in joined if c.isalnum())
    if alnum / max(1, len(joined)) < 0.3:
        return False
    return True


def load_document(source: str | Path | bytes, filename: str = "") -> Document:
    """Load a PDF or image into a page registry with per-page routes."""
    if isinstance(source, (str, Path)):
        path = Path(source)
        if not path.exists():
            raise FileNotFoundError(f"no such document: {path}")
        if path.is_dir():
            raise DocumentError(f"{path} is a directory, not a document")
        data = path.read_bytes()
        name = str(path)
    elif isinstance(source, (bytes, bytearray)):
        data = bytes(source)
        name = filename or "<bytes>"
    else:
        raise DocumentError(
            f"cannot load a document from {type(source).__name__} — "
            "pass a path or the file's bytes")

    if len(data) == 0:
        raise DocumentError(f"document is empty (zero bytes): {name}")

    digest = hashlib.sha256(data).hexdigest()

    # readers tolerate junk before the header (and some scanners emit it) —
    # sniff the first KB rather than byte 0 alone
    head = data[:1024]
    if head[:5] == b"%PDF-":
        return _load_pdf(data, name, digest)
    if b"%PDF-" in head:
        return _load_pdf(data[head.index(b"%PDF-"):], name, digest)
    return _load_image(data, name, digest)


def _load_pdf(data: bytes, name: str, digest: str) -> Document:
    try:
        import pdfplumber
    except ImportError as e:
        raise ImportError(
            "PDF support needs pdfplumber — install with: pip install paperpin[pdf]"
        ) from e
    try:
        pdf = pdfplumber.open(io.BytesIO(data))
    except Exception as e:
        # pdfminer's password error stringifies EMPTY — sniff the exception
        # chain's class names too, or the user gets a blank reason
        chain = []
        cur: Optional[BaseException] = e
        while cur is not None and len(chain) < 6:
            chain.append(f"{type(cur).__name__} {cur}")
            cur = cur.__cause__ or cur.__context__
        blob = " ".join(chain).lower()
        if "password" in blob or "encrypt" in blob or "crypt" in blob:
            raise DocumentError(
                f"{name}: PDF is password-protected — decrypt it first (E-6)"
            ) from e
        raise DocumentError(f"{name}: cannot open as PDF: {e}") from e

    pages: list[Page] = []
    for i, pg in enumerate(pdf.pages):
        try:
            words = pg.extract_words()
        except Exception:
            words = []
        route = "textlayer" if text_layer_is_sane(words) else "ocr"
        w, h = float(pg.width), float(pg.height)
        if w <= 0 or h <= 0:
            # a zero-area MediaBox page killed the whole document with a raw
            # ZeroDivisionError (normalization + pdfplumber's own renderer);
            # it can hold nothing — carry it as an empty text page
            w, h, route = 1.0, 1.0, "textlayer"
        pages.append(Page(index=i, size=(w, h), route=route, pdf_page=pg))
    return Document(source=name, kind="pdf", pages=pages, sha256=digest, _pdf=pdf)


def _load_image(data: bytes, name: str, digest: str) -> Document:
    _ensure_heif()
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        raise DocumentError(
            f"{name}: cannot open as an image ({e}) — supported: PDF, PNG, JPEG, "
            "WEBP, TIFF, BMP and HEIC (HEIC needs: pip install paperpin[heic])"
        ) from e
    frames = [img]
    dropped = 0
    if getattr(img, "n_frames", 1) > 1:  # multi-page TIFF
        # decompression bomb guard (verified: a 453KB G4 TIFF of 500 blank
        # pages decoded to ~7GB) — bound pages and total decoded pixels
        from PIL import ImageSequence
        frames = []
        total_px = 0
        for f in ImageSequence.Iterator(img):
            total_px += f.width * f.height
            if len(frames) >= MAX_IMAGE_PAGES or total_px > MAX_IMAGE_TOTAL_PIXELS:
                break
            frames.append(f.copy())
        dropped = getattr(img, "n_frames", len(frames)) - len(frames)
    pages = []
    for i, frame in enumerate(frames):
        # EXIF orientation lies (E-2): always transpose first. Upright-original
        # space is the post-transpose raster — what every viewer displays.
        frame = ImageOps.exif_transpose(frame)
        frame = frame.convert("RGB")
        pages.append(Page(index=i, size=(float(frame.width), float(frame.height)),
                          route="ocr", image=frame))
    return Document(source=name, kind="image", pages=pages, sha256=digest,
                    pages_dropped=max(0, dropped))
