"""Editing operations on HWPX documents.

- :mod:`metadata` — document properties (title, creator, ...).
- :mod:`form` — discover a form's fillable slots and fill them.
"""

from .form import FillResult, FormSlot, FormSpec, analyze_form, fill_form
from .metadata import Metadata, read_metadata, update_metadata

__all__ = [
    "Metadata",
    "read_metadata",
    "update_metadata",
    "FormSlot",
    "FormSpec",
    "FillResult",
    "analyze_form",
    "fill_form",
]
