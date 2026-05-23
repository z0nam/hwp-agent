"""Tests for hwp_agent.ops.author (Markdown -> styled fill)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops import fill_from_markdown, parse_markdown, role_map

REPO_ROOT = Path(__file__).resolve().parents[1]
TYPE1 = REPO_ROOT / "tests" / "fixtures" / "sample_hwpx.hwpx"


def test_parse_markdown_blocks() -> None:
    md = "# 제목\n\n본문 한 줄.\n이어지는 줄.\n\n- 항목 하나\n- 항목 둘\n\n## 소제목\n"
    blocks = parse_markdown(md)
    kinds = [(b.kind, b.level) for b in blocks]
    assert kinds == [
        ("heading", 1),
        ("paragraph", 0),  # the two consecutive lines merge into one paragraph
        ("bullet", 1),
        ("bullet", 1),
        ("heading", 2),
    ]
    assert blocks[1].text == "본문 한 줄. 이어지는 줄."


def test_parse_markdown_flattens_inline_emphasis() -> None:
    (block,) = parse_markdown("**굵게** 그리고 *기울임*")
    assert block.text == "굵게 그리고 기울임"


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_from_markdown_applies_outline_styles(tmp_path: Path) -> None:
    md = "# 새 장\n\n본문 문단.\n\n## 새 절\n\n- 불릿 항목\n"
    out = tmp_path / "authored.hwpx"
    result = fill_from_markdown(TYPE1, md, output=out)
    assert result.placed == 4
    assert result.unmapped_roles == []

    from hwpx.document import HwpxDocument

    roles = role_map(TYPE1)
    doc = HwpxDocument.open(str(out))
    # the appended heading paragraphs must carry the template's outline styles
    appended = doc.paragraphs[-4:]
    by_text = {p.text: str(p.style_id_ref) for p in appended}
    assert by_text["새 장"] == roles["HEADING_1"]
    assert by_text["새 절"] == roles["HEADING_2"]

    # heading paragraphs resolve to OUTLINE headings (auto-numbered by Hangul)
    h1 = next(p for p in appended if p.text == "새 장")
    heading = doc.paragraph_property(h1.para_pr_id_ref).heading
    assert heading.type == "OUTLINE" and heading.level == 0

    # output reopens with no replacement chars (non-BMP guard)
    import zipfile

    with zipfile.ZipFile(out) as zf:
        body = b"".join(
            zf.read(n) for n in zf.namelist() if n.startswith("Contents/section")
        )
    assert b"\xef\xbf\xbd" not in body


@pytest.mark.skipif(not TYPE1.is_file(), reason="type-1 sample not present")
def test_fill_inserts_at_body_marker(tmp_path: Path) -> None:
    from hwpx.document import HwpxDocument

    # build a template with a {{body}} marker as the 4th paragraph of section 0
    tmpl = tmp_path / "tmpl.hwpx"
    doc = HwpxDocument.open(str(TYPE1))
    sec = doc.sections[0]
    marker = sec.add_paragraph("{{body}}", style_id_ref=0, para_pr_id_ref=0)
    el = marker.element
    el.getparent().remove(el)
    sec.paragraphs[3].element.addprevious(el)
    doc.save_to_path(str(tmpl))

    out = tmp_path / "out.hwpx"
    result = fill_from_markdown(tmpl, "# 삽입장\n\n본문.\n", output=out)
    assert result.inserted_at_marker is True

    filled = HwpxDocument.open(str(out))
    texts = [p.text for p in filled.sections[0].paragraphs]
    assert "{{body}}" not in "".join(texts)  # marker consumed
    # authored heading sits at the marker position (index 3), not appended at the end
    assert "삽입장" in texts[3]
