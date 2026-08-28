"""Tier-1 render backend: local rhwp (PDF only, no Hancom)."""

from __future__ import annotations

from pathlib import Path

from .base import RenderBackend, RenderResult
from .rhwp import RenderFn, resolve_rhwp, rhwp_render_fn


class LocalRhwpBackend(RenderBackend):
    """Render ``.hwp``/``.hwpx`` → PDF locally with the rhwp CLI.

    Runs anywhere (no Hancom). PDF only — rhwp is a renderer with no DOCX path.
    ``render_fn`` is injectable (like ``verify_hwp``) so tests need no rhwp binary.
    """

    name = "rhwp"
    formats = ("pdf",)

    def __init__(
        self, rhwp_bin: str | None = None, render_fn: RenderFn | None = None
    ) -> None:
        self._rhwp_bin = rhwp_bin
        self._render_fn = render_fn

    def is_available(self) -> bool:
        return self._render_fn is not None or resolve_rhwp(self._rhwp_bin) is not None

    def render(self, src: Path, out: Path, *, fmt: str = "pdf") -> RenderResult:
        out = Path(out)
        if fmt != "pdf":
            return RenderResult(
                out, fmt, self.name, 2,
                stderr="rhwp renders PDF only; DOCX needs the hwp2pdf (Hancom) engine",
            )
        render = self._render_fn
        if render is None:
            rb = resolve_rhwp(self._rhwp_bin)
            if rb is None:
                return RenderResult(
                    out, fmt, self.name, 2,
                    stderr="rhwp not found — install the rhwp CLI on PATH or set $RHWP_BIN",
                )
            render = rhwp_render_fn(rb)
        try:
            render(Path(src), out)
        except Exception as exc:  # noqa: BLE001 — report failure, don't raise
            return RenderResult(out, fmt, self.name, 1, stderr=str(exc))
        return RenderResult(out, fmt, self.name, 0)
