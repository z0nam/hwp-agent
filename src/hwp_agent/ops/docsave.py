"""Save a python-hwpx document the way hwp-agent's output has always been saved.

python-hwpx 2.11 dropped every paragraph's line layout cache (``<hp:linesegarray>``)
in each section it re-serialized, so Hangul laid those sections out afresh on open.
7.0 keeps a cache unless it looks stale by its own test (a line starting past the
paragraph's end). That leaves caches hwp-agent made stale in ways that test cannot
see — a template caption cloned under a new title, a template table cell refilled —
and Hangul draws a cached line with the text it now holds pressed into it. So the
writer keeps 2.11's rule: a section that changed loses all its caches (#137).
"""

from __future__ import annotations

from pathlib import Path


def save_document(doc, path: str | Path) -> None:
    """``doc.save(path)`` after dropping the layout caches of every changed section."""
    for section in doc.sections:
        if section.dirty:
            section.remove_layout_caches()
    doc.save(str(path))
