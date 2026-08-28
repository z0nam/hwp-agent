"""Config for the Tier-2 remote hwp2pdf endpoint (the Windows/Hancom node).

Resolution mirrors the house idiom (``ops/template.py``, ``convert/_resolve_jar``):
explicit arg → ``$HWP2PDF_CONFIG`` → ``~/.config/hwp-agent/hwp2pdf.json`` → None
(None simply means Tier-2 is unavailable, so ``auto`` falls back to rhwp). After a
file is loaded, individual ``$HWP2PDF_*`` env vars override its fields.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".config" / "hwp-agent" / "hwp2pdf.json"


@dataclass(frozen=True)
class Hwp2PdfConfig:
    host: str  # ssh alias (e.g. "namun-ji"; resolved via ~/.ssh/config + Tailscale)
    remote_inbox: str  # forward-slash remote path, e.g. "C:/Users/user/.hwp-agent/inbox"
    remote_outbox: str  # "C:/Users/user/.hwp-agent/outbox"
    task_name: str = "hwp-agent-hwp2pdf"  # session-1 scheduled task to /run
    default_format: str = "pdf"
    connect_timeout: int = 5  # ssh -o ConnectTimeout
    convert_timeout: int = 180  # empirical ~45s; headroom for cold Hancom
    poll_interval: float = 2.0


def _apply_env(data: dict) -> dict:
    """Overlay per-field ``$HWP2PDF_*`` env vars onto a loaded config dict."""
    env_map = {
        "HWP2PDF_HOST": "host",
        "HWP2PDF_INBOX": "remote_inbox",
        "HWP2PDF_OUTBOX": "remote_outbox",
        "HWP2PDF_TASK": "task_name",
    }
    for env, key in env_map.items():
        if os.environ.get(env):
            data[key] = os.environ[env]
    if os.environ.get("HWP2PDF_TIMEOUT"):
        try:
            data["convert_timeout"] = int(os.environ["HWP2PDF_TIMEOUT"])
        except ValueError:
            pass
    return data


def resolve_hwp2pdf_config(
    explicit: str | Path | None = None,
) -> Hwp2PdfConfig | None:
    """Load the remote hwp2pdf config, or None if none is configured.

    None is not an error — it means Tier-2 is simply unavailable and ``auto``
    selection will use the local rhwp backend for PDF.
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
    # drop unknown keys (e.g. comments, exe_path used only by the worker script)
    fields = Hwp2PdfConfig.__dataclass_fields__
    known = {k: v for k, v in data.items() if k in fields}
    if not known.get("host") or not known.get("remote_inbox") or not known.get("remote_outbox"):
        return None  # not enough to reach Tier-2
    return Hwp2PdfConfig(**known)
