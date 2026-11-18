"""#91 2단계 — 발간본 구조의 정책이슈브리프 템플릿과 장별 채우기 (가상 원고)."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import build_report
from hwp_agent.ops.brief import brief_parts, is_brief_template, split_chapters

ROOT = Path(__file__).resolve().parents[1]
TPL = ROOT / "examples" / "정책이슈브리프-template.hwpx"
OLD = ROOT / "examples" / "house-forms" / "정책이슈브리프.normalized.hwpx"

pytestmark = pytest.mark.skipif(not TPL.is_file(), reason="brief template absent")

#: institute boilerplate the template legitimately carries (not manuscript text)
BOILERPLATE = {
    "VOL.", "Ⅰ. ", "Ⅱ. ", "Ⅲ. ", "Ⅳ. ", "참고문헌", "발행처:", " 제주연구원",
    "발행인:", "제주연구원 정책이슈브리프   vol.{{volume}}",
    "제주연구원은 지역사회가 안고 있는 ", "현안 문제에 대해 해결 방안을 모색하고자 ",
    "다양한 정책 방안을 제시하고 있습니다. ", "본 연구를 토대로 보다 합리적이고 ",
    "발전적 대안들이 도출되어 ", "도민의 삶의 질적 향상과 제주발전에 ",
    "디딤돌이 되기를 바랍니다.",
}

FRONT = """---
제목: 가상 정책 이슈 제목입니다 // 두 번째 줄 부제
호수: "123"
저자: 홍길동 제주연구원 연구위원 ; 김철수 제주연구원 부연구위원
머리제목: 가상 머리 제목
요지: 가상 요지 문장
발행인: 가 상 인
---
"""
BODY = (
    FRONT
    + "# Ⅰ. 들어가며\n\n| 가상 요약 문장입니다 |\n|---|\n\n- 첫째 장 불릿\n\n"
    + "# Ⅱ. 가상 현황\n\n- 둘째 장 불릿\n\n"
    + "# Ⅲ. 가상 분석\n\n- 셋째 장 불릿\n\n"
    + "# Ⅳ. 가상 시사점\n\n- 넷째 장 불릿\n\n"
    + "# 부록. 가상 부록\n\n- 부록 불릿\n"
)
REFS = "**보고서**\n\n가상 기관, 가상 보고서.\n"


def _texts(z: zipfile.ZipFile, name: str) -> str:
    return re.sub(r"<[^>]+>", "|", z.read(name).decode())


def test_new_template_is_a_brief_template_and_the_old_one_is_not() -> None:
    assert is_brief_template(TPL)
    if OLD.is_file():
        assert not is_brief_template(OLD)


def test_split_chapters_and_slots() -> None:
    chapters = split_chapters("앞말\n# Ⅰ. 하나\n가\n# Ⅱ. 둘\n나\n")
    assert [c.title for c in chapters] == ["Ⅰ. 하나", "Ⅱ. 둘"]
    assert "앞말" in chapters[0].body and chapters[1].body.strip() == "나"

    bp = brief_parts(BODY.split("---\n", 2)[2])
    assert bp.slots["ch1_title"] == "Ⅰ. 들어가며" and bp.slots["ch2_title"] == "Ⅱ. 가상 현황"
    assert bp.slots["ch3_title"] == "Ⅲ. 가상 분석"
    # chapters 3… share the last section; its wing lists them all, appendix left out
    assert bp.slots["wing_3"] == "Ⅲ. 가상 분석 · Ⅳ. 가상 시사점"
    assert "# Ⅳ. 가상 시사점" in bp.parts["ch3"] and "# 부록. 가상 부록" in bp.parts["ch3"]
    assert "# Ⅲ" not in bp.parts["ch3"]  # the section's first title is the slot, not repeated
    assert [bp.slots[f"toc_{k}"] for k in (1, 2, 3, 4)] == ["들어가며", "가상 현황", "가상 분석", "가상 시사점"]


def test_fewer_chapters_warn_and_blank_the_unused_sections() -> None:
    bp = brief_parts("# Ⅰ. 하나\n\n- 가\n")
    assert bp.slots["ch2_title"] == "" and bp.slots["wing_3"] == ""
    assert any("비워 둠" in w for w in bp.warnings)


def test_template_carries_no_manuscript_traces() -> None:
    with zipfile.ZipFile(TPL) as z:
        bindata = {n: z.getinfo(n).file_size for n in z.namelist() if n.startswith("BinData/")}
        assert set(Path(n).stem for n in bindata) == {"image1", "image2", "image3", "image12", "image13"}
        # the chapter ornament is cropped to its own few pixels (not a page screenshot)
        assert bindata["BinData/image3.png"] < 20_000 and bindata["BinData/image12.png"] < 20_000
        for n in z.namelist():
            if re.search(r"(section|masterpage)\d+\.xml$", n):
                xml = z.read(n).decode()
                for t in re.findall(r"<hp:t>([^<]*)</hp:t>", xml):
                    assert not t.strip() or "{{" in t or t in BOILERPLATE, (n, t)
                for c in re.findall(r"<hp:shapeComment>(.*?)</hp:shapeComment>", xml, re.S):
                    assert not re.search(r"스크린샷|\d{6}|\.hwp", c), c  # no original file names
                if "section" in n:  # no example footnotes or tables; masterpage wings hold their own tbl
                    # (the one sample table is blanked: its only text is the caption slots)
                    assert not re.search(r"<hp:footNote[ >]", xml), n
                    assert xml.count("<hp:tbl") == (1 if n.endswith("section1.xml") else 0), n
        hpf = z.read("Contents/content.hpf").decode()
        assert "Fasoo" not in hpf and "코로나" not in hpf
        for name in ("creator", "subject", "description", "lastsaveby", "keyword"):
            assert re.search(rf'<opf:meta name="{name}" content="text"/>', hpf), name
        assert z.read("Preview/PrvText.txt").decode().strip() == "정책이슈브리프 서식"
        assert len(z.read("Preview/PrvImage.png")) < 1000


def test_build_fills_chapters_wings_cover_and_references(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "r.md").write_text(REFS, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({
            "template": str(TPL), "output": "out.hwpx",
            "parts": {"body": "b.md", "references": "r.md"},
        }),
        encoding="utf-8",
    )
    result = build_report(manifest)
    assert not [w for w in result.warnings if "marker" in w or "비워" in w], result.warnings
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        s = {i: _texts(z, f"Contents/section{i}.xml") for i in range(5)}
        mp = {i: _texts(z, f"Contents/masterpage{i}.xml") for i in range(5)}
        everything = "".join(s.values()) + "".join(mp.values())
    # 표지·차례
    assert "123" in s[0] and "가상 정책 이슈 제목입니다" in s[0] and "두 번째 줄 부제" in s[0]
    assert "홍길동 제주연구원 연구위원" in s[0] and "김철수 제주연구원 부연구위원" in s[0]
    assert "가상 요지 문장" in s[0] and "가상 시사점" in s[0]  # headline + Contents entries
    # 장 구역: chapter 1 → s0, chapter 2 → s1, chapters 3… → s2
    assert "첫째 장 불릿" in s[0] and "둘째 장 불릿" not in s[0]
    assert "Ⅱ. 가상 현황" in s[1] and "둘째 장 불릿" in s[1]
    assert "Ⅲ. 가상 분석" in s[2] and "셋째 장 불릿" in s[2]
    assert "Ⅳ. 가상 시사점" in s[2] and "넷째 장 불릿" in s[2] and "부록 불릿" in s[2]
    assert "가상 기관, 가상 보고서." in s[3] and "참고문헌" in s[3]
    assert "vol." in s[4] and "123" in s[4] and "가 상 인" in s[4]
    # 장 날개 / 머리제목
    assert "가상 머리 제목" in mp[0]
    assert "Ⅰ. 들어가며" in mp[1] and "Ⅱ. 가상 현황" in mp[2]
    assert "Ⅲ. 가상 분석 · Ⅳ. 가상 시사점" in mp[3]
    assert "참고문헌" in mp[4]
    assert "{{" not in everything  # every slot and marker consumed


def test_template_decoration_is_not_numbered() -> None:
    """#101: uncaptioned decoration must stay out of 그림·표 차례."""
    from tests.test_numbering import _uncaptioned_numbered

    assert _uncaptioned_numbered(TPL) == 0


def test_footnote_definitions_follow_their_references_across_the_split(tmp_path: Path) -> None:
    """Review on #112: definitions at the end of the file must reach every chapter part."""
    body = (
        FRONT
        + "# Ⅰ. 하나\n\n- 첫째 장 본문[^a]\n\n# Ⅱ. 둘\n\n- 둘째 장 본문[^b]\n\n"
        + "# Ⅲ. 셋\n\n- 셋째 장 본문\n\n[^a]: 첫째 각주 설명입니다\n[^b]: 둘째 각주 설명입니다\n"
    )
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(TPL), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    result = build_report(manifest)
    assert not [w for w in result.warnings if "footnote" in w], result.warnings
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        s0, s1 = _texts(z, "Contents/section0.xml"), _texts(z, "Contents/section1.xml")
    assert "첫째 각주 설명입니다" in s0 and "[^a]" not in s0
    assert "둘째 각주 설명입니다" in s1 and "[^b]" not in s1


def test_body_without_chapter_headings_is_rejected_clearly(tmp_path: Path) -> None:
    """Review on #112: do not silently drop the manuscript."""
    (tmp_path / "b.md").write_text(FRONT + "장 제목 없는 본문입니다\n", encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(TPL), "output": "out.hwpx", "parts": {"body": "b.md"}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="장 제목"):
        build_report(manifest)


def _png(path: Path) -> Path:
    import struct
    import zlib

    w, h = 80, 40
    raw = b"".join(b"\x00" + b"\x80\x80\xff" * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    return path


def test_chapter_captions_keep_the_chapter_label_and_table_caption(tmp_path: Path) -> None:
    """The chapter heading is a slot, so the part has no `# Ⅱ.`: captions still read
    Ⅱ-1 (not 그림 1), and the table caption is not dropped for lack of a captioned
    reference table in the template."""
    _png(tmp_path / "a.png")
    body = (
        FRONT
        + "# Ⅰ. 들어가며\n\n- 첫째 장 불릿\n\n"
        + "# Ⅱ. 가상 현황\n\n- 둘째 장 불릿\n\n<표 Ⅱ-1> 가상 지표 비교\n\n"
        + "| 구분 | 2024 |\n|---|---|\n| 가상 A | 10 |\n\n주) 가상 주석\n\n"
        + "[그림 Ⅱ-1] 가상 사진\n![가상](a.png)\n"
    )
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    (tmp_path / "r.md").write_text(REFS, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(TPL), "output": "out.hwpx",
                    "parts": {"body": "b.md", "references": "r.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        s1 = _texts(z, "Contents/section1.xml")
    flat = re.sub(r"\|+", "", s1)
    assert "<표 Ⅱ-1> " in flat.replace("&lt;", "<").replace("&gt;", ">") and "가상 지표 비교" in flat
    assert "[그림 Ⅱ-1]" in flat and "그림 1]" not in flat
    assert "가상 주석" in flat


def test_later_chapters_in_the_last_section_start_a_page_with_the_ornament(tmp_path: Path) -> None:
    """Ⅳ·부록 share section2 with Ⅲ; as in the published brief each starts a new page
    (pageBreak) and carries the chapter ornament paragraph."""
    (tmp_path / "b.md").write_text(BODY, encoding="utf-8")
    (tmp_path / "r.md").write_text(REFS, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(TPL), "output": "out.hwpx",
                    "parts": {"body": "b.md", "references": "r.md"}}),
        encoding="utf-8",
    )
    build_report(manifest)
    from lxml import etree

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        root = etree.fromstring(z.read("Contents/section2.xml"))
    paras = root.findall(f"{{{hp}}}p")
    texts = ["".join(p.itertext()) for p in paras]
    for title in ("Ⅳ. 가상 시사점", "부록. 가상 부록"):
        i = next(k for k, t in enumerate(texts) if t.startswith(title))
        assert paras[i].get("pageBreak") == "1", title
        assert paras[i + 1].find(f".//{{{hp}}}pic") is not None, title
    ids = [e.get("id") for e in root.iter(f"{{{hp}}}pic")]
    assert len(ids) == len(set(ids)) == 3  # Ⅲ's ornament + one per later chapter


def test_generated_tables_follow_the_published_table_sample(tmp_path: Path) -> None:
    """#115: the template carries a blanked sample of the published data table; a built
    table copies its borderFill zones, caption (paraPr/charPr) and note row, and the
    sample itself does not survive the fill."""
    from lxml import etree

    hp = "http://www.hancom.co.kr/hwpml/2011/paragraph"
    with zipfile.ZipFile(TPL) as z:
        sroot = etree.fromstring(z.read("Contents/section1.xml"))
    sample = next(sroot.iter(f"{{{hp}}}tbl"))
    assert "".join(t.text or "" for t in sample.iter(f"{{{hp}}}t")).count("{{table_template:sample}}") == 1
    assert not "".join(
        t.text or "" for tr in sample.findall(f"{{{hp}}}tr") for t in tr.iter(f"{{{hp}}}t")
    ).strip()  # cells carry no text
    sample_cap_pr = sample.find(f"{{{hp}}}caption").find(f".//{{{hp}}}p").get("paraPrIDRef")
    sample_bf = [[tc.get("borderFillIDRef") for tc in tr.findall(f"{{{hp}}}tc")]
                 for tr in sample.findall(f"{{{hp}}}tr")]

    body = (
        FRONT + "# Ⅰ. 들어가며\n\n- 첫째 장 불릿\n\n# Ⅱ. 가상 현황\n\n- 둘째 장 불릿\n\n"
        "<표 Ⅱ-1> 가상 지표 비교\n\n| 구분 | 2024 | 2025 |\n|---|---|---|\n| 가상 A | 1 | 2 |\n"
        "| 가상 B | 3 | 4 |\n\n주) 가상 주석\n"
    )
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    (tmp_path / "r.md").write_text(REFS, encoding="utf-8")
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"template": str(TPL), "output": "out.hwpx",
                    "parts": {"body": "b.md", "references": "r.md"}}),
        encoding="utf-8",
    )
    result = build_report(manifest)
    assert not [w for w in result.warnings if "table_template" in w or "table…" in w], result.warnings
    with zipfile.ZipFile(tmp_path / "out.hwpx") as z:
        root = etree.fromstring(z.read("Contents/section1.xml"))
    tbls = list(root.iter(f"{{{hp}}}tbl"))
    assert len(tbls) == 1  # the sample is gone, only the generated table is left
    tbl = tbls[0]
    assert "".join(t.text or "" for t in tbl.find(f"{{{hp}}}caption").iter(f"{{{hp}}}t")).replace(" ", "") == "<표Ⅱ-1>가상지표비교"
    assert tbl.find(f"{{{hp}}}caption").find(f".//{{{hp}}}p").get("paraPrIDRef") == sample_cap_pr
    got = [[tc.get("borderFillIDRef") for tc in tr.findall(f"{{{hp}}}tc")]
           for tr in tbl.findall(f"{{{hp}}}tr")]
    # header row: published first / interior / last-column fills (the sample has 5 columns)
    assert got[0] == [sample_bf[0][0], sample_bf[0][1], sample_bf[0][-1]]
    assert got[-1] == sample_bf[-1]  # and the note row its own
