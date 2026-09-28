"""Container-preserving HWPX zip rewrite.

Edits replace only the bytes of the parts we actually change and re-emit every
other entry with its original :class:`zipfile.ZipInfo` (order, compression,
flags) — so parts we don't understand are never disturbed. This is a
**fidelity** guarantee: the output differs from the input only where we meant it
to.

Note (issue #9, measured 2026-08-28): a *full* re-zip that keeps every part and
puts ``mimetype`` first + STORED does **not** by itself trip Hangul's 보안경고 —
a container-preserved file and a fully re-zipped one both opened cleanly and
rendered pixel-identically (macOS Hangul, 보안수준 '높음'; a Windows strict
verdict is still the last word). The real tamper trigger is a **missing part** or
a **DOM re-serialization** (what ``HwpxDocument.save_to_path`` does), not the
re-zip itself. We preserve the container for fidelity, not to dodge a warning.
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
    compression preserved); ``mimetype`` stays first and ``STORED``. Parts we don't
    touch stay byte-identical — the output differs only where we meant it to (a
    fidelity guarantee; see the module docstring on the 보안경고 question).
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
