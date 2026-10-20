"""Tests for widow-tail fitting (issue #39)."""

from __future__ import annotations

import json
from pathlib import Path

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

    candidates = detect_tail_fits("tests/fixtures/sample_hwpx.hwpx", max_chars=4)
    assert len(candidates) >= 1
    rep = format_tail_fit_report(candidates)
    assert "꼬리 줄 압축" in rep
    assert "적용 가능" in rep


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
    src = Path("tests/fixtures/sample_hwpx.hwpx")
    out = tmp_path / "fitted.hwpx"

    fitted_count = apply_tail_fits(src, out, max_chars=4)
    assert fitted_count > 0
    assert out.is_file()

    # Integrity audit: check itemCnt, ordering, duplicates, dangling refs
    issues = audit_package(out)
    assert issues == []

    # Verify linesegarray was dropped from modified paragraph
    import zipfile

    with zipfile.ZipFile(str(out)) as z:
        sec5 = etree.fromstring(z.read("Contents/section5.xml"))
        p6 = list(sec5.iter(f"{{{_HP}}}p"))[6]
        assert p6.find(f"{{{_HP}}}linesegarray") is None


def test_cli_fit_tails_dry_run(capsys) -> None:
    ret = main(["fit", "tails", "tests/fixtures/sample_hwpx.hwpx", "--dry-run"])
    assert ret == 0
    captured = capsys.readouterr().out
    assert "꼬리 줄 압축" in captured
    assert "--dry-run" in captured


def test_cli_fit_tails_json(capsys) -> None:
    ret = main(["fit", "tails", "tests/fixtures/sample_hwpx.hwpx", "--json", "--dry-run"])
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
        "tests/fixtures/sample_hwpx.hwpx",
        "-o",
        str(out),
        "--max-chars",
        "4",
    ])
    assert ret == 0
    assert out.is_file()
    captured = capsys.readouterr().out
    assert "적용 완료" in captured
    assert audit_package(out) == []
