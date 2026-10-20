"""Tests for widow-tail fitting (issue #39)."""

from __future__ import annotations

import json
import subprocess
import zipfile
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


def test_format_tail_fit_report() -> None:
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


def test_cli_fit_tails_dry_run(capsys) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--dry-run"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "꼬리 줄 압축" in captured
    assert "--dry-run" in captured


def test_cli_fit_tails_json(capsys) -> None:
    ret = main(["fit", "tails", str(SAMPLE), "--json", "--dry-run"])
    assert ret == 0
    data = json.loads(capsys.readouterr().out)
    assert "candidates" in data
    assert data["total_candidates"] >= 1
    assert "fittable_count" in data


def test_cli_fit_tails_output(tmp_path: Path, capsys) -> None:
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


def test_detect_tail_fits_includes_bullet_paragraphs(tmp_path: Path) -> None:
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


def test_detect_tail_fits_footnote_handling(tmp_path: Path) -> None:
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


def test_detect_tail_fits_rhwp_failure_falls_back_to_heuristic(tmp_path: Path) -> None:
    # When rhwp returns non-zero / fails, detect_tail_fits falls back to heuristic ratio
    # rather than failing or returning 0 fittable candidates.
    candidates = detect_tail_fits(
        str(SAMPLE),
        max_chars=4,
        rhwp_bin="/bin/false",
    )
    assert len(candidates) >= 1
    # Fallback heuristic calculates candidates normally
    assert any(c.fittable for c in candidates)
