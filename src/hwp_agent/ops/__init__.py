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
from .extract import extract_markdown
from .form import FillResult, FormSlot, FormSpec, analyze_form, fill_form
from .guard import (
    GuardResult,
    plan_output,
    read_stored_fingerprint,
    stamp_fingerprint,
)
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
from .normalize import (
    NormalizeAction,
    NormalizePlan,
    NormalizeSkip,
    apply_normalization,
    plan_normalization,
)
from .profile import (
    Profile,
    ProfileFillResult,
    ProfileMatch,
    fill_from_profile,
    load_profile,
    match_slot,
    normalize_label,
)
from .styles import StyleInfo, classify_document, read_style_system, role_map
from .template import (
    bundled_template_path,
    describe_template_source,
    resolve_template_path,
)
from .verify import (
    PageIssue,
    PageVerdict,
    VerifyResult,
    verify_document,
    verify_hwp,
    verify_pdf,
)

__all__ = [
    "Metadata",
    "read_metadata",
    "update_metadata",
    "FormSlot",
    "FormSpec",
    "FillResult",
    "analyze_form",
    "fill_form",
    "Profile",
    "ProfileMatch",
    "ProfileFillResult",
    "load_profile",
    "fill_from_profile",
    "match_slot",
    "normalize_label",
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
    "resolve_template_path",
    "bundled_template_path",
    "describe_template_source",
    "diagnose_template",
    "GuardResult",
    "plan_output",
    "stamp_fingerprint",
    "read_stored_fingerprint",
    "NormalizeAction",
    "NormalizePlan",
    "NormalizeSkip",
    "plan_normalization",
    "apply_normalization",
    "extract_markdown",
    "PageIssue",
    "PageVerdict",
    "VerifyResult",
    "verify_pdf",
    "verify_hwp",
    "verify_document",
    "PicInfo",
    "ReplaceOutcome",
    "ImageReplaceResult",
    "list_images",
    "replace_image",
    "parse_alt_text",
    "read_image_size",
]
