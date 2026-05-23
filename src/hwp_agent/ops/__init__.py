"""Editing operations on HWPX documents.

First slice: document metadata (cover/properties) — see :mod:`metadata`.
"""

from .metadata import Metadata, read_metadata, update_metadata

__all__ = ["Metadata", "read_metadata", "update_metadata"]
