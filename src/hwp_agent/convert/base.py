"""Abstract converter interface.

A *backend* turns a binary ``.hwp`` into the XML-based ``.hwpx`` package.
Backends are swappable (hwp2hwpx today; hwpilot or others later) so the rest
of the toolkit only ever depends on this interface, never on a concrete tool.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ConvertResult:
    """Outcome of a single conversion."""

    hwpx_path: Path
    backend: str
    returncode: int
    stdout: str = ""
    stderr: str = ""
    #: Package entries the backend synthesized to make the output a valid HWPX
    #: (e.g. a ``Preview/`` part declared in container.xml but never written).
    normalized: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """True when the backend exited cleanly and produced the output file."""
        return self.returncode == 0 and self.hwpx_path.is_file()


class ConverterBackend(ABC):
    """Converts ``.hwp`` files to ``.hwpx``."""

    #: Stable short identifier, recorded on every :class:`ConvertResult`.
    name: str = "abstract"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this backend can run right now (binaries/jars present)."""

    @abstractmethod
    def convert(self, hwp_path: Path, hwpx_path: Path) -> ConvertResult:
        """Convert ``hwp_path`` to ``hwpx_path``.

        The original ``.hwp`` is never modified — it is the source of truth.
        The produced ``.hwpx`` is treated as a regenerable cache artifact.
        """
