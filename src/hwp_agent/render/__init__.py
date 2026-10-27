"""HWP/HWPX → PDF/DOCX rendering (a new axis, separate from ``convert/``).

Engines, in ``auto`` order: ``hwp2pdf-cli`` (Hancom on this Windows PC),
``hwp2pdf`` (Hancom via the ``hwp2pdf-serve`` HTTP API) and ``rhwp`` (local,
PDF only, approximate) — behind one selection (:func:`render_document`). See
``docs/output-verification.md``.
"""

from __future__ import annotations

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig, resolve_hwp2pdf_config
from .local_hwp2pdf import LocalHwp2PdfBackend, resolve_hwp2pdf_cli
from .local_rhwp import LocalRhwpBackend
from .remote_hwp2pdf import (
    HttpTransport,
    Hwp2PdfClient,
    Hwp2PdfError,
    Hwp2PdfStatus,
    RemoteHwp2PdfBackend,
)
from .rhwp import RenderFn, resolve_rhwp, rhwp_render_fn
from .select import (
    ENGINE_LABELS,
    ENGINES,
    render_document,
    resolve_engine,
    select_render_backend,
)

__all__ = [
    "RenderBackend",
    "RenderResult",
    "RenderFn",
    "resolve_rhwp",
    "rhwp_render_fn",
    "LocalRhwpBackend",
    "LocalHwp2PdfBackend",
    "resolve_hwp2pdf_cli",
    "RemoteHwp2PdfBackend",
    "Hwp2PdfClient",
    "Hwp2PdfError",
    "Hwp2PdfStatus",
    "HttpTransport",
    "Hwp2PdfConfig",
    "resolve_hwp2pdf_config",
    "ENGINES",
    "ENGINE_LABELS",
    "resolve_engine",
    "select_render_backend",
    "render_document",
]
