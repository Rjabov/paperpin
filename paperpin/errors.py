"""The exception family every paperpin error belongs to.

Callers write `except PaperpinError` and get everything the library can
raise on purpose; the subclasses also inherit the builtin types earlier
versions raised, so existing `except ValueError` code keeps working.
"""
from __future__ import annotations


class PaperpinError(Exception):
    """Base for every intentional paperpin error."""


class DocumentError(PaperpinError, ValueError):
    """The document could not be loaded or read (corrupt, encrypted,
    unsupported, empty)."""


class SchemaError(PaperpinError, ValueError):
    """A schema or field declaration is malformed."""


class ExtractionError(PaperpinError, RuntimeError):
    """A model/adapter failed to produce a usable extraction."""
