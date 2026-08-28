"""rhwp renderer helpers — the single source of truth for driving the ``rhwp`` CLI.

Factored out of :mod:`hwp_agent.ops.verify` so both the verify loop (Tier 1
render-then-check) and the render backends (:class:`~hwp_agent.render.local_rhwp.LocalRhwpBackend`)
share one implementation. This module imports nothing from ``ops`` to keep the
dependency one-directional (``ops.verify`` and ``render.*`` both import *from* here).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

#: a renderer: ``(src, out_pdf) -> None``; raises on failure.
RenderFn = Callable[[Path, Path], None]

DEFAULT_RHWP = "rhwp"


def resolve_rhwp(explicit: str | None = None) -> str | None:
    """Find the rhwp CLI: explicit arg > ``$RHWP_BIN`` > PATH. None if absent."""
    cand = explicit or os.environ.get("RHWP_BIN") or DEFAULT_RHWP
    if Path(cand).is_file():
        return cand
    return shutil.which(cand)


def rhwp_render_fn(rhwp_bin: str) -> RenderFn:
    """Default renderer: ``rhwp export-pdf <src> -o <out.pdf>`` (native HWP/HWPX)."""

    def render(src: Path, out_pdf: Path) -> None:
        proc = subprocess.run(  # noqa: S603
            [rhwp_bin, "export-pdf", str(src), "-o", str(out_pdf)],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0 or not out_pdf.is_file():
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()
            tail = " | ".join(detail[-3:]) if detail else f"exit {proc.returncode}"
            raise RuntimeError(f"rhwp export-pdf failed: {tail}")

    return render
