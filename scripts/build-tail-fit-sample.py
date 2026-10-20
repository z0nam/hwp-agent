"""Build ``tests/data/tail_fit_sample.hwpx`` — the committed widow-tail fixture (issue #39).

A handful of body paragraphs written onto the bundled default template, each
trimmed against rhwp's layout so its last line holds an exact number of
characters. Paragraph roles (looked up by their leading tag in the tests):

  [S] one-line opener holding the section's secPr            -> not a candidate
  [A] no linesegarray, rhwp tail 1 char                      -> fittable
  [B] no linesegarray, rhwp tail 3 chars                     -> fittable
  [C] linesegarray that matches rhwp's own layout, tail 2    -> fittable
  [D] stale linesegarray (claims 3 lines / tail 2) while rhwp
      lays the text out in 2 lines with a long tail          -> layout_mismatch
  [E] no linesegarray, long tail                             -> not a candidate

[D] is the PR #45 regression: measuring the baseline with the saved
linesegarray but the variants without it credited the lineseg removal as a
spacing/ratio effect ("-1줄" for nothing).

Needs rhwp (``$RHWP_BIN`` or PATH). Re-run only when the fixture must change:

    uv run python scripts/build-tail-fit-sample.py tests/data/tail_fit_sample.hwpx
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

from hwp_agent.cli.main import main as cli_main
from hwp_agent.ops.fit_tails import _hp, _para_text, _parse_dump_pages
from hwp_agent.render.rhwp import resolve_rhwp

SECTION = "Contents/section0.xml"
TEMPLATE = Path(__file__).resolve().parents[1] / "src/hwp_agent/assets/default-template.hwpx"
_DECL = b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'

_FILLER = (
    "지역 공공서비스의 품질을 높이기 위해서는 현장의 요구를 꾸준히 듣고 그 결과를 "
    "제도와 예산에 차례로 반영하는 과정이 필요하며 이를 위한 점검 체계도 함께 갖추어야 한다"
)

# tag -> (rhwp tail chars wanted, keep text this long before trimming)
ROLES = {
    "[S]": (None, 16),  # one line; carries secPr, so it is never a candidate
    "[A]": (1, 120),
    "[B]": (3, 120),
    "[C]": (2, 70),
    "[D]": (None, 70),
    "[E]": (None, 120),
}


def _dump(path: Path, rhwp: str) -> str:
    res = subprocess.run([rhwp, "dump-pages", str(path)], capture_output=True, text=True)
    res.check_returncode()
    return res.stdout


def _layout(path: Path, rhwp: str):
    return _parse_dump_pages(_dump(path, rhwp))


def _line_metrics(dump: str, pi: int) -> dict[str, str]:
    """rhwp's first-line metrics for top-level paragraph ``pi`` of section 0."""
    m = re.search(rf"Paragraph\s+pi={pi}\b.*?first ts=0 lh=(\d+) th=(\d+) bl=(\d+) gap=(\d+) "
                  r"cs=\d+ sw=(\d+)", dump)
    assert m, pi
    return dict(zip(("lh", "th", "bl", "gap", "sw"), m.groups(), strict=True))


def _texts() -> dict[str, str]:
    base = (_FILLER + " ") * 3
    return {tag: (tag + " " + base)[:n] for tag, (_, n) in ROLES.items()}


def _write(texts: dict[str, str], dst: Path) -> None:
    with tempfile.TemporaryDirectory() as td:
        md = Path(td) / "content.md"
        md.write_text("\n\n".join(texts.values()) + "\n", encoding="utf-8")
        if cli_main(["write", str(md), "--template", str(TEMPLATE), "-o", str(dst)]) != 0:
            raise SystemExit("hwp-agent write failed")


def _top_index(path: Path) -> dict[str, int]:
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read(SECTION))
    out = {}
    for pi, p in enumerate(root.findall(_hp("p"))):
        text = _para_text(p)
        for tag in ROLES:
            if text.startswith(tag):
                out[tag] = pi
    return out


def _lineseg(textpos: int, line: int, m: dict[str, str]) -> str:
    vertpos = line * (int(m["lh"]) + int(m["gap"]))
    return (
        f'<hp:lineseg textpos="{textpos}" vertpos="{vertpos}" vertsize="{m["lh"]}" '
        f'textheight="{m["th"]}" baseline="{m["bl"]}" spacing="{m["gap"]}" horzpos="0" '
        f'horzsize="{m["sw"]}" flags="393216"/>'
    )


def build(dst: Path, rhwp: str) -> None:
    texts = _texts()
    with tempfile.TemporaryDirectory() as td:
        draft = Path(td) / "draft.hwpx"
        # Trim each paragraph until rhwp's last line holds exactly the wanted tail.
        for _ in range(5):
            _write(texts, draft)
            lay = _layout(draft, rhwp)
            idx = _top_index(draft)
            done = True
            for tag, (want, _) in ROLES.items():
                para = lay[(0, idx[tag])]
                tail = len(texts[tag]) - para.last_ts
                if want is not None and tail != want:
                    texts[tag] = texts[tag][: para.last_ts + want]
                    done = False
            if done:
                break
        else:
            raise SystemExit("could not settle the tails")

        dump = _dump(draft, rhwp)
        lay = _parse_dump_pages(dump)
        idx = _top_index(draft)
        with zipfile.ZipFile(draft) as z:
            root = etree.fromstring(z.read(SECTION))
        tops = root.findall(_hp("p"))

        def set_lsa(tag: str, textposes: list[int]) -> None:
            p = tops[idx[tag]]
            m = _line_metrics(dump, idx[tag])
            segs = "".join(_lineseg(tp, i, m) for i, tp in enumerate(textposes))
            ns = _hp("")[1:-1]
            p.append(etree.fromstring(f'<hp:linesegarray xmlns:hp="{ns}">{segs}</hp:linesegarray>'))

        c = lay[(0, idx["[C]"])]
        assert c.lines == 2, c
        set_lsa("[C]", [0, c.last_ts])

        d_len = len(texts["[D]"])
        d = lay[(0, idx["[D]"])]
        assert d.lines == 2 and d_len - d.last_ts > 4, d
        set_lsa("[D]", [0, d.last_ts // 2, d_len - 2])

        # Keep only the role paragraphs (the first carries secPr) — drop the
        # template's example table / figure so the fixture stays small.
        keep = {id(tops[i]) for i in idx.values()}
        for p in tops:
            if id(p) not in keep:
                root.remove(p)
        _finish(draft, dst, _DECL + etree.tostring(root, encoding="UTF-8"))


def _finish(draft: Path, dst: Path, section: bytes) -> None:
    """Write the fixture without the template's figure image or hwp-agent fingerprint."""
    drop = {"BinData/image1.png", "META-INF/hwpagent.sha256"}
    with zipfile.ZipFile(draft) as zin, zipfile.ZipFile(dst, "w") as zout:
        hpf = zin.read("Contents/content.hpf").decode("utf-8")
        hpf = re.sub(r'<opf:item id="image1"[^>]*/>', "", hpf)
        for info in zin.infolist():
            if info.filename in drop:
                continue
            data = {SECTION: section, "Contents/content.hpf": hpf.encode("utf-8")}.get(
                info.filename
            )
            zout.writestr(info, data if data is not None else zin.read(info.filename))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    rhwp_bin = resolve_rhwp()
    if not rhwp_bin:
        raise SystemExit("rhwp not found — set $RHWP_BIN or put rhwp on PATH")
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    build(out, rhwp_bin)
    print(f"wrote {out}")
