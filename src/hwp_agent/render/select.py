"""Engine selection: hwp2pdf-serve (Hancom) when usable, else local rhwp."""

from __future__ import annotations

import dataclasses
from pathlib import Path

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig, resolve_hwp2pdf_config
from .local_rhwp import LocalRhwpBackend
from .remote_hwp2pdf import HttpTransport, RemoteHwp2PdfBackend
from .rhwp import RenderFn


def select_render_backend(
    fmt: str,
    engine: str = "auto",
    *,
    config: Hwp2PdfConfig | None = None,
    transport: HttpTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
    _resolve: bool = True,
) -> RenderBackend:
    """Pick a backend. ``engine``: ``auto`` | ``hwp2pdf`` | ``rhwp``.

    ``auto``: DOCX → always hwp2pdf (rhwp can't); PDF → hwp2pdf if usable (reachable
    and not overlapping a running Hangul / queued job), else rhwp.
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
        return remote  # only hwp2pdf can produce DOCX
    return remote if remote.is_available() else local


def render_document(
    src: Path | str,
    out: Path | str,
    *,
    fmt: str = "pdf",
    engine: str = "auto",
    config: Hwp2PdfConfig | None = None,
    transport: HttpTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
) -> RenderResult:
    """Render *src* → *out* in *fmt* using the selected engine. Never raises for
    expected failures — returns a :class:`RenderResult` with a helpful ``stderr``.

    When ``auto`` falls back from hwp2pdf to rhwp, the reason is put on
    ``RenderResult.note`` (one line) so the caller can say why.
    """
    out = Path(out)
    if config is None:
        config = resolve_hwp2pdf_config()
    remote = RemoteHwp2PdfBackend(config, transport)
    local = LocalRhwpBackend(rhwp_bin, render_fn)

    if engine == "rhwp" or (engine == "auto" and fmt == "pdf" and config is None):
        be: RenderBackend = local
    else:
        be = remote

    if not be.supports(fmt):
        return RenderResult(
            out, fmt, be.name, 2,
            stderr=f"engine {be.name!r} cannot produce {fmt} "
                   "(DOCX requires the hwp2pdf engine)",
        )

    if be is local:
        if not local.is_available():
            return RenderResult(
                out, fmt, local.name, 2,
                stderr="rhwp not found — install the rhwp CLI on PATH or set $RHWP_BIN",
            )
        return local.render(Path(src), out, fmt=fmt)

    result = remote.render(Path(src), out, fmt=fmt)
    # auto PDF: any hwp2pdf miss (unreachable, Hangul open on the node, queue,
    # conversion error) falls back to rhwp. DOCX has no local renderer.
    if result.ok or engine != "auto" or fmt != "pdf":
        return result
    if not local.is_available():
        return dataclasses.replace(
            result, stderr=f"{result.stderr}; rhwp도 없음 ($RHWP_BIN 또는 PATH)",
        )
    fallback = local.render(Path(src), out, fmt=fmt)
    return dataclasses.replace(fallback, note=f"hwp2pdf 미사용 — {result.stderr}")
