"""Download the prebuilt hwp2hwpx converter jar from a GitHub release.

Windows (and any non-developer) install path: building the jar needs JDK + Maven,
a high bar. Instead we publish a prebuilt jar as a GitHub release asset and fetch
it into a per-user data directory on demand (``hwp-agent setup``). Only a Java
*runtime* (JRE 17+) is then needed to run it.

Pure stdlib (``urllib``) — no new dependency. Set ``HWP2HWPX_JAR_URL`` to override
the source (e.g. an internal mirror).
"""

from __future__ import annotations

import hashlib
import os
import sys
import urllib.request
from pathlib import Path

# Release asset published from scripts/bootstrap.sh output. Override via env.
_RELEASE_TAG = "converter-jar"
_ASSET = "hwp2hwpx.jar"
JAR_URL = os.environ.get(
    "HWP2HWPX_JAR_URL",
    f"https://github.com/z0nam/hwp-agent/releases/download/{_RELEASE_TAG}/{_ASSET}",
)
# Filled in once the asset is published (sha256 of the released jar). Empty =
# skip verification (with a warning).
JAR_SHA256 = ""


def data_dir() -> Path:
    """Per-user data directory for hwp-agent (XDG / %LOCALAPPDATA%)."""
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")
    return Path(base) / "hwp-agent"


def jar_dest() -> Path:
    return data_dir() / _ASSET


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def download_jar(*, force: bool = False, url: str | None = None) -> Path:
    """Download the converter jar to the user data dir; return its path.

    Skips the download if the jar already exists (unless ``force``). Verifies the
    SHA-256 when a known checksum is compiled in.
    """
    dest = jar_dest()
    if dest.is_file() and not force:
        return dest
    src = url or JAR_URL
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(src) as resp, tmp.open("wb") as out:  # noqa: S310
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            out.write(chunk)
    if JAR_SHA256:
        got = _sha256(tmp)
        if got != JAR_SHA256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(
                f"converter jar checksum mismatch: expected {JAR_SHA256}, got {got}"
            )
    tmp.replace(dest)
    return dest
