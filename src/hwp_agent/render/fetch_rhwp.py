"""Download the pinned rhwp release binary (the fallback render engine).

rhwp (github.com/edwardkim/rhwp, MIT) renders HWP/HWPX → PDF without Hancom. It is
the last engine in the ``auto`` chain — what renders a preview when Hangul is open
on this PC (hwp2pdf-cli skips) and no hwp2pdf server is configured.

The version and each asset's sha256 are pinned here, like the converter jar in
:mod:`hwp_agent.convert.fetch_jar`: an hwp-agent commit fixes the rhwp build it
uses, and ``jaix setup`` reads these constants from the locked commit to pre-fetch
the same file. ``hwp-agent setup`` downloads it into the per-user data dir
(``%LOCALAPPDATA%\\hwp-agent\\rhwp`` / ``~/.local/share/hwp-agent/rhwp``) next to
rhwp's own LICENSE. Pure stdlib. Set ``RHWP_RELEASE_URL`` to a mirror holding the
same asset names to override the source.
"""

from __future__ import annotations

import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

from ..convert.fetch_jar import _sha256, data_dir

RHWP_REPO = "edwardkim/rhwp"
RHWP_VERSION = "v0.8.7"
RELEASE_URL = os.environ.get(
    "RHWP_RELEASE_URL",
    f"https://github.com/{RHWP_REPO}/releases/download/{RHWP_VERSION}",
)
# asset per platform slug → sha256, copied from the release's SHA256SUMS.txt
RHWP_ASSETS = {
    "windows-x86_64": (
        f"rhwp-{RHWP_VERSION}-windows-x86_64.zip",
        "f7ac326e98f595ae8fecd8da7a614ee9a37db77bdf938782a8e07c9da473ec6f",
    ),
    "macos-aarch64": (
        f"rhwp-{RHWP_VERSION}-macos-aarch64.tar.gz",
        "6ed46aa63f6d10c447842999f73abc59cf9586c5645fefc5e763bd6a6d43662d",
    ),
    "macos-x86_64": (
        f"rhwp-{RHWP_VERSION}-macos-x86_64.tar.gz",
        "07b1a5ea570e410d1b52b4d848a94f00c64674e84004883c62efad7daab61da4",
    ),
    "linux-x86_64": (
        f"rhwp-{RHWP_VERSION}-linux-x86_64.tar.gz",
        "f1e541a25cfe76b6bc1daf18bf9c1f9a5ac5e03e2c01b188daaf54107214f631",
    ),
    "linux-aarch64": (
        f"rhwp-{RHWP_VERSION}-linux-aarch64.tar.gz",
        "c4a70f5ed11b99586ee89d1ac9069145d58a0831d00e87858bb01626e2ebf313",
    ),
}


def platform_slug() -> str | None:
    """This machine's release slug, or None when rhwp ships no build for it."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform.startswith("win"):
        return "windows-x86_64"  # Windows on ARM runs the x64 build under emulation
    if sys.platform == "darwin":
        return "macos-aarch64" if arm else "macos-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-aarch64" if arm else "linux-x86_64"
    return None


def rhwp_dir() -> Path:
    return data_dir() / "rhwp"


def rhwp_dest() -> Path:
    """Where ``hwp-agent setup`` puts the binary (checked by ``resolve_rhwp``)."""
    exe = "rhwp.exe" if sys.platform.startswith("win") else "rhwp"
    return rhwp_dir() / exe


def installed_version() -> str | None:
    marker = rhwp_dir() / "VERSION"
    if not (marker.is_file() and rhwp_dest().is_file()):
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def _members(archive: bytes, asset: str, exe: str) -> dict[str, bytes]:
    """The binary and LICENSE from the release archive (top-level ``rhwp/`` dir).

    Reads members by exact name only — nothing else in the archive is extracted."""
    want = {f"rhwp/{exe}": exe, "rhwp/LICENSE": "LICENSE"}
    out: dict[str, bytes] = {}
    if asset.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            for name, dest in want.items():
                if name in zf.namelist():
                    out[dest] = zf.read(name)
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tf:
            for name, dest in want.items():
                try:
                    fh = tf.extractfile(name)
                except KeyError:
                    continue
                if fh is not None:
                    out[dest] = fh.read()
    if exe not in out:
        raise RuntimeError(f"rhwp/{exe} not found in {asset}")
    return out


def download_rhwp(*, force: bool = False, url: str | None = None) -> Path:
    """Download the pinned rhwp into the user data dir; return the binary's path.

    Skips the download when the pinned version is already there (unless ``force``).
    Always verifies the archive's sha256 against :data:`RHWP_ASSETS`.
    """
    slug = platform_slug()
    if slug is None:
        raise RuntimeError(f"rhwp ships no prebuilt binary for {sys.platform}/{platform.machine()}")
    dest = rhwp_dest()
    if not force and installed_version() == RHWP_VERSION:
        return dest
    asset, sha = RHWP_ASSETS[slug]
    src = url or f"{RELEASE_URL}/{asset}"
    with urllib.request.urlopen(src) as resp:  # noqa: S310
        archive = resp.read()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(asset + ".part")
    part.write_bytes(archive)
    try:
        got = _sha256(part)
        if got != sha:
            raise RuntimeError(f"rhwp checksum mismatch for {asset}: expected {sha}, got {got}")
        files = _members(archive, asset, dest.name)
    finally:
        part.unlink(missing_ok=True)
    for name, data in files.items():
        tmp = dest.parent / (name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest.parent / name)
    if not sys.platform.startswith("win"):
        dest.chmod(0o755)
    (dest.parent / "VERSION").write_text(RHWP_VERSION + "\n", encoding="utf-8")
    return dest
