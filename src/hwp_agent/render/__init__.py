"""HWP/HWPX → PDF/DOCX rendering (a new axis, separate from ``convert/``).

Tier 1 ``rhwp`` (local, PDF) and Tier 2 ``hwp2pdf`` (Hancom-authoritative,
PDF+DOCX, remote) behind one selection (:func:`render_document`). See
``docs/output-verification.md``.
"""

from __future__ import annotations

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig, resolve_hwp2pdf_config
from .local_rhwp import LocalRhwpBackend
from .remote_hwp2pdf import RemoteHwp2PdfBackend, RemoteTransport, SshTransport
from .rhwp import RenderFn, resolve_rhwp, rhwp_render_fn
from .select import render_document, select_render_backend

__all__ = [
    "RenderBackend",
    "RenderResult",
    "RenderFn",
    "resolve_rhwp",
    "rhwp_render_fn",
    "LocalRhwpBackend",
    "RemoteHwp2PdfBackend",
    "RemoteTransport",
    "SshTransport",
    "Hwp2PdfConfig",
    "resolve_hwp2pdf_config",
    "select_render_backend",
    "render_document",
]
