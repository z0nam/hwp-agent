#!/usr/bin/env python3
"""Strip source-document traces from the shipped templates' package parts (#120).

The templates in ``examples/`` are baked from published reports / institute forms.
Their ``Contents/content.hpf`` (title, creator, subject, a DRM trace in
``description``, save dates) and ``Preview/PrvText.txt``·``PrvImage.png`` still
describe that source file. This rewrites only those parts — title and preview text
become a neutral label, the rest is blanked, the preview image becomes a blank PNG —
and copies every other part byte-for-byte. The 과제 forms' master-page pictures that
carry the sample report's title / chapter title as pixels are swapped for cleaned
copies and the even pages get a ``{{running_title}}`` text slot instead (#95,
``ops/form_backgrounds.py``). Re-runnable: run it again after any change that
rebuilds a template (e.g. after a branch rewrites the template XML).

    python scripts/neutralize-template-meta.py            # every shipped template
    python scripts/neutralize-template-meta.py FILE.hwpx  # just these
    python scripts/neutralize-template-meta.py --check    # exit 1 if any has traces
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from hwp_agent.ops.form_backgrounds import (  # noqa: E402
    _map,
    add_running_title_slot,
    clean_form_backgrounds,
)
from hwp_agent.ops.metadata import (  # noqa: E402
    INHERITED_FIELDS,
    neutralize_template,
    read_metadata,
)


def _house_title():
    path = ROOT / "scripts" / "refresh-house-forms.py"
    spec = importlib.util.spec_from_file_location("refresh_house_forms", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.template_title


def targets() -> list[Path]:
    ex = ROOT / "examples"
    return sorted(ex.glob("*.hwpx")) + sorted((ex / "house-forms").glob("*.hwpx"))


#: shipped golden templates → neutral label; house forms derive theirs from the file name
GOLDEN = {
    "정책과제-template.hwpx": "정책과제 서식",
    "정책이슈브리프-template.hwpx": "정책이슈브리프 서식",
}


def title_for(path: Path) -> str:
    if path.name in GOLDEN:
        return GOLDEN[path.name]
    return _house_title()(path)


def traces(path: Path) -> list[str]:
    """What still describes a source file: non-neutral meta or preview."""
    want = title_for(path)
    md = read_metadata(path)
    out = [f"{f}={getattr(md, f)!r}" for f in INHERITED_FIELDS if f != "title" and getattr(md, f)]
    if md.title != want:
        out.append(f"title={md.title!r}")
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        if "Preview/PrvText.txt" in names and z.read("Preview/PrvText.txt").decode("utf-8") != want:
            out.append("Preview/PrvText.txt")
        if "Preview/PrvImage.png" in names and z.getinfo("Preview/PrvImage.png").file_size > 1024:
            out.append("Preview/PrvImage.png")
        out += [
            f"{n} (sample lettering)"
            for n in sorted(names)
            if n.startswith("BinData/") and hashlib.sha256(z.read(n)).hexdigest() in _map()
        ]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("files", nargs="*", type=Path)
    ap.add_argument("--check", action="store_true", help="report traces, change nothing")
    args = ap.parse_args()
    files = args.files or targets()
    bad = 0
    for f in files:
        left = traces(f)
        if args.check:
            # names only — the values themselves are what must not be echoed into logs
            print(f"{'TRACE' if left else 'ok   '} {f.relative_to(ROOT) if f.is_absolute() else f}"
                  + (f"  ({', '.join(x.split('=')[0] for x in left)})" if left else ""))
            bad += bool(left)
            continue
        if left:
            neutralize_template(f, title_for(f))
            swapped = clean_form_backgrounds(f)
            slots = add_running_title_slot(f) if swapped else []
            print(f"neutralized {f.name} → {title_for(f)!r}"
                  + (f"; cleaned {len(swapped)} background(s), {len(slots)} title slot(s)"
                     if swapped else ""))
        else:
            print(f"clean       {f.name}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
