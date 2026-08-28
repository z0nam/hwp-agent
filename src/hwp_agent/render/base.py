"""Render abstraction — HWP/HWPX → PDF/DOCX (a new axis, separate from ``convert/``).

``convert/`` turns a binary ``.hwp`` into the XML ``.hwpx`` package. This axis
turns an ``.hwp``/``.hwpx`` into a *rendered* terminal artefact (PDF or DOCX)
for sign-off or distribution — never edited further by hwp-agent.

Two backends implement :class:`RenderBackend`:

- ``rhwp`` (Tier 1) — local, PDF only, layout is approximate (rhwp paginates
  ~+25% vs Hancom); good for gross-defect checks, runs anywhere.
- ``hwp2pdf`` (Tier 2) — Hancom-authoritative PDF+DOCX, round-tripped through a
  Windows node; the source of truth for precise layout.

Mirrors the shape of :mod:`hwp_agent.convert.base` (``.ok`` result, cheap
``is_available()``).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RenderResult:
    """Outcome of one render. ``ok`` == process succeeded *and* the file exists."""

    output_path: Path
    fmt: str  # "pdf" | "docx"
    backend: str  # "rhwp" | "hwp2pdf"
    returncode: int
    stdout: str = ""
    stderr: str = ""
    remote: bool = False  # True when it round-tripped through a remote node

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and Path(self.output_path).is_file()


class RenderBackend(ABC):
    """A renderer that turns an ``.hwp``/``.hwpx`` into a PDF or DOCX."""

    name: str = "abstract"  # stable short id, recorded on every RenderResult
    formats: tuple[str, ...] = ()  # e.g. ("pdf",) or ("pdf", "docx")

    @abstractmethod
    def is_available(self) -> bool:
        """Cheap check: can this backend run right now (binary present / reachable)?"""

    def supports(self, fmt: str) -> bool:
        return fmt in self.formats

    @abstractmethod
    def render(self, src: Path, out: Path, *, fmt: str = "pdf") -> RenderResult:
        """Render *src* to *out* in *fmt*. Returns a :class:`RenderResult` (never raises
        for expected failures — failure is reported via ``returncode``/``stderr``)."""
