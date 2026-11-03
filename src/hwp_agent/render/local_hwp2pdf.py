"""Render backend: the hwp2pdf CLI on this PC (Hancom COM, Windows only).

A researcher's Windows PC with Hancom Office and hwp2pdf installed converts
locally — no server, no token, Hancom-authoritative output. The CLI is found
as ``$HWP2PDF_CLI`` → ``hwp2pdf-cli`` on PATH (installing it is jaix-setup's
job, not hwp-agent's).

**Never disturb the user's Hangul.** The CLI itself refuses to run while a
``Hwp.exe`` is open (unless ``--allow-running-hwp`` / ``--kill-hwp``, which
hwp-agent never passes); that refusal is reported as ``busy`` so the caller
moves on to the next engine.

The CLI writes ``<stem>.<ext>`` next to its input, so the source is copied into
a private temp folder first and the output moved to the requested path.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .._subproc import decode as _decode
from .base import RenderBackend, RenderResult
from .remote_hwp2pdf import Hwp2PdfStatus

DEFAULT_CLI = "hwp2pdf-cli"
DEFAULT_TIMEOUT = 900  # same whole-job budget as the conversion server
#: hwp2pdf's message when a Hangul is already open (cli.prepare_hwp_processes)
_HWP_RUNNING = "already running"
#: hwp2pdf could not bring Hangul up. Measured (Hancom 2022, 2026-10-30): with the
#: first-run consent window up, COM gives up after ~2 min with CO_E_SERVER_EXEC_FAILURE
#: ("서버 실행에 실패했습니다", -2146959355) and the CLI says only that it could not start.
_START_FAILED = (
    "한글을 시작하지 못했습니다", "Could not start Hancom", "서버 실행에 실패", "-2146959355",
)
CONSENT_HINT = "한컴 첫 실행 동의창이 떠 있을 수 있음 — 한글을 한 번 열어 답하세요"
# env the CLI would read to go *remote*; this backend is the local engine
_REMOTE_ENV = ("HWP2PDF_SERVER_URL", "HWP2PDF_TOKEN")
_FLAGS = {"pdf": "--pdf", "docx": "--docx"}


def _is_windows() -> bool:
    return os.name == "nt"


def resolve_hwp2pdf_cli(explicit: str | None = None) -> str | None:
    """Find the hwp2pdf CLI: explicit arg > ``$HWP2PDF_CLI`` > ``hwp2pdf-cli`` on PATH."""
    for cand in (explicit, os.environ.get("HWP2PDF_CLI"), DEFAULT_CLI):
        if not cand:
            continue
        if Path(cand).is_file():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def _tail(text: str, n: int = 3) -> str:
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    return " | ".join(lines[-n:])


class LocalHwp2PdfBackend(RenderBackend):
    """Hancom-authoritative PDF/DOCX through ``hwp2pdf-cli`` on this PC."""

    name = "hwp2pdf-cli"
    formats = ("pdf", "docx")

    def __init__(self, cli: str | None = None, timeout: int | None = None) -> None:
        self._cli = cli
        if timeout is None:
            try:
                timeout = int(os.environ.get("HWP2PDF_TIMEOUT") or DEFAULT_TIMEOUT)
            except ValueError:
                timeout = DEFAULT_TIMEOUT
        self.timeout = timeout

    def status(self) -> Hwp2PdfStatus:
        if not _is_windows():
            return Hwp2PdfStatus(False, "hwp2pdf-cli는 Windows(한컴오피스)에서만 씀")
        if resolve_hwp2pdf_cli(self._cli) is None:
            return Hwp2PdfStatus(False, "hwp2pdf-cli 없음 ($HWP2PDF_CLI 또는 PATH)")
        return Hwp2PdfStatus(True)

    def is_available(self) -> bool:
        return self.status().usable

    def render(self, src: Path, out: Path, *, fmt: str = "pdf") -> RenderResult:
        out = Path(out)
        if fmt not in _FLAGS:
            return RenderResult(out, fmt, self.name, 2, stderr=f"unsupported format: {fmt}")
        st = self.status()
        if not st.usable:
            return RenderResult(out, fmt, self.name, 2, stderr=st.reason)
        cli = resolve_hwp2pdf_cli(self._cli)
        src = Path(src)

        env = {k: v for k, v in os.environ.items() if k not in _REMOTE_ENV}
        env["HWP2PDF_LOCAL"] = "1"  # newer hwp2pdf: ignore a server saved in its GUI
        env["PYTHONIOENCODING"] = "utf-8"
        kwargs: dict = {}
        if _is_windows():
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        with tempfile.TemporaryDirectory(prefix="hwp_agent_h2p_") as td:
            staged = Path(td) / f"doc{src.suffix.lower()}"
            shutil.copyfile(src, staged)
            produced = staged.with_suffix(f".{fmt}")
            # --timeout: hwp2pdf restarts its *own* Hangul if one file hangs (e.g.
            # a security prompt); the outer timeout is the last-resort guard.
            cmd = [cli, str(staged), _FLAGS[fmt], "--timeout", str(self.timeout)]
            try:
                proc = subprocess.run(  # noqa: S603
                    cmd, capture_output=True, env=env, timeout=self.timeout + 60, **kwargs,
                )
            except subprocess.TimeoutExpired:
                return RenderResult(
                    out, fmt, self.name, 1,
                    stderr=f"hwp2pdf-cli가 {self.timeout}초 안에 끝나지 않음 — 한글 창에 "
                           "응답이 필요할 수 있음(예: 첫 실행 '사용성 데이터 수집 동의'). "
                           "이 PC 화면을 확인",
                )
            except OSError as exc:
                return RenderResult(out, fmt, self.name, 2, stderr=f"hwp2pdf-cli 실행 실패: {exc}")

            stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
            text = f"{stdout}\n{stderr}"
            if proc.returncode != 0 or not produced.is_file():
                busy = _HWP_RUNNING in text
                if busy:
                    reason = "이 PC에서 한글이 실행 중 — 사용자 작업과 겹치지 않도록 쓰지 않음"
                else:
                    reason = f"hwp2pdf-cli 변환 실패 (exit {proc.returncode}): {_tail(text)}"
                    if any(m in text for m in _START_FAILED):
                        reason += f" — {CONSENT_HINT}"
                return RenderResult(
                    out, fmt, self.name, proc.returncode or 1,
                    stdout=stdout, stderr=reason, busy=busy,
                )
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(produced), str(out))
        return RenderResult(out, fmt, self.name, 0, stdout=stdout)
