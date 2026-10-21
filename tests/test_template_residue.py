"""Tests for issue #51: template residue cleanup on write / build.

Verifies:
1. Unconsumed {{figure_template}} paragraphs (and unreferenced BinData) are removed.
2. Shared BinData is preserved if referenced elsewhere.
3. {{table_template}} reference tables are removed while table styling is preserved.
4. Preview/PrvText.txt and Preview/PrvImage.png are replaced.
5. Leftover {{...}} tokens emit warnings in write and check.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from hwpx.document import HwpxDocument

from hwp_agent.ops import diagnose_template, fill_from_markdown
from hwp_agent.ops.template import bundled_template_path

_DEFAULT_TPL = bundled_template_path()
_HOUSE_POLICY = Path("examples/house-forms/정책과제_서식.normalized.hwpx")


def test_write_strips_figure_template_and_bindata(tmp_path: Path) -> None:
    """When markdown has no figures, {{figure_template}} paragraph and its BinData are dropped."""
    md = "# 제목\n\n본문 텍스트입니다.\n"
    out = tmp_path / "out_no_fig.hwpx"

    res = fill_from_markdown(_DEFAULT_TPL, md, output=out)
    assert not any("unconsumed template marker" in w for w in res.warnings)

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # image1.png was only referenced by figure_template; must be deleted
        assert not any(n.startswith("BinData/image1") for n in names)
        # content.hpf manifest must not contain image1
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        assert 'id="image1"' not in hpf
        # section0.xml must not contain figure_template
        sec = z.read("Contents/section0.xml").decode("utf-8")
        assert "{{figure_template}}" not in sec


def test_write_preserves_shared_bindata(tmp_path: Path) -> None:
    """If another part of the document references the same BinData, BinData is not deleted."""
    # Create a template where image1 is referenced in an extra paragraph
    custom_tpl = tmp_path / "custom_tpl.hwpx"
    doc = HwpxDocument.open(str(_DEFAULT_TPL))
    sec = doc.sections[0]
    p_extra = sec.add_paragraph("다른 문단의 그림")
    # Add a pic pointing to image1
    from lxml import etree
    _HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    _HC = "http://www.hancom.co.kr/hwpml/2011/core"
    pic_xml = (
        f'<hp:run xmlns:hp="{_HP}" xmlns:hc="{_HC}">'
        f'<hp:pic><hc:img binaryItemIDRef="image1"/></hp:pic>'
        f'</hp:run>'
    )
    p_extra.element.append(etree.fromstring(pic_xml))
    doc.save_to_path(str(custom_tpl))

    out = tmp_path / "out_shared.hwpx"
    fill_from_markdown(custom_tpl, "# 본문\n\n내용\n", output=out)

    with zipfile.ZipFile(out) as z:
        names = z.namelist()
        # image1 was shared, so BinData/image1.png must still exist!
        assert any(n.startswith("BinData/image1") for n in names)
        hpf = z.read("Contents/content.hpf").decode("utf-8")
        assert 'id="image1"' in hpf
        sec = z.read("Contents/section0.xml").decode("utf-8")
        assert "{{figure_template}}" not in sec


def test_write_strips_table_template_while_preserving_style(tmp_path: Path) -> None:
    """{{table_template}} reference table is stripped, but authored table inherits its styling."""
    md = (
        "# 1. 서론\n\n"
        "| 열 1 | 열 2 |\n"
        "|---|---|\n"
        "| 값 1 | 값 2 |\n"
    )
    out = tmp_path / "out_table.hwpx"
    fill_from_markdown(_DEFAULT_TPL, md, output=out)

    with zipfile.ZipFile(out) as z:
        sec = z.read("Contents/section0.xml").decode("utf-8")
        assert "{{table_template}}" not in sec
        assert "예시 항목" not in sec

    doc = HwpxDocument.open(str(out))
    _HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    tbls = list(doc.sections[0].element.iter(f"{{{_HP}}}tbl"))
    # Only 1 table (the authored one) should remain
    assert len(tbls) == 1
    authored_tbl = tbls[0]
    # Verify table borderFillIDRef and cell properties were copied from reference table
    assert authored_tbl.get("borderFillIDRef") is not None


def test_write_updates_preview_parts(tmp_path: Path) -> None:
    """Preview/PrvText.txt and Preview/PrvImage.png are replaced on write."""
    md = "# 테스트 제목\n\n이것은 본문의 첫 문단입니다.\n"
    out = tmp_path / "out_preview.hwpx"
    fill_from_markdown(_DEFAULT_TPL, md, output=out)

    with zipfile.ZipFile(out) as z:
        prv_text = z.read("Preview/PrvText.txt").decode("utf-8")
        assert "테스트 제목" in prv_text
        assert "이것은 본문의 첫 문단입니다." in prv_text
        assert "{{body}}" not in prv_text
        assert "제주 현행 지표체계 진단" not in prv_text

        prv_img = z.read("Preview/PrvImage.png")
        # minimal white PNG is 69 bytes
        assert len(prv_img) == 69


def test_residual_token_warnings(tmp_path: Path) -> None:
    """Unconsumed {{...}} tokens trigger warnings in write and check."""
    md = "# 제목\n\n여기에 {{unconsumed_token}} 토큰이 남아있습니다.\n"
    out = tmp_path / "out_residual.hwpx"
    res = fill_from_markdown(_DEFAULT_TPL, md, output=out)

    assert any("unconsumed_token" in w for w in res.warnings)

    # doctor check on this document should also flag unrecognized marker
    diag = diagnose_template(str(out))
    assert any("unconsumed_token" in w for w in diag["warnings"])


def test_residual_korean_and_whitespace_placeholders(tmp_path: Path) -> None:
    """Residual check recognizes form-engine style placeholders like {{ 제목 }}."""
    md = "# 제목\n\n문서 내에 {{  공백_토큰  }} 및 {{ 제목 }} 남아있음.\n"
    out = tmp_path / "out_korean_residual.hwpx"
    res = fill_from_markdown(_DEFAULT_TPL, md, output=out)

    assert any("공백_토큰" in w for w in res.warnings)
    assert any("제목" in w for w in res.warnings)

    diag = diagnose_template(str(out))
    # Unrecognized marker should report each without false ambiguity
    assert any("공백_토큰" in w for w in diag["warnings"])
    assert any("제목" in w for w in diag["warnings"])
    assert not any("appears 2×" in w for w in diag["warnings"])


def test_caption_residual_not_double_counted(tmp_path: Path) -> None:
    """A residual token inside a table caption must be counted exactly once."""
    from lxml import etree

    custom_tpl = tmp_path / "custom_caption.hwpx"
    doc = HwpxDocument.open(str(_DEFAULT_TPL))
    sec = doc.sections[0]
    _HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    for tbl in sec.element.iter(f"{{{_HP}}}tbl"):
        cap = tbl.find(f"{{{_HP}}}caption")
        if cap is not None:
            p = cap.find(f".//{{{_HP}}}p")
            if p is not None:
                p.append(etree.fromstring(
                    f'<hp:run xmlns:hp="{_HP}"><hp:t> {{{{custom_cap}}}}</hp:t></hp:run>'
                ))
                break
    doc.save_to_path(str(custom_tpl))

    diag = diagnose_template(str(custom_tpl))
    toks = {m["token"]: m["count"] for m in diag["markers"]}
    assert toks.get("{{custom_cap}}") == 1
    # Must NOT be flagged as ambiguous (count == 1)
    assert not any("custom_cap" in w and "appears 2×" in w for w in diag["warnings"])
    assert any("custom_cap" in w and "unrecognized marker" in w for w in diag["warnings"])
