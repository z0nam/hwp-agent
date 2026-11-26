# SPDX-License-Identifier: Apache-2.0
#
# Adapted from python-hwpx 2.11.1, ``hwpx/tools/table_navigation.py``
# (``_find_label_candidates``, ``_move`` and their helpers).
# Copyright 2025-2026 airmang. Licensed under the Apache License, Version 2.0
# (https://www.apache.org/licenses/LICENSE-2.0). Changes: trimmed to the label
# search + one-step move hwp-agent uses, and typed against any table object with
# ``row_count`` / ``column_count`` / ``cell(row, col)``.
"""Find a form label cell and step to its neighbour (#137).

``form`` used python-hwpx's private label helpers; 7.0 moved label search to the
separate python-hwpx-automation package and dropped the private functions, so
the two small pieces hwp-agent needs live here, unchanged in behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_WHITESPACE_RE = re.compile(r"\s+")

_DELTA = {"right": (0, 1), "left": (0, -1), "down": (1, 0), "up": (-1, 0)}


@dataclass(frozen=True, slots=True)
class LabelCandidate:
    table_index: int
    table: Any
    row: int
    col: int
    text: str


def _collapse_whitespace(value: str) -> str:
    return _WHITESPACE_RE.sub(" ", value).strip()


def _normalize_label_text(value: str) -> str:
    normalized = _collapse_whitespace(value).casefold()
    while normalized.endswith((":", "：")):
        normalized = normalized[:-1].rstrip()
    return normalized


def _cell_text(table, row: int, col: int) -> str:
    cell = table.cell(row, col)
    paragraphs = list(getattr(cell, "paragraphs", []) or [])
    if paragraphs:
        return "\n".join(paragraph.text or "" for paragraph in paragraphs)
    return cell.text


def find_label_candidates(tables, label_text: str) -> list[LabelCandidate]:
    """Cells whose text equals *label_text* (whitespace-collapsed, case-folded,
    trailing colons ignored), across *tables* (items with ``table_index`` and
    ``table``). Raises ``ValueError`` for a blank label."""
    normalized_label = _normalize_label_text(label_text)
    if not normalized_label:
        raise ValueError("label_text must contain at least one non-whitespace character")
    candidates: list[LabelCandidate] = []
    for ref in tables:
        table = ref.table
        for row in range(table.row_count):
            for col in range(table.column_count):
                text = _cell_text(table, row, col)
                if _normalize_label_text(text) == normalized_label:
                    candidates.append(LabelCandidate(ref.table_index, table, row, col, text))
    return candidates


def move(table, row: int, col: int, direction: str) -> tuple[int, int] | None:
    """The grid position one step *direction* (left/right/up/down) away, or None
    off the table's edge."""
    dr, dc = _DELTA.get(direction, (-1, 0))
    r, c = row + dr, col + dc
    if r < 0 or c < 0 or r >= table.row_count or c >= table.column_count:
        return None
    return (r, c)
