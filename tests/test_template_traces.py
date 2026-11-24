"""Shipped templates and written documents carry no source-document traces (#120).

A template baked from a published report keeps that report's document info
(``content.hpf``: title, creator, subject, a DRM trace in description, save dates)
and its ``Preview/PrvText.txt``·``PrvImage.png``. The repo is public and every
``write``/``build`` output starts from a template, so neither may carry them.
"""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import neutralize_template, read_metadata
from hwp_agent.ops.author import fill_from_markdown
from hwp_agent.ops.container import _rewrite_zip_preserving

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "examples" / "정책과제-template.hwpx"
HOUSE = ROOT / "examples" / "house-forms" / "정책과제_서식.normalized.hwpx"

#: planted stand-ins for a source document's info — never real values
PLANTED = {
    "title": "원본 보고서 제목",
    "creator": "원본작성자",
    "subject": "원본 주제",
    "description": "TRACE-ID-0000",
    "keyword": "원본키워드",
    "lastsaveby": "원본저장자",
    "date": "2019년 1월 1일",
}
PLANTED_PREVIEW = "원본 미리보기 본문"


def _script():
    path = ROOT / "scripts" / "neutralize-template-meta.py"
    spec = importlib.util.spec_from_file_location("neutralize_template_meta", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _planted(src: Path, tmp_path: Path) -> Path:
    """A copy of *src* whose document info and preview look like a source report's.

    Edited at the zip level (only content.hpf and the preview change), so a template
    whose untouched sections would not pass a full re-save's validation still works.
    """
    from lxml import etree

    from hwp_agent.ops.metadata import _FIELD_MAP, _OPF

    with zipfile.ZipFile(src) as z:
        root = etree.fromstring(z.read("Contents/content.hpf"))
    md = root.find(f"{{{_OPF}}}metadata")
    for field, value in PLANTED.items():
        kind, key = _FIELD_MAP[field]
        el = md.find(f"{{{_OPF}}}{key}" if kind == "elem" else f'{{{_OPF}}}meta[@name="{key}"]')
        if el is None:
            el = etree.SubElement(md, f"{{{_OPF}}}meta", {"name": key, "content": "text"})
        el.text = value
    out = tmp_path / "planted.hwpx"
    _rewrite_zip_preserving(
        src,
        out,
        {
            "Contents/content.hpf": etree.tostring(root, xml_declaration=True, encoding="UTF-8"),
            "Preview/PrvText.txt": PLANTED_PREVIEW.encode("utf-8"),
        },
    )
    return out


def _assert_no_planted(path: Path) -> None:
    md = read_metadata(path)
    for field, value in PLANTED.items():
        assert getattr(md, field) != value, f"{field} inherited from the template"
    with zipfile.ZipFile(path) as z:
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        prv = z.read("Preview/PrvText.txt").decode("utf-8")
    for value in PLANTED.values():
        assert value not in hpf
    assert PLANTED_PREVIEW not in prv


def test_neutralize_template_rewrites_only_meta_and_preview(tmp_path: Path) -> None:
    src = _planted(HOUSE, tmp_path)
    out = tmp_path / "neutral.hwpx"
    neutralize_template(src, "정책과제 서식", output=out)

    md = read_metadata(out)
    assert md.title == "정책과제 서식"
    left = (md.creator, md.subject, md.description, md.keyword, md.lastsaveby, md.date)
    assert left == (None,) * 6
    assert md.created == md.modified == "2026-01-01T00:00:00Z"
    with zipfile.ZipFile(src) as a, zipfile.ZipFile(out) as b:
        assert a.namelist() == b.namelist()
        assert [i.compress_type for i in a.infolist()] == [i.compress_type for i in b.infolist()]
        changed = {n for n in a.namelist() if a.read(n) != b.read(n)}
        assert b.read("Preview/PrvText.txt").decode("utf-8") == "정책과제 서식"
        assert b.read("Preview/PrvImage.png").startswith(b"\x89PNG")
        assert b.getinfo("Preview/PrvImage.png").file_size < 1024
    assert changed <= {"Contents/content.hpf", "Preview/PrvText.txt", "Preview/PrvImage.png"}


def test_neutralize_template_is_idempotent(tmp_path: Path) -> None:
    once = tmp_path / "once.hwpx"
    twice = tmp_path / "twice.hwpx"
    neutralize_template(_planted(HOUSE, tmp_path), "정책과제 서식", output=once)
    neutralize_template(once, "정책과제 서식", output=twice)
    assert once.read_bytes() == twice.read_bytes()


def test_write_does_not_inherit_template_document_info(tmp_path: Path) -> None:
    tpl = _planted(GOLDEN, tmp_path)
    out = tmp_path / "out.hwpx"
    fill_from_markdown(tpl, "# 새 문서\n\n본문 문장입니다.\n", output=out)
    _assert_no_planted(out)
    with zipfile.ZipFile(out) as z:
        assert "본문 문장입니다." in z.read("Preview/PrvText.txt").decode("utf-8")
        assert z.getinfo("Preview/PrvImage.png").file_size < 1024


def test_cli_write_sets_title_from_document_not_template(tmp_path: Path) -> None:
    from hwp_agent.cli.main import main

    tpl = _planted(GOLDEN, tmp_path)
    md = tmp_path / "doc.md"
    md.write_text("# 새 문서 제목\n\n본문.\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"
    argv = ["write", str(md), "--template", str(tpl), "--author", "작성자", "-o", str(out)]
    assert main(argv) == 0
    _assert_no_planted(out)
    meta = read_metadata(out)
    assert (meta.title, meta.creator) == ("새 문서 제목", "작성자")


_SHIPPED = _script().targets()


@pytest.mark.parametrize("path", _SHIPPED, ids=[p.name for p in _SHIPPED])
def test_shipped_templates_carry_no_source_traces(path: Path) -> None:
    if path == GOLDEN and _script().traces(path):
        # regenerated with scripts/neutralize-template-meta.py once PR #77 (which
        # also rewrites this binary) is merged — kept separate to avoid a conflict
        pytest.xfail("golden template is neutralized after #77 merges")
    assert _script().traces(path) == []
