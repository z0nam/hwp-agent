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
    """Find the rhwp CLI: explicit arg > ``$RHWP_BIN`` > PATH > the copy
    ``hwp-agent setup`` downloaded into the user data dir.

    Returns None if absent. A set ``$RHWP_BIN`` is final (no further fallback).
    """
    if explicit:
        return explicit if Path(explicit).is_file() else shutil.which(explicit)
    env = os.environ.get("RHWP_BIN")
    cand = env or DEFAULT_RHWP
    if Path(cand).is_file():
        return cand
    found = shutil.which(cand)
    if found or env:
        return found
    from .fetch_rhwp import rhwp_dest

    fetched = rhwp_dest()
    return str(fetched) if fetched.is_file() else None


def rhwp_search_desc(explicit: str | None = None) -> str:
    """Where :func:`resolve_rhwp` looked, for a "not found" message."""
    if explicit:
        return f"지정 경로 {explicit} 없음"
    env = os.environ.get("RHWP_BIN")
    where = f"$RHWP_BIN={env} 없음" if env else "$RHWP_BIN 미설정"
    return f"{where}, PATH에 {DEFAULT_RHWP} 없음 (받기: hwp-agent setup)"


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
