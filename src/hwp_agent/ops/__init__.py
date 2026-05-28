"""Editing operations on HWPX documents.

- :mod:`metadata` — document properties (title, creator, ...).
- :mod:`form` — discover a form's fillable slots and fill them.
"""

from .author import (
    AuthorResult,
    Block,
    Segment,
    TableBlock,
    TableFormat,
    fill_from_markdown,
    inline_segments,
    parse_markdown,
    plain_text,
    read_instructions,
)
from .doctor import diagnose_template
from .form import FillResult, FormSlot, FormSpec, analyze_form, fill_form
from .images import (
    ImageReplaceResult,
    PicInfo,
    ReplaceOutcome,
    list_images,
    parse_alt_text,
    read_image_size,
    replace_image,
)
from .metadata import Metadata, read_metadata, update_metadata
from .styles import StyleInfo, classify_document, read_style_system, role_map

__all__ = [
    "Metadata",
    "read_metadata",
    "update_metadata",
    "FormSlot",
    "FormSpec",
    "FillResult",
    "analyze_form",
    "fill_form",
    "StyleInfo",
    "read_style_system",
    "role_map",
    "classify_document",
    "Block",
    "Segment",
    "TableBlock",
    "TableFormat",
    "AuthorResult",
    "parse_markdown",
    "inline_segments",
    "plain_text",
    "fill_from_markdown",
    "read_instructions",
    "diagnose_template",
    "PicInfo",
    "ReplaceOutcome",
    "ImageReplaceResult",
    "list_images",
    "replace_image",
    "parse_alt_text",
    "read_image_size",
]
