"""Tier-2 render backend: remote hwp2pdf on a Windows/Hancom node (namun-ji).

Hancom COM only runs in an interactive **session 1** (proven in
``docs/output-verification.md``), so a direct SSH call to ``hwp2pdf`` (session 0)
hangs. Instead the client drops the file in an inbox and triggers a
**pre-registered session-1 scheduled task** (``schtasks /run``); a worker script
on the node runs hwp2pdf, writes ``<job>.pdf``/``<job>.docx`` + a ``<job>.done``
marker to the outbox; the client polls, pulls, and cleans up.

Transport is injectable (:class:`RemoteTransport`) so tests never touch namun-ji.
The default :class:`SshTransport` reuses the ``smon`` pattern
(``ssh -o BatchMode=yes -o ConnectTimeout=N`` + ``scp``); the Windows default
shell is PowerShell, so commands go through ``powershell -EncodedCommand``.
"""

from __future__ import annotations

import base64
import subprocess
import time
import uuid
from pathlib import Path
from typing import Protocol

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig


def _ps_encoded(script: str) -> list[str]:
    """Wrap a PowerShell snippet as ``powershell -NoProfile -EncodedCommand <b64>``.

    Base64 of UTF-16LE — the robust way to pass commands to a remote PowerShell
    without cmd/PS quoting/escaping breaking (backslashes, ``&``, redirection).
    """
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return ["powershell", "-NoProfile", "-EncodedCommand", b64]


class RemoteTransport(Protocol):
    """SSH/scp transport to the Windows node. Injectable for tests."""

    def run(self, argv: list[str], *, timeout: int) -> subprocess.CompletedProcess: ...
    def push(self, local: Path, remote: str) -> None: ...
    def pull(self, remote: str, local: Path) -> None: ...


class SshTransport:
    """Default transport: OpenSSH + scp to *host* (an ``~/.ssh/config`` alias)."""

    def __init__(self, host: str, connect_timeout: int = 5) -> None:
        self._host = host
        self._base = [
            "ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={connect_timeout}", host,
        ]

    def run(self, argv: list[str], *, timeout: int) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603
            self._base + argv, capture_output=True, text=True, timeout=timeout
        )

    def push(self, local: Path, remote: str) -> None:
        subprocess.run(  # noqa: S603
            ["scp", "-o", "BatchMode=yes", str(local), f"{self._host}:{remote}"],
            capture_output=True, text=True, check=True,
        )

    def pull(self, remote: str, local: Path) -> None:
        subprocess.run(  # noqa: S603
            ["scp", "-o", "BatchMode=yes", f"{self._host}:{remote}", str(local)],
            capture_output=True, text=True, check=True,
        )


class RemoteHwp2PdfBackend(RenderBackend):
    """Round-trip an ``.hwp``/``.hwpx`` through the Hancom node for authoritative
    PDF/DOCX. See module docstring for the session-1 trigger rationale."""

    name = "hwp2pdf"
    formats = ("pdf", "docx")

    def __init__(
        self,
        config: Hwp2PdfConfig | None = None,
        transport: RemoteTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport
        self._reachable: bool | None = None

    def _tx(self) -> RemoteTransport:
        if self._transport is not None:
            return self._transport
        return SshTransport(self.config.host, self.config.connect_timeout)

    def is_available(self) -> bool:
        """Cheap: config present + one short ssh probe (cached per instance)."""
        if self.config is None:
            return False
        if self._transport is not None:
            return True  # injected transport (tests)
        if self._reachable is None:
            try:
                p = self._tx().run(
                    ["cmd", "/c", "echo ok"],
                    timeout=self.config.connect_timeout + 3,
                )
                self._reachable = p.returncode == 0
            except (subprocess.SubprocessError, OSError):
                self._reachable = False
        return self._reachable

    def render(self, src: Path, out: Path, *, fmt: str = "pdf") -> RenderResult:
        out = Path(out)
        if self.config is None:
            return RenderResult(
                out, fmt, self.name, 2,
                stderr="no hwp2pdf config — copy examples/hwp2pdf.example.json to "
                       "~/.config/hwp-agent/hwp2pdf.json",
            )
        if fmt not in self.formats:
            return RenderResult(out, fmt, self.name, 2, stderr=f"unsupported format: {fmt}")

        cfg = self.config
        tx = self._tx()
        src = Path(src)
        job = f"{src.stem}-{uuid.uuid4().hex[:8]}"
        remote_in = f"{cfg.remote_inbox}/{job}{src.suffix}"
        done = f"{cfg.remote_outbox}/{job}.done"
        errf = f"{cfg.remote_outbox}/{job}.err"
        product = f"{cfg.remote_outbox}/{job}.{fmt}"

        try:
            tx.push(src, remote_in)
            trig = tx.run(
                _ps_encoded(f'schtasks /run /tn "{cfg.task_name}"'),
                timeout=cfg.connect_timeout + 5,
            )
            if trig.returncode != 0:
                return RenderResult(
                    out, fmt, self.name, 2, remote=True,
                    stderr=f"could not trigger task {cfg.task_name!r} on {cfg.host} "
                           f"— run scripts/install-hwp2pdf-worker.ps1 there. "
                           f"{(trig.stderr or trig.stdout).strip()}",
                )

            deadline = time.monotonic() + cfg.convert_timeout
            while time.monotonic() < deadline:
                chk = tx.run(
                    _ps_encoded(
                        f'if (Test-Path "{done}") {{"done"}} '
                        f'elseif (Test-Path "{errf}") {{"err"}} else {{"wait"}}'
                    ),
                    timeout=cfg.connect_timeout + 5,
                )
                state = (chk.stdout or "").strip()
                if state == "done":
                    break
                if state == "err":
                    tail = tx.run(_ps_encoded(f'Get-Content "{errf}" -Tail 5'),
                                  timeout=cfg.connect_timeout + 5)
                    self._cleanup(tx, cfg, job)
                    return RenderResult(
                        out, fmt, self.name, 1, remote=True,
                        stderr=f"hwp2pdf failed on {cfg.host}: {(tail.stdout or '').strip()}",
                    )
                time.sleep(cfg.poll_interval)
            else:
                self._cleanup(tx, cfg, job)
                return RenderResult(
                    out, fmt, self.name, 1, remote=True,
                    stderr=f"no output within {cfg.convert_timeout}s — is {cfg.host} "
                           "logged in (session 1) and unlocked? is FilePathCheckerModule "
                           "registered? (both are required for unattended Hancom COM)",
                )

            tx.pull(product, out)
            self._cleanup(tx, cfg, job)
            return RenderResult(out, fmt, self.name, 0, remote=True)
        except subprocess.CalledProcessError as exc:
            return RenderResult(
                out, fmt, self.name, 1, remote=True,
                stderr=f"scp/ssh failed: {(exc.stderr or str(exc)).strip()}",
            )
        except (subprocess.SubprocessError, OSError) as exc:
            return RenderResult(out, fmt, self.name, 1, remote=True, stderr=str(exc))

    @staticmethod
    def _cleanup(tx: RemoteTransport, cfg: Hwp2PdfConfig, job: str) -> None:
        try:
            tx.run(
                _ps_encoded(
                    f'Remove-Item "{cfg.remote_inbox}/{job}*","{cfg.remote_outbox}/{job}*" '
                    "-Force -ErrorAction SilentlyContinue"
                ),
                timeout=cfg.connect_timeout + 5,
            )
        except (subprocess.SubprocessError, OSError):
            pass
