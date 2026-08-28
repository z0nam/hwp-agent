"""Tier selection: choose rhwp (Tier 1) vs remote hwp2pdf (Tier 2) and render."""

from __future__ import annotations

from pathlib import Path

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig, resolve_hwp2pdf_config
from .local_rhwp import LocalRhwpBackend
from .remote_hwp2pdf import RemoteHwp2PdfBackend, RemoteTransport
from .rhwp import RenderFn


def select_render_backend(
    fmt: str,
    engine: str = "auto",
    *,
    config: Hwp2PdfConfig | None = None,
    transport: RemoteTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
    _resolve: bool = True,
) -> RenderBackend:
    """Pick a backend. ``engine``: ``auto`` | ``hwp2pdf`` | ``rhwp``.

    ``auto``: DOCX → always hwp2pdf (rhwp can't); PDF → hwp2pdf if available, else rhwp.
    """
    if config is None and _resolve:
        config = resolve_hwp2pdf_config()
    remote = RemoteHwp2PdfBackend(config, transport)
    local = LocalRhwpBackend(rhwp_bin, render_fn)

    if engine == "hwp2pdf":
        return remote
    if engine == "rhwp":
        return local
    # auto
    if fmt == "docx":
        return remote  # only Tier 2 can produce DOCX
    return remote if remote.is_available() else local


def render_document(
    src: Path | str,
    out: Path | str,
    *,
    fmt: str = "pdf",
    engine: str = "auto",
    config: Hwp2PdfConfig | None = None,
    transport: RemoteTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
) -> RenderResult:
    """Render *src* → *out* in *fmt* using the selected tier. Never raises for
    expected failures — returns a :class:`RenderResult` with a helpful ``stderr``."""
    out = Path(out)
    be = select_render_backend(
        fmt, engine, config=config, transport=transport,
        rhwp_bin=rhwp_bin, render_fn=render_fn,
    )
    if not be.supports(fmt):
        return RenderResult(
            out, fmt, be.name, 2,
            stderr=f"engine {be.name!r} cannot produce {fmt} "
                   "(DOCX requires the hwp2pdf/namun-ji engine)",
        )
    if not be.is_available():
        if be.name == "hwp2pdf":
            hint = (
                "hwp2pdf engine unavailable — configure ~/.config/hwp-agent/hwp2pdf.json "
                "and ensure the Windows node is reachable over Tailscale/SSH"
            )
        else:
            hint = "rhwp not found — install the rhwp CLI on PATH or set $RHWP_BIN"
        return RenderResult(out, fmt, be.name, 2, stderr=hint)
    return be.render(Path(src), out, fmt=fmt)
