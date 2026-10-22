"""Config for the hwp2pdf conversion server (``hwp2pdf-serve``, issue #61).

hwp2pdf is a separate project; hwp-agent only *calls* its HTTP API (protocol:
z0nam/hwp2pdf ``docs/protocol.md``). Resolution mirrors the house idiom
(``ops/template.py``, ``convert/_resolve_jar``): explicit arg → ``$HWP2PDF_CONFIG``
→ ``~/.config/hwp-agent/hwp2pdf.json`` → None (None simply means hwp2pdf is not
configured, so callers fall back to rhwp). After a file is loaded, individual
``$HWP2PDF_*`` env vars override its fields.

The bearer token is never stored in this object: :meth:`Hwp2PdfConfig.token`
reads ``$HWP2PDF_TOKEN`` or the file named by ``token_file`` at call time, so
the secret stays out of the repo, reprs and logs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path.home() / ".config" / "hwp-agent" / "hwp2pdf.json"
DEFAULT_PORT = 17650  # hwp2pdf ``protocol.DEFAULT_PORT``
#: Probed when nothing is configured: an ``hwp2pdf serve`` on this very PC.
LOCAL_URL = f"http://127.0.0.1:{DEFAULT_PORT}"
_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class Hwp2PdfConfig:
    server_url: str  # e.g. "http://namun-ji.tail0fdba8.ts.net:17650"
    token_file: str | None = None  # plain-text file holding the bearer token
    connect_timeout: int = 10  # health / capabilities / small requests
    convert_timeout: int = 900  # whole job; matches the server's own job timeout
    auto_local: bool = False  # not configured; probing :data:`LOCAL_URL` on a guess

    def token(self) -> str | None:
        """``$HWP2PDF_TOKEN`` > ``token_file`` > (loopback only) the token the local
        ``hwp2pdf serve --init`` saved > None."""
        env = os.environ.get("HWP2PDF_TOKEN", "").strip()
        if env:
            return env
        files = [Path(self.token_file).expanduser()] if self.token_file else []
        if urlsplit(self.server_url).hostname in _LOOPBACK and os.environ.get("LOCALAPPDATA"):
            files.append(Path(os.environ["LOCALAPPDATA"]) / "hwp2pdf" / "server_token.txt")
        for f in files:
            try:
                text = f.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                return text
        return None


def normalize_server_url(value: str) -> str:
    """Accept what the hwp2pdf client accepts: full URL, ``host`` or ``host:port``."""
    value = value.strip().rstrip("/")
    if "://" not in value:
        value = f"http://{value}"
    parts = urlsplit(value)
    if parts.port is None and parts.scheme == "http":
        value = f"{parts.scheme}://{parts.hostname}:{DEFAULT_PORT}{parts.path}"
    return value


def _apply_env(data: dict) -> dict:
    """Overlay per-field ``$HWP2PDF_*`` env vars onto a loaded config dict."""
    if os.environ.get("HWP2PDF_SERVER_URL"):
        data["server_url"] = os.environ["HWP2PDF_SERVER_URL"]
    if os.environ.get("HWP2PDF_TOKEN_FILE"):
        data["token_file"] = os.environ["HWP2PDF_TOKEN_FILE"]
    if os.environ.get("HWP2PDF_TIMEOUT"):
        try:
            data["convert_timeout"] = int(os.environ["HWP2PDF_TIMEOUT"])
        except ValueError:
            pass
    return data


def resolve_hwp2pdf_config(
    explicit: str | Path | None = None,
) -> Hwp2PdfConfig | None:
    """Load the hwp2pdf server config.

    With no ``server_url`` anywhere, returns a guess at a server on this PC
    (:data:`LOCAL_URL`, ``auto_local=True``) — e.g. a researcher's Windows PC
    running ``hwp2pdf serve`` — unless ``$HWP2PDF_NO_LOCAL`` is set, in which case
    None (hwp2pdf unavailable; callers use rhwp). Keys of the retired
    SSH/schtasks transport (``host``, ``remote_inbox``, …) are ignored.
    """
    path = (
        Path(explicit)
        if explicit
        else Path(os.environ["HWP2PDF_CONFIG"])
        if os.environ.get("HWP2PDF_CONFIG")
        else DEFAULT_CONFIG
    )
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid hwp2pdf config {path}: {exc}") from exc
    data = _apply_env(data)
    fields = Hwp2PdfConfig.__dataclass_fields__
    known = {k: v for k, v in data.items() if k in fields}
    known.pop("auto_local", None)
    if not known.get("server_url"):
        if os.environ.get("HWP2PDF_NO_LOCAL"):
            return None
        known.setdefault("connect_timeout", 3)
        return Hwp2PdfConfig(server_url=LOCAL_URL, auto_local=True, **known)
    known["server_url"] = normalize_server_url(str(known["server_url"]))
    return Hwp2PdfConfig(**known)
