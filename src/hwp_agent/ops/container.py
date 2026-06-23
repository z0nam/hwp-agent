"""Container-preserving HWPX zip rewrite.

Hangul (보안수준 '높음') treats a re-zipped HWPX as tampered unless the package
keeps its original entry order, compression and flags — so edits replace part
bytes inside the original container instead of re-packing from scratch.
"""

from __future__ import annotations

import zipfile
from pathlib import Path


def _read_text(zf: zipfile.ZipFile, name: str) -> str:
    return zf.read(name).decode("utf-8")


def _rewrite_zip_preserving(
    src: str | Path, dst: str | Path, overrides: dict[str, bytes]
) -> None:
    """Copy *src* to *dst* replacing only the named parts, keeping the container intact.

    Each entry is re-emitted with its original :class:`zipfile.ZipInfo` (order and
    compression preserved); ``mimetype`` stays first and ``STORED``. This is what keeps
    Hangul from treating the edited file as tampered (보안경고).
    """
    with zipfile.ZipFile(src) as zin:
        infos = zin.infolist()
        with zipfile.ZipFile(dst, "w") as zout:
            for info in infos:
                data = overrides.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                # reuse the source ZipInfo so order/compression/flags are preserved
                zout.writestr(info, data)
