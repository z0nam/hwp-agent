"""Tests for hwp_agent.ops.metadata (document properties / cover fields)."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from hwp_agent.ops import Metadata, read_metadata, update_metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_HWPX = REPO_ROOT / "tests" / "fixtures" / "sample_big_ref.hwpx"

_CONTENT_HPF = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<opf:package xmlns:opf="http://www.idpf.org/2007/opf/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" version="">'
    "<opf:metadata>"
    "<opf:title>원래 제목</opf:title>"
    "<opf:language>ko</opf:language>"
    '<opf:meta name="creator" content="text">홍길동</opf:meta>'
    '<opf:meta name="CreatedDate" content="text">2023-09-10T06:19:08Z</opf:meta>'
    "</opf:metadata>"
    '<opf:manifest><opf:item id="header" href="Contents/header.xml" '
    'media-type="application/xml"/>'
    '<opf:item id="section0" href="Contents/section0.xml" '
    'media-type="application/xml"/></opf:manifest>'
    '<opf:spine><opf:itemref idref="header"/>'
    '<opf:itemref idref="section0"/></opf:spine>'
    "</opf:package>"
)
_CONTAINER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>'
    '<ocf:container xmlns:ocf="urn:oasis:names:tc:opendocument:xmlns:container">'
    "<ocf:rootfiles>"
    '<ocf:rootfile full-path="Contents/content.hpf" '
    'media-type="application/hwpml-package+xml"/>'
    "</ocf:rootfiles></ocf:container>"
)


def _make_hwpx(path: Path) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("mimetype", "application/hwp+zip")
        zf.writestr(
            "version.xml",
            '<?xml version="1.0"?>'
            '<hv:HCFVersion xmlns:hv="http://www.hancom.co.kr/hwpml/2011/version"/>',
        )
        zf.writestr("META-INF/container.xml", _CONTAINER)
        zf.writestr("Contents/content.hpf", _CONTENT_HPF)
        zf.writestr(
            "Contents/header.xml",
            '<?xml version="1.0"?>'
            '<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head"/>',
        )
        # python-hwpx >= 2.11 validates open-safety at save: the package must
        # declare at least one section part, so the fixture carries a stub one.
        zf.writestr(
            "Contents/section0.xml",
            '<?xml version="1.0"?>'
            '<hs:sec xmlns:hs="http://www.hancom.co.kr/hwpml/2011/section"/>',
        )


def test_metadata_as_dict_drops_empty() -> None:
    md = Metadata(title="t", creator="", subject=None)
    assert md.as_dict() == {"title": "t"}


def test_read_metadata(tmp_path: Path) -> None:
    pkg = tmp_path / "doc.hwpx"
    _make_hwpx(pkg)
    md = read_metadata(pkg)
    assert md.title == "원래 제목"
    assert md.language == "ko"
    assert md.creator == "홍길동"
    assert md.created == "2023-09-10T06:19:08Z"
    assert md.keyword is None


def test_update_existing_and_create_new(tmp_path: Path) -> None:
    pkg = tmp_path / "doc.hwpx"
    _make_hwpx(pkg)
    written = update_metadata(
        pkg,
        title="새 제목 『PUA』",  # existing element
        creator="조남운",  # existing meta
        keyword="AI; 공공",  # new meta
    )
    assert set(written) == {"title", "creator", "keyword"}

    md = read_metadata(pkg)
    assert md.title == "새 제목 『PUA』"
    assert md.creator == "조남운"
    assert md.keyword == "AI; 공공"
    assert md.created == "2023-09-10T06:19:08Z"  # untouched

    # output still a structurally valid HWPX package
    from hwpx.opc.package import HwpxPackage

    HwpxPackage.open(str(pkg))


def test_update_to_separate_output_leaves_source(tmp_path: Path) -> None:
    src = tmp_path / "src.hwpx"
    out = tmp_path / "out.hwpx"
    _make_hwpx(src)
    update_metadata(src, output=out, title="복사본 제목")
    assert read_metadata(out).title == "복사본 제목"
    assert read_metadata(src).title == "원래 제목"  # source untouched


def test_update_rejects_unknown_field(tmp_path: Path) -> None:
    pkg = tmp_path / "doc.hwpx"
    _make_hwpx(pkg)
    with pytest.raises(ValueError, match="unknown metadata field"):
        update_metadata(pkg, author="x")  # 'author' isn't a field (it's 'creator')


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_read_real_reference() -> None:
    md = read_metadata(REF_HWPX)
    assert md.title and md.creator  # real document carries these
