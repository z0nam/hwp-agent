"""Shared fixtures.

Every test runs with hwp2pdf *unconfigured* (issue #61): a developer machine may
have a real ``~/.config/hwp-agent/hwp2pdf.json`` pointing at a live Hancom node,
and the suite must never reach it. Tests that want hwp2pdf start
:class:`FakeHwp2Pdf` — a real local HTTP server speaking the hwp2pdf-serve
protocol (API 1) — and point ``$HWP2PDF_SERVER_URL`` at it.
"""

from __future__ import annotations

import json
import re
import threading
import zipfile
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest
from lxml import etree

TOKEN = "test-token-not-secret"


@pytest.fixture(autouse=True)
def _isolate_hwp2pdf(monkeypatch, tmp_path_factory) -> None:
    monkeypatch.setenv(
        "HWP2PDF_CONFIG", str(tmp_path_factory.getbasetemp() / "no-hwp2pdf.json")
    )
    monkeypatch.setenv("HWP2PDF_NO_LOCAL", "1")  # never probe a real local server
    # nor read the token a real "hwp2pdf serve --init" saved on this (Windows) machine
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path_factory.getbasetemp() / "no-localappdata"))
    # never run a real hwp2pdf-cli found on PATH
    monkeypatch.setenv("HWP2PDF_CLI", str(tmp_path_factory.getbasetemp() / "no-hwp2pdf-cli"))
    monkeypatch.setattr("hwp_agent.render.local_hwp2pdf.DEFAULT_CLI", "no-such-hwp2pdf-cli")
    for var in ("HWP2PDF_SERVER_URL", "HWP2PDF_TOKEN", "HWP2PDF_TOKEN_FILE", "HWP2PDF_TIMEOUT",
                "HWP_AGENT_RENDER_ENGINE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(autouse=True)
def _isolate_rhwp_download(monkeypatch, tmp_path_factory) -> None:
    """`hwp-agent setup` also fetches rhwp: never reach the network or install it
    into the developer's real data dir (tests that want a download pass a file:// url)."""
    base = tmp_path_factory.getbasetemp()
    monkeypatch.setenv("XDG_DATA_HOME", str(base / "no-xdg-data"))
    monkeypatch.setattr("hwp_agent.render.fetch_rhwp.RELEASE_URL",
                        (base / "no-rhwp-release").as_uri())


@pytest.fixture(autouse=True)
def _isolate_profile(monkeypatch, tmp_path_factory) -> None:
    """write/build fall back to the profile's name for the document creator (#32);
    never let the suite read the developer's real ~/.config/hwp-agent/profile.json."""
    monkeypatch.setenv(
        "HWP_AGENT_PROFILE", str(tmp_path_factory.getbasetemp() / "no-profile.json")
    )


def resave_with_linesegs(data: bytes, tail: int = 2, laid_out: int | None = None) -> bytes:
    """Fake "Hangul re-save": give every paragraph lacking a linesegarray a
    three-line layout whose last line holds ``tail`` characters.

    ``laid_out`` mimics Hangul saving after laying out only the first pages
    (#65): only the first ``laid_out`` text paragraphs get a layout, the rest
    are saved with none."""
    from hwp_agent.ops.fit_tails import _HP, _para_text

    src = zipfile.ZipFile(BytesIO(data))
    out = BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            blob = src.read(info.filename)
            if re.search(r"Contents/section\d+\.xml$", info.filename):
                root = etree.fromstring(blob)
                seen = 0
                for p in root.iter(f"{{{_HP}}}p"):
                    text = _para_text(p)
                    if not text:
                        continue
                    seen += 1
                    if laid_out is not None and seen > laid_out:
                        lsa = p.find(f"{{{_HP}}}linesegarray")
                        if lsa is not None:
                            p.remove(lsa)
                        continue
                    if p.find(f"{{{_HP}}}linesegarray") is not None:
                        continue
                    lsa = etree.SubElement(p, f"{{{_HP}}}linesegarray")
                    last = max(len(text) - tail, 2)
                    for pos in (0, last // 2, last):
                        etree.SubElement(lsa, f"{{{_HP}}}lineseg", textpos=str(pos))
                blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            dst.writestr(info, blob)
    return out.getvalue()


class FakeHwp2Pdf:
    """In-process hwp2pdf-serve stand-in. Mutate the attributes to steer it."""

    def __init__(self) -> None:
        self.hwp_running: list[str] = []
        self.queue_depth = 0
        self.hwp_installed = True
        self.formats = ["DOCX", "HWPX", "PDF"]
        self.fail_item: str | None = None  # message -> item reports "failed"
        self.convert: Callable[[bytes, str], bytes] = self._default_convert
        self.requests: list[tuple[str, str]] = []
        self.run_payloads: list[dict] = []
        self.deleted: list[str] = []
        self._inputs: dict[str, bytes] = {}
        self._outputs: dict[str, bytes] = {}
        self._events: dict[str, list[dict]] = {}
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )
        self._thread.start()

    @staticmethod
    def _default_convert(data: bytes, fmt: str) -> bytes:
        if fmt == "HWPX":
            return resave_with_linesegs(data)
        return b"%PDF-1.4\n%%EOF\n" if fmt == "PDF" else b"PK\x03\x04docx"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a) -> None:  # quiet
                pass

            def _send(self, code: int, payload=None, raw: bytes | None = None) -> None:
                body = raw if raw is not None else json.dumps(payload or {}).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authed(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                    return True
                self._send(401, {"error": "unauthorized"})
                return False

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _route(self, method: str) -> None:
                path = self.path.split("?")[0]
                fake.requests.append((method, path))
                if path == "/v1/health":
                    return self._send(200, {"app": "hwp2pdf", "version": "test",
                                            "api": 1, "auth_required": True})
                if not self._authed():
                    return None
                if path == "/v1/capabilities":
                    return self._send(200, {
                        "os": "nt", "hwp_installed": fake.hwp_installed,
                        "hwp_detail": "fake", "hwp_running": fake.hwp_running,
                        "formats": fake.formats, "queue_depth": fake.queue_depth,
                    })
                if method == "POST" and path == "/v1/jobs":
                    self._body()
                    jid = f"job{len(fake._events) + 1}"
                    fake._events[jid] = []
                    return self._send(201, {"job_id": jid, "queue_depth": 0})
                m = re.match(r"/v1/jobs/([^/]+)(/.*)?$", path)
                if not m:
                    return self._send(404, {"error": "route"})
                jid, rest = m.group(1), m.group(2) or ""
                if method == "DELETE" and not rest:
                    fake.deleted.append(jid)
                    return self._send(204, raw=b"")
                if method == "PUT" and rest.startswith("/inputs/"):
                    fake._inputs[jid + rest[8:]] = self._body()
                    return self._send(204, raw=b"")
                rm = re.match(r"/items/([^/]+)/run$", rest)
                if method == "POST" and rm:
                    item = rm.group(1)
                    payload = json.loads(self._body() or b"{}")
                    fake.run_payloads.append(payload)
                    fmt = payload["output_format"]
                    if fmt == "HWPX" and payload["name"].lower().endswith(".hwpx"):
                        return self._send(400, {"error": "HWPX input is already HWPX"})
                    if fake.fail_item:
                        ev = {"kind": "item", "item": item, "status": "failed",
                              "message": fake.fail_item}
                    else:
                        fake._outputs[jid + item] = fake.convert(fake._inputs[jid + item], fmt)
                        ev = {"kind": "item", "item": item, "status": "ok", "actual": fmt}
                    fake._events[jid] += [{"kind": "log", "text": "started"}, ev]
                    return self._send(202, {"queue_depth": 0})
                if method == "GET" and rest == "/events":
                    evs = fake._events.get(jid, [])
                    return self._send(200, {"events": evs, "cursor": len(evs),
                                            "cancelled": False, "queue_depth": 0})
                if method == "GET" and rest.startswith("/outputs/"):
                    data = fake._outputs.get(jid + rest[9:])
                    if data is None:
                        return self._send(404, {"error": "no output"})
                    return self._send(200, raw=data)
                if method == "POST" and rest == "/cancel":
                    return self._send(200, {})
                return self._send(404, {"error": "route"})

            def do_GET(self) -> None:
                self._route("GET")

            def do_POST(self) -> None:
                self._route("POST")

            def do_PUT(self) -> None:
                self._route("PUT")

            def do_DELETE(self) -> None:
                self._route("DELETE")

        return H


@pytest.fixture
def fake_hwp2pdf(monkeypatch) -> Iterator[FakeHwp2Pdf]:
    """A running fake hwp2pdf-serve, wired in through the environment."""
    fake = FakeHwp2Pdf()
    monkeypatch.setenv("HWP2PDF_SERVER_URL", fake.url)
    monkeypatch.setenv("HWP2PDF_TOKEN", TOKEN)
    try:
        yield fake
    finally:
        fake.close()


@pytest.fixture
def no_rhwp(monkeypatch, tmp_path) -> None:
    """Make rhwp undiscoverable ($RHWP_BIN to a missing file; PATH irrelevant)."""
    monkeypatch.setenv("RHWP_BIN", str(tmp_path / "missing-rhwp"))
