"""hwp2hwpx backend: shells out to the vendored fat jar.

The jar is built by ``scripts/bootstrap.sh`` from neolord0/hwp2hwpx plus our
thin ``Hwp2HwpxCli`` entry point, and lands at ``vendor/hwp2hwpx.jar``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .base import ConverterBackend, ConvertResult

# <repo>/src/hwp_agent/convert/hwp2hwpx_backend.py -> parents[3] == <repo>
_DEFAULT_JAR = Path(__file__).resolve().parents[3] / "vendor" / "hwp2hwpx.jar"
# When frozen by PyInstaller (the Windows single-exe), the jar is bundled next to
# the unpacked code at sys._MEIPASS.
_BUNDLED_JAR = Path(getattr(sys, "_MEIPASS", "")) / "hwp2hwpx.jar"

_OCF_NS = "urn:oasis:names:tc:opendocument:xmlns:container"
_SECTION_RE = re.compile(r"Contents/section\d+\.xml$")
_LINESPACING_RE = re.compile(r'type="PERCENT" value="(\d+)" unit="HWPUNIT"')
# A real line-spacing percentage is never four digits; hwp2hwpx inflates some
# by exactly 10x. Anything at/above this is treated as the 10x bug.
_LINESPACING_MAX = 1000


def _fix_linespacing(header_xml: str) -> tuple[str, int]:
    """Undo hwp2hwpx's 10x line-spacing inflation (see docs/findings.md)."""
    count = 0

    def repl(m: re.Match[str]) -> str:
        nonlocal count
        value = int(m.group(1))
        if value >= _LINESPACING_MAX:
            count += 1
            return f'type="PERCENT" value="{value // 10}" unit="HWPUNIT"'
        return m.group(0)

    return _LINESPACING_RE.sub(repl, header_xml), count


def _normalize_hwpx(hwpx_path: Path) -> tuple[str, ...]:
    """Repair known hwp2hwpx output defects, in place. See docs/findings.md.

    Each fix relies only on information preserved in the converted file:

    * ``preview:<path>`` — add empty placeholders for ``Preview/`` rootfiles that
      ``container.xml`` declares but hwp2hwpx never writes (else strict OPC
      readers such as python-hwpx reject the package).
    * ``linespacing:<n>`` — divide back 10x-inflated ``PERCENT`` line spacings.
    * ``pagebreak:<n>`` — restore table ``pageBreak="CELL"`` (hwp2hwpx rewrites it
      as ``"TABLE"``, collapsing full-page border frames). Safe by string match:
      paragraphs use ``pageBreak="0"/"1"``; only tables use the TABLE/CELL/NONE
      enum.

    Returns a tuple of ``"<fix>:<detail>"`` tags describing what changed (empty
    if nothing did). The character-loss bug (non-BMP -> U+FFFD) is *not* fixable
    here — it originates in hwplib at read time; see docs/findings.md.
    """
    with zipfile.ZipFile(hwpx_path) as zf:
        names = [i.filename for i in zf.infolist()]
        data = {n: zf.read(n) for n in names}

    changes: list[str] = []

    # (1) Preview rootfiles declared in container.xml but missing.
    container = data.get("META-INF/container.xml")
    if container is not None:
        nameset = set(names)
        for rf in ET.fromstring(container).iter(f"{{{_OCF_NS}}}rootfile"):
            fp = rf.get("full-path")
            if fp and fp not in nameset and fp.startswith("Preview/"):
                data[fp] = b""
                names.append(fp)
                changes.append(f"preview:{fp}")

    # (2) 10x line-spacing inflation in header.xml.
    header = data.get("Contents/header.xml")
    if header is not None:
        fixed, n = _fix_linespacing(header.decode("utf-8"))
        if n:
            data["Contents/header.xml"] = fixed.encode("utf-8")
            changes.append(f"linespacing:{n}")

    # (3) Table pageBreak CELL -> TABLE swap, across all sections.
    pagebreaks = 0
    for name in names:
        if not _SECTION_RE.search(name):
            continue
        text = data[name].decode("utf-8")
        c = text.count('pageBreak="TABLE"')
        if c:
            data[name] = text.replace('pageBreak="TABLE"', 'pageBreak="CELL"').encode(
                "utf-8"
            )
            pagebreaks += c
    if pagebreaks:
        changes.append(f"pagebreak:{pagebreaks}")

    if not changes:
        return ()

    # Rewrite the package: mimetype first and stored, per the HWPX/OPC convention.
    tmp = hwpx_path.with_name(hwpx_path.name + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        if "mimetype" in data:
            info = zipfile.ZipInfo("mimetype")
            info.compress_type = zipfile.ZIP_STORED
            zf.writestr(info, data["mimetype"])
        for name in names:
            if name != "mimetype":
                zf.writestr(name, data[name])
    tmp.replace(hwpx_path)
    return tuple(changes)


def _resolve_jar(explicit: Path | str | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("HWP2HWPX_JAR")
    if env:
        return Path(env)
    if getattr(sys, "frozen", False) and _BUNDLED_JAR.is_file():
        return _BUNDLED_JAR  # the Windows single-exe ships the jar inside
    if _DEFAULT_JAR.is_file():  # editable checkout with a built vendor jar
        return _DEFAULT_JAR
    # pipx/git install: the jar lives in the per-user data dir (hwp-agent setup).
    from .fetch_jar import jar_dest

    downloaded = jar_dest()
    return downloaded if downloaded.is_file() else _DEFAULT_JAR


class Hwp2HwpxBackend(ConverterBackend):
    name = "hwp2hwpx"

    def __init__(
        self,
        jar_path: Path | str | None = None,
        java_bin: str | None = None,
        normalize: bool = True,
    ) -> None:
        self.jar_path = _resolve_jar(jar_path)
        self.java_bin = java_bin or os.environ.get("JAVA_BIN") or "java"
        # Patch up hwp2hwpx's incomplete package (see _normalize_hwpx).
        self.normalize = normalize

    def is_available(self) -> bool:
        return self.jar_path.is_file() and shutil.which(self.java_bin) is not None

    def convert(self, hwp_path: Path | str, hwpx_path: Path | str) -> ConvertResult:
        hwp_path = Path(hwp_path)
        hwpx_path = Path(hwpx_path)

        if not hwp_path.is_file():
            raise FileNotFoundError(f"input HWP not found: {hwp_path}")
        if not self.jar_path.is_file():
            raise FileNotFoundError(
                f"hwp2hwpx jar not found at {self.jar_path}; "
                "run `hwp-agent setup` to download it (or scripts/bootstrap.sh to build)."
            )

        hwpx_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.java_bin, "-jar", str(self.jar_path), str(hwp_path), str(hwpx_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603

        normalized: tuple[str, ...] = ()
        if self.normalize and proc.returncode == 0 and hwpx_path.is_file():
            normalized = _normalize_hwpx(hwpx_path)

        return ConvertResult(
            hwpx_path=hwpx_path,
            backend=self.name,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            normalized=normalized,
        )
