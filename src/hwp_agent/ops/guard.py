"""Output overwrite-guard: never silently clobber an externally-edited file.

The problem (real incident): hwp-agent writes ``out.hwpx``; the user opens it in
Hangul, inserts figures, saves; a later hwp-agent run regenerates ``out.hwpx`` and
the manual work is gone — outputs were written unconditionally
(``cli/main.py``: *"output paths are never checked"*).

Mechanism — **provenance fingerprint embedded inside the .hwpx** (no sidecar, no
folder clutter, travels with the file): a hidden OPC part
``META-INF/hwpagent.sha256`` holds the sha256 of *all other parts*. After each
write we stamp it; before each write we compare.

Policy (decided 2026-06-23):
- target absent  → write.
- target is ours, untouched (stored fp == current content hash) → overwrite.
- target drifted (stored fp differs) or foreign (no fp) → **write to a versioned
  name** (``out_v2.hwpx`` …), leaving the existing file intact. Never clobber.

Verified compatible with python-hwpx, rhwp, and Hangul (the extra part is ignored
on open; a Hangul re-save rewrites the package → fp drops/mismatches → drift is
detected, which is exactly what we want).
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

#: hidden OPC part holding the provenance fingerprint
FINGERPRINT_PART = "META-INF/hwpagent.sha256"


@dataclass
class GuardResult:
    target: Path  # where the caller should actually write
    versioned: bool  # True if redirected to a _vN name to avoid clobbering
    reason: str  # new | overwrite-own | versioned-drift | versioned-foreign

    def as_dict(self) -> dict:
        return {"target": str(self.target), "versioned": self.versioned, "reason": self.reason}


def _read_parts(path: Path) -> dict[str, bytes] | None:
    """Return {name: bytes} for an OPC/zip file, or None if it isn't a zip."""
    if not path.is_file() or not zipfile.is_zipfile(path):
        return None
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i.filename) for i in zf.infolist()}


def _content_hash(parts: dict[str, bytes]) -> str:
    """sha256 over every part except the fingerprint part, order-independent."""
    h = hashlib.sha256()
    for name in sorted(k for k in parts if k != FINGERPRINT_PART):
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        h.update(parts[name])
    return h.hexdigest()


def read_stored_fingerprint(path: str | Path) -> str | None:
    """The sha256 hwp-agent recorded in *path*, or None if absent/not a package."""
    parts = _read_parts(Path(path))
    if not parts or FINGERPRINT_PART not in parts:
        return None
    try:
        return str(json.loads(parts[FINGERPRINT_PART]).get("sha256")) or None
    except (ValueError, AttributeError):
        return None


def current_content_hash(path: str | Path) -> str | None:
    """Hash of *path*'s current content (excluding the fp part), or None."""
    parts = _read_parts(Path(path))
    return _content_hash(parts) if parts else None


def is_ours_untouched(path: str | Path) -> bool:
    """True iff *path* carries our fingerprint and its content still matches it."""
    stored = read_stored_fingerprint(path)
    return stored is not None and stored == current_content_hash(path)


def next_versioned_path(path: str | Path) -> Path:
    """``b.hwpx`` → first free of ``b_v2.hwpx``, ``b_v3.hwpx``, …"""
    p = Path(path)
    n = 2
    while True:
        cand = p.with_name(f"{p.stem}_v{n}{p.suffix}")
        if not cand.exists():
            return cand
        n += 1


def plan_output(path: str | Path, *, force: bool = False) -> GuardResult:
    """Decide where to write *path* without clobbering an externally-edited file.

    Call this *before* writing; write to ``result.target``; then
    :func:`stamp_fingerprint` the written file.
    """
    p = Path(path)
    if not p.exists():
        return GuardResult(p, False, "new")
    if force:
        return GuardResult(p, False, "overwrite-own")
    if is_ours_untouched(p):
        return GuardResult(p, False, "overwrite-own")
    reason = "versioned-drift" if read_stored_fingerprint(p) else "versioned-foreign"
    return GuardResult(next_versioned_path(p), True, reason)


def stamp_fingerprint(path: str | Path) -> bool:
    """Embed/refresh the provenance fingerprint in a just-written .hwpx.

    Rewrites the package with ``mimetype`` first + stored (HWPX/OPC convention).
    No-op (returns False) if *path* isn't a zip package.
    """
    p = Path(path)
    parts = _read_parts(p)
    if parts is None:
        return False
    parts.pop(FINGERPRINT_PART, None)
    fp = {
        "sha256": _content_hash(parts),
        "tool": "hwp-agent",
        "v": 1,
        "written_at": datetime.now().isoformat(timespec="seconds"),
    }
    parts[FINGERPRINT_PART] = json.dumps(fp, ensure_ascii=False).encode("utf-8")

    order = (
        (["mimetype"] if "mimetype" in parts else [])
        + [n for n in parts if n not in ("mimetype", FINGERPRINT_PART)]
        + [FINGERPRINT_PART]
    )
    tmp = p.with_name(p.name + ".fp.tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in order:
            if name == "mimetype":
                info = zipfile.ZipInfo("mimetype")
                info.compress_type = zipfile.ZIP_STORED
                zf.writestr(info, parts[name])
            else:
                zf.writestr(name, parts[name])
    tmp.replace(p)
    return True
