"""Tests for hwp_agent.ops.profile (personal-data profile fill)."""

from __future__ import annotations

import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from hwp_agent.ops.profile import (
    Profile,
    fill_from_profile,
    kdate,
    load_profile,
    match_slot,
    normalize_label,
    resolve_profile_path,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_HWPX = REPO_ROOT / "tests" / "fixtures" / "sample_big_ref.hwpx"

_DATA = {
    "name": "조남운",
    "birthdate": "1975. 7. 4.",
    "mobile": "010-0000-0000",
    "work": {"title": "부연구위원", "email": "namun@example.org"},
    "account": {"bank": "하나은행", "number": "391-910072-65607"},
    "extra": {"특이사항": "없음"},
}


def _profile() -> Profile:
    return Profile(data=_DATA)


def test_normalize_label() -> None:
    assert normalize_label("성  명") == "성명"
    assert normalize_label("주민등록번호 :") == "주민등록번호"
    assert normalize_label("e-mail") == "email"
    assert normalize_label("직위(직급)") == "직위직급"


def test_match_slot_aliases() -> None:
    p = _profile()
    assert match_slot("성명", p).field == "name"
    assert match_slot("신청자", p).value == "조남운"
    assert match_slot("휴대폰", p).field == "mobile"
    assert match_slot("핸드폰", p).field == "mobile"
    assert match_slot("e-mail", p).value == "namun@example.org"
    assert match_slot("계좌번호", p).value == "391-910072-65607"
    assert match_slot("금융기관명", p).value == "하나은행"


def test_match_slot_extra_fallback() -> None:
    assert match_slot("특이사항", _profile()).value == "없음"


def test_match_slot_unknown() -> None:
    m = match_slot("존재하지않는라벨", _profile())
    assert m.field is None and m.value is None


def test_profile_get_nested() -> None:
    p = _profile()
    assert p.get("work.email") == "namun@example.org"
    assert p.get("account.bank") == "하나은행"
    assert p.get("nope.nope") is None
    assert p.get(None) is None


def test_kdate() -> None:
    assert kdate(date(2026, 6, 4)) == "2026. 6. 4."


def test_profile_custom_aliases_merge() -> None:
    p = Profile(data={**_DATA, "aliases": {"평가위원성명": "name"}})
    assert match_slot("평가위원 성명", p, aliases=p.aliases).value == "조남운"


def test_resolve_profile_path_order(tmp_path: Path, monkeypatch) -> None:
    explicit = tmp_path / "x.json"
    env = tmp_path / "env.json"
    monkeypatch.setenv("HWP_AGENT_PROFILE", str(env))
    assert resolve_profile_path(explicit) == explicit  # explicit wins
    assert resolve_profile_path(None) == env  # then env var
    monkeypatch.delenv("HWP_AGENT_PROFILE")
    assert resolve_profile_path(None).name == "profile.json"  # then default


def test_load_profile_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_profile(tmp_path / "absent.json")


def test_load_profile_reads_json(tmp_path: Path) -> None:
    p = tmp_path / "me.json"
    p.write_text(json.dumps(_DATA), encoding="utf-8")
    prof = load_profile(p)
    assert prof.get("name") == "조남운"
    assert prof.source == p


@pytest.mark.skipif(not REF_HWPX.is_file(), reason="reference HWPX not present")
def test_fill_from_profile_roundtrip(tmp_path: Path) -> None:
    prof = tmp_path / "me.json"
    prof.write_text(json.dumps(_DATA), encoding="utf-8")
    out = tmp_path / "filled.hwpx"
    result = fill_from_profile(REF_HWPX, prof, output=out, date="today")

    assert out.is_file()
    assert isinstance(result.filled, list)
    assert isinstance(result.blank, list)
    # no mojibake introduced
    with zipfile.ZipFile(out) as z:
        bad = sum(
            z.read(n).count(b"\xef\xbf\xbd")
            for n in z.namelist()
            if n.startswith("Contents/section")
        )
    assert bad == 0
