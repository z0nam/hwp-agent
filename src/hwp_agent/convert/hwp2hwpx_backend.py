"""hwp2hwpx backend: shells out to the vendored fat jar.

The jar is built by ``scripts/bootstrap.sh`` from neolord0/hwp2hwpx plus our
thin ``Hwp2HwpxCli`` entry point, and lands at ``vendor/hwp2hwpx.jar``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .base import ConverterBackend, ConvertResult

# <repo>/src/hwp_agent/convert/hwp2hwpx_backend.py -> parents[3] == <repo>
_DEFAULT_JAR = Path(__file__).resolve().parents[3] / "vendor" / "hwp2hwpx.jar"


def _resolve_jar(explicit: Path | str | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("HWP2HWPX_JAR")
    return Path(env) if env else _DEFAULT_JAR


class Hwp2HwpxBackend(ConverterBackend):
    name = "hwp2hwpx"

    def __init__(
        self,
        jar_path: Path | str | None = None,
        java_bin: str | None = None,
    ) -> None:
        self.jar_path = _resolve_jar(jar_path)
        self.java_bin = java_bin or os.environ.get("JAVA_BIN") or "java"

    def is_available(self) -> bool:
        return self.jar_path.is_file() and shutil.which(self.java_bin) is not None

    def convert(self, hwp_path: Path | str, hwpx_path: Path | str) -> ConvertResult:
        hwp_path = Path(hwp_path)
        hwpx_path = Path(hwpx_path)

        if not hwp_path.is_file():
            raise FileNotFoundError(f"input HWP not found: {hwp_path}")
        if not self.jar_path.is_file():
            raise FileNotFoundError(
                f"hwp2hwpx jar not found at {self.jar_path}; run scripts/bootstrap.sh"
            )

        hwpx_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [self.java_bin, "-jar", str(self.jar_path), str(hwp_path), str(hwpx_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
        return ConvertResult(
            hwpx_path=hwpx_path,
            backend=self.name,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
