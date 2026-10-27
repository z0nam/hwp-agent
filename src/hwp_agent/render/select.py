"""Engine selection: hwp2pdf-cli on this PC → hwp2pdf server → local rhwp.

Engines (``--engine`` or ``$HWP_AGENT_RENDER_ENGINE``):

- ``auto`` — the whole chain, first one that renders wins (rhwp only for PDF).
- ``hwp2pdf`` — Hancom only: this PC's CLI, else the server. Never rhwp.
- ``local`` / ``remote`` — only the CLI on this PC / only the server.
- ``rhwp`` — only rhwp (approximate layout).

Every result says which engine rendered it (``RenderResult.backend``); when a
preferred engine was skipped, why is on ``RenderResult.note``.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig, resolve_hwp2pdf_config
from .local_hwp2pdf import LocalHwp2PdfBackend
from .local_rhwp import LocalRhwpBackend
from .remote_hwp2pdf import HttpTransport, RemoteHwp2PdfBackend
from .rhwp import RenderFn

ENGINES = ("auto", "hwp2pdf", "local", "remote", "rhwp")
ENGINE_ENV = "HWP_AGENT_RENDER_ENGINE"
_CHAINS = {
    "auto": ("local", "remote", "rhwp"),
    "hwp2pdf": ("local", "remote"),
    "local": ("local",),
    "remote": ("remote",),
    "rhwp": ("rhwp",),
}
#: one-line label per backend name, for "via …" lines
ENGINE_LABELS = {
    "hwp2pdf-cli": "한컴 · 이 PC",
    "hwp2pdf": "한컴 · 변환 서버",
    "rhwp": "근사 레이아웃",
}


def resolve_engine(engine: str | None) -> str:
    """``engine`` if given, else ``$HWP_AGENT_RENDER_ENGINE``, else ``auto``."""
    value = (engine or os.environ.get(ENGINE_ENV) or "auto").strip().lower()
    if value not in ENGINES:
        raise ValueError(f"unknown render engine {value!r} (choose: {', '.join(ENGINES)})")
    return value


def _backends(
    engine: str,
    config: Hwp2PdfConfig | None,
    transport: HttpTransport | None,
    rhwp_bin: str | None,
    render_fn: RenderFn | None,
    hwp2pdf_cli: str | None,
    timeout: int | None = None,
) -> list[RenderBackend]:
    if timeout is None and config is not None:
        timeout = config.convert_timeout
    make = {
        "local": lambda: LocalHwp2PdfBackend(hwp2pdf_cli, timeout),
        "remote": lambda: RemoteHwp2PdfBackend(config, transport),
        "rhwp": lambda: LocalRhwpBackend(rhwp_bin, render_fn),
    }
    return [make[key]() for key in _CHAINS[engine]]


def _quietly_absent(be: RenderBackend, config: Hwp2PdfConfig | None) -> bool:
    """Not installed / not configured — skip without a note (the normal case on a Mac)."""
    if isinstance(be, RemoteHwp2PdfBackend):
        return config is None
    return not be.is_available()


def select_render_backend(
    fmt: str,
    engine: str | None = "auto",
    *,
    config: Hwp2PdfConfig | None = None,
    transport: HttpTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
    hwp2pdf_cli: str | None = None,
    _resolve: bool = True,
) -> RenderBackend:
    """The first backend in *engine*'s chain that can render *fmt* right now; if none
    can, the last one that supports *fmt* (its ``render`` then reports why)."""
    if config is None and _resolve:
        config = resolve_hwp2pdf_config()
    chain = [b for b in _backends(resolve_engine(engine), config, transport, rhwp_bin,
                                  render_fn, hwp2pdf_cli) if b.supports(fmt)]
    for be in chain:
        if isinstance(be, RemoteHwp2PdfBackend):
            usable = be.status(fmt).usable
        else:
            usable = be.is_available()
        if usable:
            return be
    return chain[-1] if chain else RemoteHwp2PdfBackend(config, transport)


def render_document(
    src: Path | str,
    out: Path | str,
    *,
    fmt: str = "pdf",
    engine: str | None = None,
    config: Hwp2PdfConfig | None = None,
    transport: HttpTransport | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
    hwp2pdf_cli: str | None = None,
    timeout: int | None = None,
) -> RenderResult:
    """Render *src* → *out* in *fmt*, trying *engine*'s chain in order. Never raises
    for expected failures — returns a :class:`RenderResult` with a helpful ``stderr``.

    A backend that is simply not installed / configured is skipped silently; one
    that was tried and declined (Hangul open, queue, conversion error) is skipped
    with its reason collected on ``RenderResult.note`` of the eventual success,
    or joined into ``stderr`` when nothing rendered.

    ``timeout`` (seconds) bounds the local CLI even when no server is configured;
    the server's own budget stays ``config.convert_timeout``.
    """
    out = Path(out)
    engine = resolve_engine(engine)
    if config is None:
        config = resolve_hwp2pdf_config()
    chain = _backends(engine, config, transport, rhwp_bin, render_fn, hwp2pdf_cli, timeout)
    capable = [b for b in chain if b.supports(fmt)]
    if not capable:
        return RenderResult(
            out, fmt, chain[0].name, 2,
            stderr=f"engine {engine!r} cannot produce {fmt} "
                   "(DOCX requires a Hancom engine: hwp2pdf-cli or the hwp2pdf server)",
        )

    notes: list[str] = []
    failed: RenderResult | None = None
    missing: list[RenderBackend] = []
    for be in capable:
        # an explicitly chosen single engine always reports its own reason
        if len(capable) > 1 and _quietly_absent(be, config):
            missing.append(be)
            continue
        result = be.render(Path(src), out, fmt=fmt)
        if result.ok:
            return dataclasses.replace(result, note="; ".join(notes))
        notes.append(f"{be.name} 미사용 — {result.stderr}")
        failed = result

    if failed is None:
        if fmt == "docx" or engine == "hwp2pdf":
            msg = ("한컴 엔진 없음 — 이 PC에 hwp2pdf-cli($HWP2PDF_CLI 또는 PATH)도, "
                   "hwp2pdf 서버 설정(~/.config/hwp-agent/hwp2pdf.json)도 없음")
        else:
            msg = "rhwp not found — install the rhwp CLI on PATH or set $RHWP_BIN"
        return RenderResult(out, fmt, capable[-1].name, 2, stderr=msg)
    stderr = failed.stderr if len(notes) == 1 else "; ".join(notes)
    if any(isinstance(b, LocalRhwpBackend) for b in missing):
        stderr += "; rhwp도 없음 ($RHWP_BIN 또는 PATH)"
    return dataclasses.replace(failed, stderr=stderr)
