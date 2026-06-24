"""Tests for the default-template resolver and the bundled asset."""

from __future__ import annotations

from pathlib import Path

import pytest

from hwp_agent.ops.styles import role_map
from hwp_agent.ops.template import (
    USER_DEFAULT,
    bundled_template_path,
    describe_template_source,
    resolve_template_path,
)

_ENV = "HWP_AGENT_TEMPLATE"


def test_bundled_template_exists_and_is_hwpx() -> None:
    p = bundled_template_path()
    assert p.is_file()
    assert p.suffix == ".hwpx"
    assert p.read_bytes()[:2] == b"PK"  # zip/OPC package


def test_bundled_template_has_author_roles() -> None:
    """The shipped default declares the ladders + AI:INSTRUCTION author needs."""
    roles = role_map(bundled_template_path())
    assert roles.get("HEADING_1") and roles.get("HEADING_2")  # heading ladder
    assert roles.get("BULLET_1") and roles.get("BULLET_2")  # bullet ladder (dash = B2)
    assert "INSTRUCTION" in roles
    assert roles.get("BODY")


def test_resolve_explicit_wins(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv(_ENV, str(tmp_path / "env.hwpx"))
    explicit = tmp_path / "explicit.hwpx"
    assert resolve_template_path(explicit) == explicit
    assert describe_template_source(explicit) == "explicit"


def test_resolve_env_over_default(monkeypatch, tmp_path) -> None:
    env_path = tmp_path / "env.hwpx"
    monkeypatch.setenv(_ENV, str(env_path))
    assert resolve_template_path(None) == env_path
    assert describe_template_source(None) == f"${_ENV}"


def test_resolve_user_default_when_present(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    user = tmp_path / "template.hwpx"
    user.write_bytes(b"PK\x03\x04")
    monkeypatch.setattr("hwp_agent.ops.template.USER_DEFAULT", user)
    assert resolve_template_path(None) == user
    assert describe_template_source(None) == str(user)


def test_resolve_falls_back_to_bundled(monkeypatch) -> None:
    monkeypatch.delenv(_ENV, raising=False)
    # a user-default path that does not exist → bundled
    monkeypatch.setattr(
        "hwp_agent.ops.template.USER_DEFAULT", Path("/no/such/template.hwpx")
    )
    assert resolve_template_path(None) == bundled_template_path()
    assert describe_template_source(None) == "bundled default"


def test_write_cli_uses_bundled_default(monkeypatch, tmp_path, capsys) -> None:
    """`write` with no --template authors onto the bundled default, end to end."""
    from hwp_agent.cli.main import main

    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr(
        "hwp_agent.ops.template.USER_DEFAULT", Path("/no/such/template.hwpx")
    )
    md = tmp_path / "content.md"
    md.write_text("# 서론\n\n본문 문단.\n\n- 항목\n", encoding="utf-8")
    out = tmp_path / "out.hwpx"
    rc = main(["write", str(md), "-o", str(out)])
    assert rc == 0
    assert out.is_file()
    captured = capsys.readouterr().out
    assert "bundled default" in captured
    # instruction guidance paragraphs are stripped on write
    body = b"".join(
        __import__("zipfile").ZipFile(out).read(n)
        for n in __import__("zipfile").ZipFile(out).namelist()
        if n.endswith(".xml")
    )
    assert b"\xef\xbf\xbd" not in body  # no U+FFFD mojibake
    assert "AI 작성 지침".encode() not in body  # instructions removed


def test_default_does_not_overwrite_template(monkeypatch, tmp_path) -> None:
    """write with no -o targets the markdown stem, never the shared template."""
    from hwp_agent.cli.main import main

    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setattr(
        "hwp_agent.ops.template.USER_DEFAULT", Path("/no/such/template.hwpx")
    )
    before = bundled_template_path().read_bytes()
    md = tmp_path / "report.md"
    md.write_text("# 장\n\n본문.\n", encoding="utf-8")
    rc = main(["write", str(md)])
    assert rc == 0
    assert (tmp_path / "report.hwpx").is_file()  # output beside the markdown
    assert bundled_template_path().read_bytes() == before  # bundled asset untouched


@pytest.mark.skipif(
    not USER_DEFAULT.is_file(), reason="no real user default installed"
)
def test_real_user_default_smoke() -> None:  # pragma: no cover - env dependent
    assert resolve_template_path(None) == USER_DEFAULT
