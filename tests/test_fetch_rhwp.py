"""rhwp download (``hwp-agent setup``): pinned asset, sha256 check, data-dir lookup."""

from __future__ import annotations

import hashlib
import io
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from hwp_agent.render import fetch_rhwp
from hwp_agent.render.rhwp import resolve_rhwp

LICENSE = b"MIT License\n"


def _zip(exe: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(f"rhwp/{exe}", payload)
        zf.writestr("rhwp/LICENSE", LICENSE)
        zf.writestr("rhwp/README.md", b"not extracted")
    return buf.getvalue()


def _tgz(exe: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for name, data in ((f"rhwp/{exe}", payload), ("rhwp/LICENSE", LICENSE)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.fixture
def release(tmp_path: Path, monkeypatch):
    """A fake release dir (file:// URL) holding this platform's asset; data dir in tmp."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "local"))
    monkeypatch.delenv("RHWP_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty-path"))
    slug = fetch_rhwp.platform_slug()
    asset = fetch_rhwp.RHWP_ASSETS[slug][0]
    exe = fetch_rhwp.rhwp_dest().name
    data = (_zip if asset.endswith(".zip") else _tgz)(exe, b"fake rhwp")
    rel = tmp_path / "rel"
    rel.mkdir()
    (rel / asset).write_bytes(data)
    monkeypatch.setitem(fetch_rhwp.RHWP_ASSETS, slug, (asset, hashlib.sha256(data).hexdigest()))
    return rel / asset


def test_every_asset_is_pinned_to_the_version() -> None:
    for asset, sha in fetch_rhwp.RHWP_ASSETS.values():
        assert fetch_rhwp.RHWP_VERSION in asset and len(sha) == 64


def test_download_extracts_binary_and_license(release: Path) -> None:
    dest = fetch_rhwp.download_rhwp(url=release.as_uri())
    assert dest == fetch_rhwp.rhwp_dest() and dest.read_bytes() == b"fake rhwp"
    assert (dest.parent / "LICENSE").read_bytes() == LICENSE  # MIT notice ships with it
    assert not (dest.parent / "README.md").exists()
    assert fetch_rhwp.installed_version() == fetch_rhwp.RHWP_VERSION
    assert not list(dest.parent.glob("*.part"))


def test_download_skips_when_pinned_version_present(release: Path) -> None:
    fetch_rhwp.download_rhwp(url=release.as_uri())
    release.unlink()  # a second fetch would fail
    assert fetch_rhwp.download_rhwp(url=release.as_uri()).is_file()


def test_download_rejects_checksum_mismatch(release: Path, monkeypatch) -> None:
    slug = fetch_rhwp.platform_slug()
    monkeypatch.setitem(fetch_rhwp.RHWP_ASSETS, slug, (release.name, "0" * 64))
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        fetch_rhwp.download_rhwp(url=release.as_uri())
    assert not fetch_rhwp.rhwp_dest().exists()
    assert not list(fetch_rhwp.rhwp_dir().glob("*.part"))


def test_resolve_falls_back_to_downloaded_copy(release: Path, monkeypatch) -> None:
    assert resolve_rhwp() is None
    fetch_rhwp.download_rhwp(url=release.as_uri())
    assert resolve_rhwp() == str(fetch_rhwp.rhwp_dest())
    # an explicit $RHWP_BIN stays final, even when it points nowhere
    monkeypatch.setenv("RHWP_BIN", str(release.parent / "missing"))
    assert resolve_rhwp() is None


@pytest.mark.skipif(sys.platform.startswith("win"), reason="exec bit is POSIX only")
def test_downloaded_binary_is_executable(release: Path) -> None:
    import os

    assert os.access(fetch_rhwp.download_rhwp(url=release.as_uri()), os.X_OK)


def test_setup_warns_but_succeeds_without_rhwp(release: Path, monkeypatch, capsys) -> None:
    import importlib

    cli = importlib.import_module("hwp_agent.cli.main")

    monkeypatch.setattr(fetch_rhwp, "RELEASE_URL", (release.parent / "gone").as_uri())
    cli._setup_rhwp(force=False, diag_out=None)
    assert "warn: could not download rhwp" in capsys.readouterr().err
    assert not fetch_rhwp.rhwp_dest().exists()
