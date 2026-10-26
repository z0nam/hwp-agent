"""Tests for widow-tail fitting (issue #39)."""

from __future__ import annotations

import json
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from lxml import etree

from hwp_agent.cli.main import main
from hwp_agent.ops.fit_tails import (
    _HP,
    _clone_char_pr,
    apply_tail_fits,
    compute_tail_fit,
    detect_tail_fits,
    detect_tail_fits_with_engine,
    format_tail_fit_report,
)
from hwp_agent.ops.integrity import audit_package
from hwp_agent.render.rhwp import resolve_rhwp

# Committed fixture (scripts/build-tail-fit-sample.py): body paragraphs tagged
# [S] [A] [B] [C] [D] [E] — see that script for what each one exercises.
SAMPLE = Path(__file__).parent / "data" / "tail_fit_sample.hwpx"


def test_compute_tail_fit_spacing_only() -> None:
    # 80 total chars, 2 tail chars -> 2/80 = 2.5% reduction needed
    # Spacing -3% is sufficient (2.5% <= 3%)
    spacing, ratio, fittable = compute_tail_fit(
        total_chars=80,
        tail_chars=2,
        orig_spacing=0,
        orig_ratio=100,
        min_spacing=-5,
        min_ratio=95,
    )
    assert fittable is True
    assert spacing == -3
    assert ratio == 100


def test_compute_tail_fit_spacing_and_ratio() -> None:
    # 40 total chars, 3 tail chars -> 3/40 = 7.5% reduction needed
    # Spacing -5% (5%) + Ratio 98% (2%) = 7% < 7.5% -> needs Ratio 97% (5% + 3% = 8%)
    spacing, ratio, fittable = compute_tail_fit(
        total_chars=40,
        tail_chars=3,
        orig_spacing=0,
        orig_ratio=100,
        min_spacing=-5,
        min_ratio=95,
    )
    assert fittable is True
    assert spacing == -5
    assert ratio == 97


def test_compute_tail_fit_unfittable() -> None:
    # 20 total chars, 4 tail chars -> 4/20 = 20% reduction needed
    # Max allowed reduction is -5% spacing + 5% ratio = 10% < 20% -> unfittable
    spacing, ratio, fittable = compute_tail_fit(
        total_chars=20,
        tail_chars=4,
        orig_spacing=0,
        orig_ratio=100,
        min_spacing=-5,
        min_ratio=95,
    )
    assert fittable is False
    assert spacing == -5
    assert ratio == 95


def test_format_tail_fit_report(fake_hwp2pdf, no_rhwp) -> None:
    report = format_tail_fit_report([])
    assert "대상 문단 없음" in report

    candidates = detect_tail_fits(str(SAMPLE), max_chars=4)
    assert len(candidates) >= 1
    rep = format_tail_fit_report(candidates)
    assert "꼬리 줄 압축" in rep


def test_clone_char_pr_attributes() -> None:
    raw = etree.fromstring(
        b'<hh:charPr xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" id="0">'
        b'<hh:spacing hangul="0" latin="0"/>'
        b'<hh:ratio hangul="100" latin="100"/>'
        b"</hh:charPr>"
    )
    cloned = _clone_char_pr(raw, "99", spacing=-3, ratio=98)
    assert cloned.get("id") == "99"
    sp = cloned.find("{http://www.hancom.co.kr/hwpml/2011/head}spacing")
    rat = cloned.find("{http://www.hancom.co.kr/hwpml/2011/head}ratio")
    assert sp.get("hangul") == "-3"
    assert sp.get("latin") == "-3"
    assert rat.get("hangul") == "98"
    assert rat.get("latin") == "98"


def test_apply_tail_fits_and_integrity(tmp_path: Path) -> None:
    src = Path(str(SAMPLE))
    out = tmp_path / "fitted.hwpx"

    fitted_count = apply_tail_fits(src, out, max_chars=4)
    assert out.is_file()

    # Integrity audit: check itemCnt, ordering, duplicates, dangling refs
    issues = audit_package(out)
    assert issues == []

    # [C] carries a linesegarray in the fixture; once fitted it must be dropped
    if fitted_count > 0:
        with zipfile.ZipFile(str(out)) as z:
            sec0 = etree.fromstring(z.read("Contents/section0.xml"))
        fitted = [c for c in detect_tail_fits(SAMPLE, max_chars=4) if c.fittable]
        all_p = list(sec0.iter(f"{{{_HP}}}p"))
        for c in fitted:
            assert all_p[c.para_index].find(f"{{{_HP}}}linesegarray") is None


def test_cli_fit_tails_dry_run(capsys, fake_hwp2pdf) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--dry-run"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "꼬리 줄 압축" in captured
    assert "--dry-run" in captured


def test_cli_fit_tails_json(capsys, fake_hwp2pdf) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--json", "--dry-run"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert "candidates" in data
    assert data["total_candidates"] >= 1
    assert "fittable_count" in data


def test_cli_fit_tails_output(tmp_path: Path, capsys, fake_hwp2pdf) -> None:
    out = tmp_path / "cli_out.hwpx"
    ret = main([
        "fit",
        "tails",
        str(SAMPLE),
        "-o",
        str(out),
        "--max-chars",
        "4",
    ])
    assert ret == 0
    assert out.is_file()
    assert audit_package(out) == []


def test_detect_tail_fits_skips_hard_linebreak(tmp_path: Path) -> None:
    src = Path(str(SAMPLE))
    mod = tmp_path / "hard_break.hwpx"

    with zipfile.ZipFile(str(src)) as z_in:
        names = z_in.namelist()
        sec0_xml = z_in.read("Contents/section0.xml").decode("utf-8")

        injected = (
            '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:run charPrIDRef="0">'
            "<hp:t>이것은 긴 본문 테스트 문단입니다. 첫째 줄이 여기에 있고"
            "<hp:lineBreak/>"
            "꼬리</hp:t>"
            "</hp:run>"
            "<hp:linesegarray>"
            '<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            'baseline="850" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            '<hp:lineseg textpos="33" vertpos="1600" vertsize="1000" textheight="1000" '
            'baseline="2450" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            "</hp:linesegarray>"
            "</hp:p>"
        )
        assert "</hs:sec>" in sec0_xml
        sec0_xml = sec0_xml.replace("</hs:sec>", injected + "</hs:sec>", 1)

        with zipfile.ZipFile(str(mod), "w") as z_out:
            for name in names:
                if name == "Contents/section0.xml":
                    z_out.writestr(name, sec0_xml.encode("utf-8"))
                else:
                    z_out.writestr(name, z_in.read(name))

    candidates = detect_tail_fits(mod, max_chars=4)
    assert not any("첫째 줄이 여기에 있고" in c.text_preview for c in candidates)


def test_detect_tail_fits_includes_bullet_paragraphs(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    # Paragraphs with heading type BULLET are institutional body text (■·-),
    # not outline headings, and MUST be included in tail-fitting.
    src = Path(str(SAMPLE))
    mod = tmp_path / "bullet_test.hwpx"

    with zipfile.ZipFile(str(src)) as z_in:
        names = z_in.namelist()
        header_xml = z_in.read("Contents/header.xml").decode("utf-8")
        sec0_xml = z_in.read("Contents/section0.xml").decode("utf-8")

        bullet_para = (
            '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph" '
            'paraPrIDRef="BULLET_PP" styleIDRef="BULLET_ST">'
            '<hp:run charPrIDRef="0">'
            "<hp:t>■ 이것은 글머리표 본문 테스트 문단입니다. 두 번째 줄로 넘어가서 "
            "꼬리</hp:t></hp:run>"
            "<hp:linesegarray>"
            '<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            'baseline="850" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            '<hp:lineseg textpos="36" vertpos="1600" vertsize="1000" textheight="1000" '
            'baseline="2450" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            "</hp:linesegarray>"
            "</hp:p>"
        )
        sec0_xml = sec0_xml.replace("</hs:sec>", bullet_para + "</hs:sec>", 1)

        injected_pp = (
            '<hh:paraPr xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" id="BULLET_PP">'
            '<hh:heading type="BULLET" idRef="0" level="0"/>'
            '</hh:paraPr>'
        )
        injected_st = (
            '<hh:style xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" id="BULLET_ST" '
            'type="PARA" name="JI_■" paraPrIDRef="BULLET_PP"/>'
        )
        header_xml = header_xml.replace(
            "</hh:paraProperties>", injected_pp + "</hh:paraProperties>", 1
        )
        header_xml = header_xml.replace(
            "</hh:styles>", injected_st + "</hh:styles>", 1
        )

        with zipfile.ZipFile(str(mod), "w") as z_out:
            for name in names:
                if name == "Contents/section0.xml":
                    z_out.writestr(name, sec0_xml.encode("utf-8"))
                elif name == "Contents/header.xml":
                    z_out.writestr(name, header_xml.encode("utf-8"))
                else:
                    z_out.writestr(name, z_in.read(name))

    candidates = detect_tail_fits(mod, max_chars=4)
    assert any("■ 이것은 글머리표 본문" in c.text_preview for c in candidates)


def _rhwp_lines(path: Path, rhwp: str) -> dict[tuple[int, int], int]:
    from hwp_agent.ops.fit_tails import _parse_dump_pages

    out = subprocess.run([rhwp, "dump-pages", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return {k: v.lines for k, v in _parse_dump_pages(out.stdout).items()}


def _control_and_keys(src: Path, fitted, dst: Path) -> list[tuple[int, int]]:
    """Write the control: ``src`` with only the fitted paragraphs' linesegarray removed
    (charPr untouched). Returns the fitted paragraphs' (section, top-level index)."""
    from hwp_agent.ops.container import _rewrite_zip_preserving
    from hwp_agent.ops.fit_tails import _section_number, _strip_linesegs

    strip: dict[str, set[int]] = {}
    for c in fitted:
        strip.setdefault(c.section, set()).add(c.para_index)
    with zipfile.ZipFile(src) as z:
        blobs = {s: z.read(s) for s in strip}
    _rewrite_zip_preserving(src, dst, _strip_linesegs(blobs, strip))

    keys = []
    for c in fitted:
        root = etree.fromstring(blobs[c.section])
        all_p = list(root.iter(f"{{{_HP}}}p"))
        tops = root.findall(f"{{{_HP}}}p")
        keys.append((_section_number(c.section), tops.index(all_p[c.para_index])))
    return keys


def _assert_report_matches_control(src: Path, tmp_path: Path, rhwp: str) -> int:
    """Reported "-N줄" must equal rhwp(fitted) - rhwp(control), paragraph by paragraph."""
    candidates = detect_tail_fits(src, max_chars=4)
    fitted = [c for c in candidates if c.fittable]
    out = tmp_path / "fitted.hwpx"
    assert apply_tail_fits(src, out, candidates=candidates) == len(fitted)
    if not fitted:
        return 0
    keys = _control_and_keys(src, fitted, tmp_path / "control.hwpx")
    control = _rhwp_lines(tmp_path / "control.hwpx", rhwp)
    after = _rhwp_lines(out, rhwp)
    for c, key in zip(fitted, keys, strict=True):
        assert control[key] == c.total_lines, (c, control[key])
        assert after[key] == c.target_lines, (c, after[key])
        assert after[key] < control[key]
    claimed = sum(c.target_lines - c.total_lines for c in fitted)
    assert sum(after.values()) - sum(control.values()) == claimed
    return claimed


def _by_tag(candidates):
    return {c.text_preview[:3]: c for c in candidates}


def test_rhwp_fit_reduces_lines_against_control(tmp_path: Path) -> None:
    """#39 follow-up: the reduction is measured against a control laid out the same
    way (linesegarray removed, charPr unchanged), not against the Hangul cache."""
    rhwp = resolve_rhwp()
    if not rhwp:
        pytest.skip("rhwp CLI not available")

    cands = _by_tag(detect_tail_fits(SAMPLE, max_chars=4))
    assert {"[A]", "[B]", "[C]"} <= {t for t, c in cands.items() if c.fittable}
    assert cands["[A]"].tail_chars == 1 and cands["[B]"].tail_chars == 3
    assert cands["[C]"].tail_chars == 2
    # Stale cache: the old code credited the lineseg removal as a fit (-1줄).
    assert cands["[D]"].fittable is False
    assert cands["[D]"].skip_reason == "layout_mismatch"
    assert "[E]" not in cands and "[S]" not in cands

    assert _assert_report_matches_control(SAMPLE, tmp_path, rhwp) == -3


def test_rhwp_no_room_means_no_fit(tmp_path: Path) -> None:
    rhwp = resolve_rhwp()
    if not rhwp:
        pytest.skip("rhwp CLI not available")
    cands = detect_tail_fits(SAMPLE, max_chars=4, min_spacing=0, min_ratio=100)
    assert cands and not any(c.fittable for c in cands)
    assert "한도 초과" in format_tail_fit_report(cands, min_spacing=0, min_ratio=100)


def test_parse_dump_pages_does_not_sum_partial_pieces() -> None:
    from hwp_agent.ops.fit_tails import _parse_dump_pages

    dump = (
        "=== 페이지 12 (global_idx=11, section=3, page_num=3) ===\n"
        "    PartialParagraph  pi=62  lines=0..2  vpos=1  ls=3[first ts=0 lh=1 th=1 "
        "bl=1 gap=1 cs=0 sw=1; last ts=90 lh=1 th=1 bl=1 gap=1 cs=0 sw=1]  \"x\"\n"
        "=== 페이지 13 (global_idx=12, section=3, page_num=4) ===\n"
        "    PartialParagraph  pi=62  lines=2..3  vpos=2  ls=3[first ts=0 lh=1 th=1 "
        "bl=1 gap=1 cs=0 sw=1; last ts=90 lh=1 th=1 bl=1 gap=1 cs=0 sw=1]  \"x\"\n"
        "    FullParagraph  pi=63  h=1 vpos=3  ls=1[ts=0 lh=1 th=1 bl=1 gap=1 cs=0 sw=1]\n"
    )
    out = _parse_dump_pages(dump)
    assert out[(3, 62)].lines == 3  # each piece reports the whole paragraph
    assert out[(3, 62)].last_ts == 90 and out[(3, 62)].page_num == 13
    assert out[(3, 63)].lines == 1 and out[(3, 63)].last_ts == 0


def test_sample_big_converted_report_matches_control(tmp_path: Path) -> None:
    # Auxiliary: a real report kept out of git (tests/tmp is ignored), so this only
    # runs where that file exists. The committed SAMPLE covers the same check.
    sample_big = Path(__file__).parent / "tmp" / "sample_big_converted.hwpx"
    if not sample_big.is_file():
        pytest.skip("tests/tmp/sample_big_converted.hwpx not available")
    rhwp = resolve_rhwp()
    if not rhwp:
        pytest.skip("rhwp CLI not available")
    _assert_report_matches_control(sample_big, tmp_path, rhwp)
    assert audit_package(tmp_path / "fitted.hwpx") == audit_package(sample_big)


def test_section_sorting_and_numbering() -> None:
    from hwp_agent.ops.fit_tails import _section_number, _section_parts

    names = [
        "Contents/section10.xml",
        "Contents/section2.xml",
        "Contents/section1.xml",
        "Contents/header.xml",
    ]
    sorted_sections = _section_parts(names)
    assert sorted_sections == [
        "Contents/section1.xml",
        "Contents/section2.xml",
        "Contents/section10.xml",
    ]
    assert _section_number("Contents/section10.xml") == 10
    assert _section_number("Contents/section2.xml") == 2


def test_char_pr_cloner_per_run_isolation() -> None:
    from hwp_agent.ops.fit_tails import _CharPrCloner

    raw = etree.fromstring(
        b'<hh:charProperties xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head" itemCnt="2">'
        b'<hh:charPr id="0"><hh:spacing hangul="0"/><hh:ratio hangul="100"/></hh:charPr>'
        b'<hh:charPr id="1"><hh:spacing hangul="2"/><hh:ratio hangul="105"/></hh:charPr>'
        b"</hh:charProperties>"
    )
    cloner = _CharPrCloner(raw)

    id_0_cloned = cloner.get_or_create("0", spacing=-3, ratio=98)
    id_1_cloned = cloner.get_or_create("1", spacing=-3, ratio=98)

    assert id_0_cloned != id_1_cloned
    assert id_0_cloned == "2"
    assert id_1_cloned == "3"

    # Same request returns cached id
    assert cloner.get_or_create("0", spacing=-3, ratio=98) == "2"

    cloner.finalize()
    assert raw.get("itemCnt") == "4"


def test_detect_tail_fits_footnote_handling(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    src = Path(str(SAMPLE))
    mod = tmp_path / "footnote_test.hwpx"

    with zipfile.ZipFile(str(src)) as z_in:
        names = z_in.namelist()
        sec0_xml = z_in.read("Contents/section0.xml").decode("utf-8")

        footnote_para = (
            '<hp:p xmlns:hp="http://www.hancom.co.kr/hwpml/2011/paragraph">'
            '<hp:run charPrIDRef="0">'
            '<hp:ctrl><hp:footNote number="1">'
            '<hp:subList><hp:p>'
            '<hp:run charPrIDRef="0">'
            '<hp:t>각주 본문 내용 테스트 문단입니다. 두 번째 줄 꼬리</hp:t>'
            '</hp:run>'
            '<hp:linesegarray>'
            '<hp:lineseg textpos="0" vertpos="0" vertsize="1000" textheight="1000" '
            'baseline="850" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            '<hp:lineseg textpos="25" vertpos="1600" vertsize="1000" textheight="1000" '
            'baseline="2450" spacing="600" horzpos="0" horzsize="40000" flags="0"/>'
            '</hp:linesegarray>'
            '</hp:p></hp:subList>'
            '</hp:footNote></hp:ctrl>'
            '</hp:run>'
            '</hp:p>'
        )
        sec0_xml = sec0_xml.replace("</hs:sec>", footnote_para + "</hs:sec>", 1)

        with zipfile.ZipFile(str(mod), "w") as z_out:
            for name in names:
                if name == "Contents/section0.xml":
                    z_out.writestr(name, sec0_xml.encode("utf-8"))
                else:
                    z_out.writestr(name, z_in.read(name))

    # Without include_footnotes -> 0 footnote candidates
    cands_no_fn = detect_tail_fits(mod, max_chars=4, include_footnotes=False)
    assert not any("각주 본문 내용" in c.text_preview for c in cands_no_fn)

    # With include_footnotes=True -> candidate detected
    cands_with_fn = detect_tail_fits(mod, max_chars=4, include_footnotes=True)
    assert any("각주 본문 내용" in c.text_preview for c in cands_with_fn)


def test_generate_fit_steps_preserves_already_tighter_properties() -> None:
    from hwp_agent.ops.fit_tails import _generate_fit_steps

    # If orig_spacing is already -8 (below min_spacing=-5), it should never expand to -5
    steps = _generate_fit_steps(
        orig_spacing=-8, orig_ratio=100, min_spacing=-5, min_ratio=95
    )
    # Since orig_spacing <= min_spacing, no spacing reduction steps needed
    # Ratio reduction steps must hold base spacing at -8 (not -5)
    for sp, rat in steps:
        assert sp <= -8
        assert rat <= 100

    # compute_tail_fit with already tighter spacing
    target_sp, target_rat, fittable = compute_tail_fit(
        total_chars=100,
        tail_chars=2,
        orig_spacing=-8,
        orig_ratio=100,
        min_spacing=-5,
        min_ratio=95,
    )
    assert fittable is True
    assert target_sp == -8
    assert target_rat == 98


def test_detect_tail_fits_rhwp_failure_is_no_engine(tmp_path: Path) -> None:
    # #61: paragraphs without a saved layout + rhwp failing (and no hwp2pdf) is
    # "could not judge", not a heuristic guess over the paragraphs that happen
    # to carry a linesegarray.
    failing = tmp_path / "rhwp"
    failing.write_text("#!/bin/sh\nexit 1\n")
    failing.chmod(0o755)
    det = detect_tail_fits_with_engine(SAMPLE, max_chars=4, rhwp_bin=str(failing))
    assert det.engine == "none" and not det.judged and det.candidates == []
    assert any("hwp2pdf 미사용" in n for n in det.notes)
    assert any("rhwp 실행 실패" in n for n in det.notes)


# --------------------------------------------------------------------------- #
# #61 engine selection: saved linesegarray > hwp2pdf > rhwp > none
# --------------------------------------------------------------------------- #
def _drop_linesegs(src: Path, dst: Path) -> None:
    """A write-like output: no paragraph carries a Hangul layout cache."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            blob = zin.read(info.filename)
            if info.filename.startswith("Contents/section"):
                root = etree.fromstring(blob)
                for lsa in list(root.iter(f"{{{_HP}}}linesegarray")):
                    lsa.getparent().remove(lsa)
                blob = etree.tostring(root, xml_declaration=True, encoding="UTF-8")
            zout.writestr(info, blob)


def _has_lineseg(path: Path, para_index: int) -> bool:
    with zipfile.ZipFile(path) as z:
        root = etree.fromstring(z.read("Contents/section0.xml"))
    p = list(root.iter(f"{{{_HP}}}p"))[para_index]
    return p.find(f"{{{_HP}}}linesegarray") is not None


def test_hwp2pdf_resave_supplies_hangul_layout(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    bare = tmp_path / "bare.hwpx"
    _drop_linesegs(SAMPLE, bare)
    det = detect_tail_fits_with_engine(bare, max_chars=4)
    assert det.engine == "hwp2pdf" and det.judged
    assert "hwp2pdf" in det.engine_label
    # fake re-save: every paragraph gets 3 lines with a 2-char tail
    assert {c.text_preview[:3] for c in det.candidates} >= {"[A]", "[B]", "[E]"}
    assert all(c.tail_chars == 2 and c.total_lines == 3 for c in det.candidates)
    assert [p["output_format"] for p in fake_hwp2pdf.run_payloads] == ["HWPX"]


def test_hwp2pdf_resave_never_leaks_into_result(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    bare = tmp_path / "bare.hwpx"
    _drop_linesegs(SAMPLE, bare)
    out = tmp_path / "out.hwpx"
    det = detect_tail_fits_with_engine(bare, max_chars=4)
    n = apply_tail_fits(bare, out, candidates=det.candidates)
    assert n >= 1 and audit_package(out) == []
    # the re-saved layout was only measured: no paragraph gained a linesegarray
    with zipfile.ZipFile(out) as z:
        assert b"linesegarray" not in z.read("Contents/section0.xml")


def test_saved_layout_skips_hwp2pdf(tmp_path: Path, fake_hwp2pdf) -> None:
    # every eligible paragraph already carries a Hangul layout: no round-trip
    from conftest import resave_with_linesegs

    full = tmp_path / "full.hwpx"
    full.write_bytes(resave_with_linesegs(SAMPLE.read_bytes()))
    det = detect_tail_fits_with_engine(full, max_chars=4, rhwp_bin=str(tmp_path / "none"))
    assert det.engine == "document"
    assert fake_hwp2pdf.run_payloads == []


def test_hwp2pdf_overlap_falls_back_to_rhwp(tmp_path: Path, fake_hwp2pdf) -> None:
    rhwp = resolve_rhwp()
    if not rhwp:
        pytest.skip("rhwp CLI not available")
    fake_hwp2pdf.hwp_running = ["777"]
    det = detect_tail_fits_with_engine(SAMPLE, max_chars=4)
    assert det.engine == "rhwp" and "한글과 다를 수 있음" in det.engine_label
    assert any("한글이 실행 중" in n for n in det.notes)
    assert fake_hwp2pdf.run_payloads == []


def test_hwp2pdf_overlap_and_no_rhwp_is_no_engine(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    fake_hwp2pdf.hwp_running = ["777"]
    det = detect_tail_fits_with_engine(SAMPLE, max_chars=4)
    assert det.engine == "none" and det.candidates == []
    assert any("한글이 실행 중" in n for n in det.notes)
    assert any("rhwp 없음" in n and "RHWP_BIN" in n for n in det.notes)
    assert fake_hwp2pdf.run_payloads == []  # never touched Hangul


def test_hwp2pdf_conversion_failure_falls_through(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    fake_hwp2pdf.fail_item = "RPC 서버를 사용할 수 없음"
    det = detect_tail_fits_with_engine(SAMPLE, max_chars=4)
    assert det.engine == "none"
    assert any("hwp2pdf 판정 실패" in n and "RPC" in n for n in det.notes)
    assert fake_hwp2pdf.deleted == ["job1"]


def test_cli_fit_tails_no_engine_exit_code(capsys, no_rhwp) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--dry-run"])
    cap = capsys.readouterr()
    assert ret == 3
    assert "판정 엔진 없음" in cap.err and "대상 없음이 아님" in cap.err
    assert "hwp2pdf 서버 미설정" in cap.err and "rhwp 없음" in cap.err
    assert "대상 문단 없음" not in cap.out


def test_cli_fit_tails_no_engine_json(capsys, no_rhwp) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert ret == 3 and data["judged"] is False and data["engine"] == "none"
    assert data["notes"]


def test_cli_fit_tails_reports_engine(capsys, fake_hwp2pdf, no_rhwp) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--dry-run"])
    assert ret == 0
    assert "[판정: hwp2pdf 한글 조판" in capsys.readouterr().out


def test_write_fit_tails_without_engine_warns_and_writes(
    tmp_path: Path, capsys, no_rhwp
) -> None:
    md = tmp_path / "content.md"
    md.write_text("# 서론\n\n" + "지역 공공서비스의 품질을 높이기 위한 방안을 검토함. " * 6 + "\n",
                  encoding="utf-8")
    out = tmp_path / "out.hwpx"
    ret = main(["write", str(md), "-o", str(out), "--fit-tails"])
    cap = capsys.readouterr()
    assert ret == 0 and out.is_file()
    assert "--fit-tails 미적용 — 판정 엔진 없음" in cap.err


def test_malformed_hwp2pdf_config_falls_through(tmp_path: Path, monkeypatch, no_rhwp) -> None:
    # review #63: a broken hwp2pdf.json must not crash; it is "hwp2pdf unavailable"
    bad = tmp_path / "hwp2pdf.json"
    bad.write_text("{not json")
    monkeypatch.setenv("HWP2PDF_CONFIG", str(bad))
    det = detect_tail_fits_with_engine(SAMPLE, max_chars=4)
    assert det.engine == "none"
    assert any("설정 오류" in n for n in det.notes)


# --------------------------------------------------------------------------- #
# #65 hwp2pdf re-save laid out only the first pages: rest judged by rhwp
# --------------------------------------------------------------------------- #
def _partial_resave(fake, laid_out: int) -> None:
    from conftest import resave_with_linesegs

    def convert(data: bytes, fmt: str) -> bytes:
        return resave_with_linesegs(data, laid_out=laid_out)

    fake.convert = convert


def _bare(tmp_path: Path) -> Path:
    bare = tmp_path / "bare.hwpx"
    _drop_linesegs(SAMPLE, bare)
    return bare


def test_partial_layout_without_rhwp_reports_partial(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    _partial_resave(fake_hwp2pdf, laid_out=3)  # [S] [A] [B] laid out; [C] [D] [E] not
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4)
    assert det.engine == "hwp2pdf" and det.judged and det.partial
    assert det.counts == {"hwp2pdf": 2, "rhwp": 0, "no_layout": 3, "unmatched": 0}
    assert det.coverage == "한글 2 · 판정 못함 3"
    assert "한글 2 · 판정 못함 3" in det.engine_label
    assert {c.text_preview[:3] for c in det.candidates} == {"[A]", "[B]"}
    assert all(c.engine == "hwp2pdf" for c in det.candidates)
    assert any("줄 배치가 없는 문단 3개는 판정 못함" in n and "rhwp 없음" in n
               for n in det.notes)


def test_partial_layout_empty_result_is_not_no_tails(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    # the laid-out paragraph has no tail: must not read as "대상 없음"
    from conftest import resave_with_linesegs

    fake_hwp2pdf.convert = lambda data, fmt: resave_with_linesegs(data, tail=30, laid_out=2)
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4)
    assert det.candidates == [] and det.partial
    report = format_tail_fit_report(
        det.candidates, engine_label=det.engine_label, partial=det.partial
    )
    assert "일부만 판정" in report and "대상 없음이 아님" in report
    assert "대상 문단 없음" not in report


def test_partial_layout_rest_judged_by_rhwp(tmp_path: Path, fake_hwp2pdf) -> None:
    rhwp = resolve_rhwp()
    if not rhwp:
        pytest.skip("rhwp CLI not available")
    bare = _bare(tmp_path)
    _partial_resave(fake_hwp2pdf, laid_out=3)
    det = detect_tail_fits_with_engine(bare, max_chars=4)
    assert det.engine == "hwp2pdf" and not det.partial
    assert det.counts == {"hwp2pdf": 2, "rhwp": 3, "no_layout": 0, "unmatched": 0}
    assert det.coverage == "한글 2 · rhwp 3"
    assert any("문단 3개는 rhwp로 판정" in n for n in det.notes)
    by_engine = {c.text_preview[:3]: c.engine for c in det.candidates}
    assert by_engine["[A]"] == by_engine["[B]"] == "hwp2pdf"
    rhwp_judged = [c for c in det.candidates if c.engine == "rhwp"]
    assert rhwp_judged
    # rhwp-judged paragraphs get exactly what the rhwp engine alone measures
    fake_hwp2pdf.hwp_running = ["777"]
    alone = detect_tail_fits_with_engine(bare, max_chars=4)
    assert alone.engine == "rhwp"

    def key(c):
        return (c.para_index, c.total_lines, c.target_lines, c.target_spacing,
                c.target_ratio, c.fittable, c.skip_reason)

    assert {key(c) for c in rhwp_judged} == {
        key(c) for c in alone.candidates if c.para_index in {3, 4, 5}
    }
    # and they can be applied together
    out = tmp_path / "out.hwpx"
    n = apply_tail_fits(bare, out, candidates=det.candidates)
    assert n == sum(c.fittable for c in det.candidates) and audit_package(out) == []


def test_full_layout_never_calls_rhwp(tmp_path: Path, fake_hwp2pdf) -> None:
    # regression: a fully laid-out re-save is judged on Hangul alone, as before
    failing = tmp_path / "rhwp"
    failing.write_text("#!/bin/sh\necho called >> \"$0.log\"\nexit 1\n")
    failing.chmod(0o755)
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4, rhwp_bin=str(failing))
    assert det.engine == "hwp2pdf" and not det.partial and det.notes == []
    assert det.counts == {"hwp2pdf": 5, "rhwp": 0, "no_layout": 0, "unmatched": 0}
    assert det.coverage == "한글 5"
    assert not (tmp_path / "rhwp.log").exists()
    assert all(c.tail_chars == 2 and c.total_lines == 3 for c in det.candidates)


def test_text_mismatch_and_missing_layout_counted_apart(
    tmp_path: Path, fake_hwp2pdf, no_rhwp
) -> None:
    from conftest import resave_with_linesegs

    def convert(data: bytes, fmt: str) -> bytes:
        out = zipfile.ZipFile(BytesIO(resave_with_linesegs(data, laid_out=5)))  # [E] unlaid
        buf = BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for info in out.infolist():
                blob = out.read(info.filename)
                if info.filename == "Contents/section0.xml":
                    blob = blob.replace(b"[D]", b"[X]")  # no longer matches
                z.writestr(info, blob)
        return buf.getvalue()

    fake_hwp2pdf.convert = convert
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4)
    assert det.counts == {"hwp2pdf": 3, "rhwp": 0, "no_layout": 1, "unmatched": 1}
    assert det.coverage == "한글 3 · 판정 못함 1 · 맞출 수 없음 1"
    assert any("텍스트가 맞지 않는 문단 1개" in n for n in det.notes)
    assert any("줄 배치가 없는 문단 1개는 판정 못함" in n for n in det.notes)


def test_no_layout_at_all_falls_through(tmp_path: Path, fake_hwp2pdf, no_rhwp) -> None:
    _partial_resave(fake_hwp2pdf, laid_out=0)
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4)
    assert det.engine == "none"
    assert any("hwp2pdf 판정 실패" in n and "linesegarray" in n for n in det.notes)


def test_cli_fit_tails_partial_json_and_text(
    tmp_path: Path, capsys, fake_hwp2pdf, no_rhwp
) -> None:
    _partial_resave(fake_hwp2pdf, laid_out=3)
    bare = _bare(tmp_path)
    assert main(["fit", "tails", str(bare), "--json", "--dry-run"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["engine"] == "hwp2pdf" and data["judged"] is True and data["partial"] is True
    assert data["paragraphs"] == {"hwp2pdf": 2, "rhwp": 0, "no_layout": 3, "unmatched": 0}
    assert {c["engine"] for c in data["candidates"]} == {"hwp2pdf"}
    assert main(["fit", "tails", str(bare), "--dry-run"]) == 0
    cap = capsys.readouterr()
    assert "일부만 판정" in cap.out and "한글 2 · 판정 못함 3" in cap.out
    assert "판정 못함" in cap.err


def test_write_fit_tails_partial_layout(tmp_path: Path, capsys, fake_hwp2pdf, no_rhwp) -> None:
    _partial_resave(fake_hwp2pdf, laid_out=2)
    para = "지역 공공서비스의 품질을 높이기 위한 방안을 검토함. " * 6
    md = tmp_path / "content.md"
    md.write_text(f"# 서론\n\n{para}\n\n{para}\n\n{para}\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"
    ret = main(["write", str(md), "-o", str(out), "--fit-tails"])
    cap = capsys.readouterr()
    assert ret == 0 and out.is_file()
    assert "일부만 판정" in cap.out and "판정 못함" in cap.out
    assert "줄 배치가 없는 문단" in cap.err


def test_partial_layout_unlaunchable_rhwp_keeps_hangul_results(
    tmp_path: Path, fake_hwp2pdf
) -> None:
    # review #67: an rhwp that cannot even start must not lose the Hangul-judged part
    noexec = tmp_path / "rhwp"
    noexec.write_text("#!/bin/sh\nexit 0\n")
    noexec.chmod(0o644)
    _partial_resave(fake_hwp2pdf, laid_out=3)
    det = detect_tail_fits_with_engine(_bare(tmp_path), max_chars=4, rhwp_bin=str(noexec))
    assert det.engine == "hwp2pdf" and det.partial
    assert det.counts == {"hwp2pdf": 2, "rhwp": 0, "no_layout": 3, "unmatched": 0}
    assert {c.text_preview[:3] for c in det.candidates} == {"[A]", "[B]"}
    assert any("판정 못함 — rhwp 실행 실패" in n for n in det.notes)
