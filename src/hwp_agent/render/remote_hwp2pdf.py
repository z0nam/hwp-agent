"""hwp2pdf render backend: an HTTP client for ``hwp2pdf-serve`` (issue #61).

hwp2pdf is a separate project. Its conversion server owns the Hancom COM engine
on a Windows node and converts one file at a time; hwp-agent only calls the
HTTP API (z0nam/hwp2pdf ``docs/protocol.md``, API 1)::

    GET  /v1/health                         (no auth) reachable? which API?
    GET  /v1/capabilities                   hwp_installed, hwp_running, queue_depth, formats
    POST /v1/jobs                           -> job_id
    PUT  /v1/jobs/{id}/inputs/{item}        raw bytes
    POST /v1/jobs/{id}/items/{item}/run     {name, output_format, force_one_page}
    GET  /v1/jobs/{id}/events?cursor=N&wait=S
    GET  /v1/jobs/{id}/outputs/{item}
    DELETE /v1/jobs/{id}

This replaces the old scp + ``schtasks /run hwp-agent-hwp2pdf`` worker, which
drove Hancom COM next to the server and collided with it (RPC -2147023174).

**Never disturb the user's Hangul.** The server reports the ``Hwp.exe``
processes it sees (``hwp_running``) and its queue (``queue_depth``). If either
is non-empty the node is treated as busy and hwp2pdf is not used at all — the
caller falls back to rhwp. hwp-agent never asks the server to close Hangul.

The HTTP transport is injectable (:class:`HttpTransport`) so tests never touch
the network; the default uses :mod:`urllib` (stdlib only).
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .base import RenderBackend, RenderResult
from .config import Hwp2PdfConfig

API_VERSION = 1
EVENT_WAIT = 25  # server clamps ``wait`` to 25 s
JOB_TIMEOUT = 15  # job create / run / delete (hwp2pdf's own client uses 15 s)
TRANSFER_TIMEOUT = 120  # upload / download
RETRY_DELAYS = (1, 2, 4)  # event polling survives a brief network drop
_ITEM = "00001"

# hwp-agent format name -> hwp2pdf ``output_format``
FORMATS = {"pdf": "PDF", "docx": "DOCX", "hwpx": "HWPX"}

NOT_CONFIGURED = (
    "hwp2pdf 서버 미설정 (~/.config/hwp-agent/hwp2pdf.json 의 server_url 또는 "
    "$HWP2PDF_SERVER_URL)"
)


class Hwp2PdfError(RuntimeError):
    """A conversion that was attempted and failed (message is user-facing)."""

    def __init__(self, message: str, *, busy: bool = False) -> None:
        super().__init__(message)
        self.busy = busy


class HttpTransport(Protocol):
    """One HTTP exchange -> ``(status, body)``. Raises ``OSError`` if unreachable."""

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, bytes]: ...


def urllib_transport(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


@dataclass(frozen=True)
class Hwp2PdfStatus:
    """Whether hwp2pdf may be used right now, and if not, why (one line)."""

    usable: bool
    reason: str = ""
    busy: bool = False  # the node is fine but occupied (Hangul open / queue)
    capabilities: dict = field(default_factory=dict)


def _json(body: bytes) -> dict:
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _detail(body: bytes) -> str:
    data = _json(body)
    return str(data.get("error") or data.get("message") or body[:200].decode("utf-8", "replace"))


class Hwp2PdfClient:
    """Talks to one ``hwp2pdf-serve``. :meth:`status` is cached per instance."""

    def __init__(
        self,
        config: Hwp2PdfConfig | None,
        transport: HttpTransport | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or urllib_transport
        self._status: Hwp2PdfStatus | None = None

    # -- plumbing ----------------------------------------------------------
    def _call(
        self,
        method: str,
        path: str,
        *,
        payload: dict | None = None,
        data: bytes | None = None,
        timeout: float | None = None,
        auth: bool = True,
    ) -> tuple[int, bytes]:
        cfg = self.config
        headers: dict[str, str] = {}
        body = data
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = "application/octet-stream"
            headers["Content-Length"] = str(len(data))
        if auth:
            token = cfg.token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
        return self._transport(
            method, cfg.server_url + path, headers=headers, body=body,
            timeout=timeout or cfg.connect_timeout,
        )

    # -- availability --------------------------------------------------------
    def status(self, fmt: str | None = None) -> Hwp2PdfStatus:
        """Reachable, authorised, Hangul installed, and *not* overlapping anything.

        Overlap = ``hwp_running`` non-empty (a user's Hangul, or another engine)
        or ``queue_depth > 0``. Either way hwp2pdf is skipped, never forced.
        """
        if self._status is None:
            self._status = self._probe()
        st = self._status
        if st.usable and fmt is not None:
            formats = st.capabilities.get("formats") or []
            if FORMATS.get(fmt, fmt.upper()) not in formats:
                return Hwp2PdfStatus(
                    False, f"hwp2pdf 서버가 {fmt.upper()} 출력을 지원하지 않음 "
                    f"(지원: {', '.join(formats) or '없음'})", capabilities=st.capabilities,
                )
        return st

    def _probe(self) -> Hwp2PdfStatus:
        cfg = self.config
        if cfg is None:
            return Hwp2PdfStatus(False, NOT_CONFIGURED)
        url = cfg.server_url
        try:
            code, body = self._call("GET", "/v1/health", auth=False)
        except OSError as exc:
            return Hwp2PdfStatus(False, f"hwp2pdf 서버에 연결 못 함 ({url}): {exc}")
        if code != 200:
            return Hwp2PdfStatus(False, f"hwp2pdf 서버 health 응답 {code} ({url})")
        health = _json(body)
        if health.get("api") != API_VERSION:
            return Hwp2PdfStatus(
                False, f"hwp2pdf 서버 프로토콜이 다름 (서버 API {health.get('api')}, "
                f"hwp-agent는 API {API_VERSION})",
            )
        if health.get("auth_required", True) and not cfg.token():
            return Hwp2PdfStatus(
                False, "hwp2pdf 토큰 없음 ($HWP2PDF_TOKEN 또는 hwp2pdf.json 의 token_file)",
            )
        try:
            code, body = self._call("GET", "/v1/capabilities")
        except OSError as exc:
            return Hwp2PdfStatus(False, f"hwp2pdf 서버에 연결 못 함 ({url}): {exc}")
        if code == 401:
            return Hwp2PdfStatus(False, "hwp2pdf 서버 인증 실패 (토큰 불일치)")
        if code != 200:
            return Hwp2PdfStatus(False, f"hwp2pdf capabilities 응답 {code}: {_detail(body)}")
        caps = _json(body)
        if not caps.get("hwp_installed"):
            return Hwp2PdfStatus(
                False, f"hwp2pdf 서버에 한글 없음 ({caps.get('hwp_detail', '')})",
                capabilities=caps,
            )
        running = caps.get("hwp_running") or []
        if running:
            return Hwp2PdfStatus(
                False,
                f"hwp2pdf 서버 머신에서 한글이 실행 중 (Hwp.exe {len(running)}개) — "
                "사용자 작업과 겹치지 않도록 쓰지 않음",
                busy=True, capabilities=caps,
            )
        queue = int(caps.get("queue_depth") or 0)
        if queue > 0:
            return Hwp2PdfStatus(
                False,
                f"hwp2pdf 서버가 다른 변환을 처리 중 (대기 {queue}건) — 겹치지 않도록 쓰지 않음",
                busy=True, capabilities=caps,
            )
        return Hwp2PdfStatus(True, capabilities=caps)

    # -- conversion ------------------------------------------------------------
    def convert(self, src: Path, out: Path, fmt: str) -> None:
        """Convert *src* to *out* as *fmt* (``pdf``/``docx``/``hwpx``).

        Raises :class:`Hwp2PdfError` on any failure. The server job is always
        deleted afterwards so no engine session is left behind.
        """
        cfg = self.config
        if cfg is None:
            raise Hwp2PdfError(NOT_CONFIGURED)
        if fmt not in FORMATS:
            raise Hwp2PdfError(f"hwp2pdf: 지원하지 않는 형식 {fmt}")
        src, out = Path(src), Path(out)
        output_format = FORMATS[fmt]
        item = f"{_ITEM}-{output_format}"
        # The server refuses ``*.hwpx -> HWPX`` by name (its output would overwrite
        # the staged input). Hangul's Open() detects the format from the content,
        # so an HWPX re-save goes up under the bare stem.
        name = src.name
        if output_format == "HWPX" and src.suffix.lower() == ".hwpx":
            name = src.stem

        stage = "작업 생성"
        try:
            code, body = self._call("POST", "/v1/jobs", payload={"lang": "ko", "safe_temp": True},
                                    timeout=JOB_TIMEOUT)
        except OSError as exc:
            raise Hwp2PdfError(f"hwp2pdf {stage} 실패 (연결): {exc}") from None
        if code == 429:
            raise Hwp2PdfError("hwp2pdf 서버 대기열이 가득 참", busy=True)
        job_id = _json(body).get("job_id") if code in (200, 201) else None
        if not job_id:
            raise Hwp2PdfError(f"hwp2pdf {stage} 실패 ({code}): {_detail(body)}")

        base = f"/v1/jobs/{job_id}"
        try:
            stage = "업로드"
            self._expect(
                self._call("PUT", f"{base}/inputs/{item}", data=src.read_bytes(),
                           timeout=TRANSFER_TIMEOUT),
                (200, 201, 204), stage,
            )
            stage = "변환 요청"
            code, body = self._call(
                "POST", f"{base}/items/{item}/run",
                payload={"name": name, "output_format": output_format, "force_one_page": True},
                timeout=JOB_TIMEOUT,
            )
            if code == 429:
                raise Hwp2PdfError("hwp2pdf 서버 대기열이 가득 참", busy=True)
            self._expect((code, body), (200, 202), stage)

            stage = "진행 조회"
            event = self._await_item(base, item, cfg.convert_timeout)
            if event.get("status") != "ok":
                msg = event.get("message") or event.get("status") or "알 수 없는 오류"
                raise Hwp2PdfError(f"hwp2pdf 변환 실패: {msg}")

            stage = "결과 내려받기"
            code, body = self._call("GET", f"{base}/outputs/{item}", timeout=TRANSFER_TIMEOUT)
            self._expect((code, body), (200,), stage)
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_name(out.name + ".part")
            tmp.write_bytes(body)
            os.replace(tmp, out)
        except OSError as exc:
            raise Hwp2PdfError(f"hwp2pdf {stage} 실패 (통신): {exc}") from None
        finally:
            try:
                self._call("DELETE", base, timeout=JOB_TIMEOUT)
            except OSError:
                pass

    @staticmethod
    def _expect(resp: tuple[int, bytes], ok: tuple[int, ...], what: str) -> None:
        code, body = resp
        if code not in ok:
            raise Hwp2PdfError(f"hwp2pdf {what} 실패 ({code}): {_detail(body)}")

    def _await_item(self, base: str, item: str, timeout: int) -> dict:
        """Drain the job's event log until *item* reports, or cancel on timeout."""
        deadline = time.monotonic() + timeout
        cursor = 0
        while time.monotonic() < deadline:
            wait = max(0, min(EVENT_WAIT, int(deadline - time.monotonic())))
            code, body = self._poll(f"{base}/events?cursor={cursor}&wait={wait}", wait)
            if code != 200:
                raise Hwp2PdfError(f"hwp2pdf 진행 조회 실패 ({code}): {_detail(body)}")
            page = _json(body)
            cursor = page.get("cursor", cursor)
            for ev in page.get("events") or []:
                if ev.get("kind") == "item" and ev.get("item") == item:
                    return ev
        try:
            self._call("POST", f"{base}/cancel", payload={}, timeout=JOB_TIMEOUT)
        except OSError:
            pass
        raise Hwp2PdfError(f"hwp2pdf 변환이 {timeout}초 안에 끝나지 않음")

    def _poll(self, path: str, wait: int) -> tuple[int, bytes]:
        """One events long-poll, retried on transient network errors (cursor-safe)."""
        for delay in (*RETRY_DELAYS, None):
            try:
                return self._call("GET", path, timeout=wait + self.config.connect_timeout)
            except OSError:
                if delay is None:
                    raise
                time.sleep(delay)
        raise AssertionError("unreachable")


class RemoteHwp2PdfBackend(RenderBackend):
    """Hancom-authoritative PDF/DOCX through ``hwp2pdf-serve``."""

    name = "hwp2pdf"
    formats = ("pdf", "docx")

    def __init__(
        self,
        config: Hwp2PdfConfig | None = None,
        transport: HttpTransport | None = None,
        client: Hwp2PdfClient | None = None,
    ) -> None:
        self.client = client or Hwp2PdfClient(config, transport)
        self.config = self.client.config

    def status(self, fmt: str | None = None) -> Hwp2PdfStatus:
        return self.client.status(fmt)

    def is_available(self) -> bool:
        return self.client.status().usable

    def render(self, src: Path, out: Path, *, fmt: str = "pdf") -> RenderResult:
        out = Path(out)
        if fmt not in self.formats:
            return RenderResult(out, fmt, self.name, 2, stderr=f"unsupported format: {fmt}")
        st = self.client.status(fmt)
        if not st.usable:
            return RenderResult(out, fmt, self.name, 1 if st.busy else 2,
                                remote=True, busy=st.busy, stderr=st.reason)
        try:
            self.client.convert(Path(src), out, fmt)
        except Hwp2PdfError as exc:
            return RenderResult(out, fmt, self.name, 1, remote=True, busy=exc.busy,
                                stderr=str(exc))
        return RenderResult(out, fmt, self.name, 0, remote=True)
