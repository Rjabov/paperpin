"""paperpin — pin every value an LLM extracts from a document to the exact
spot on the page it came from, and flag the values that aren't there at all.

Never ask the model for coordinates: the model reads, deterministic OCR /
text-layer geometry locates, an alignment algorithm links them, verification
proves or flags every field. MIT licensed. Zero telemetry.
"""
from .api import extract, ground
from .errors import DocumentError, ExtractionError, PaperpinError, SchemaError
from .intake.loader import Document, load_document
from .types import (Candidate, FieldResult, FieldSpec, FieldType, GroundResult,
                    PageInfo, Segment, Status, _version)

__version__ = _version()

__all__ = [
    "ground", "extract",
    "Document", "load_document",
    "GroundResult", "FieldResult", "FieldSpec", "FieldType", "Candidate",
    "Status", "PageInfo", "Segment",
    "PaperpinError", "DocumentError", "SchemaError", "ExtractionError",
    "__version__",
]
