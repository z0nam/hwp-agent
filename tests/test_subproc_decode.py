"""Child-process output is decoded as bytes, not with the locale (hwp-agent#83).

On a Korean Windows ``text=True`` reads rhwp's UTF-8 output as cp949: the reader
threads die with a traceback on screen and the error tail is lost."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from hwp_agent._subproc import decode
from hwp_agent.render.rhwp import rhwp_render_fn

FAKE_RHWP = r"""
import sys
# a Korean path in UTF-8, as rhwp prints it, then fail
sys.stdout.buffer.write("읽는 중: C:\\Users\\연구원\\서식.hwpx\n".encode("utf-8"))
sys.stderr.buffer.write("오류: 표를 해석할 수 없음 (서식.hwpx)\n".encode("utf-8"))
sys.exit(3)
"""


def _fake_exe(tmp_path: Path, body: str) -> str:
    exe = tmp_path / "bin" / "rhwp"
    exe.parent.mkdir()
    if os.name == "nt":  # no shebangs: a .cmd shim runs the script
        script = exe.with_suffix(".py")
        script.write_text(body, encoding="utf-8")
        exe = exe.with_suffix(".cmd")
        exe.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding="utf-8")
    else:
        exe.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
        exe.chmod(0o755)
    return str(exe)


def test_decode_utf8_then_fallback():
    assert decode(None) == "" and decode(b"") == ""
    assert decode("서식".encode()) == "서식"
    # not UTF-8: falls back without raising (cp949 where that is the code page)
    out = decode("서식".encode("cp949"))
    assert isinstance(out, str) and out


def test_decode_cp949_locale(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")  # skip the Windows OEM branch
    monkeypatch.setattr("locale.getpreferredencoding", lambda *_a: "cp949")
    assert decode("한글 실행 중".encode("cp949")) == "한글 실행 중"


def test_decode_prefers_ansi_over_oem(monkeypatch):
    # OEM 437 decodes any byte; an ANSI-1252 frozen exe's output must not be read as 437
    monkeypatch.setattr("locale.getpreferredencoding", lambda *_a: "cp1252")
    monkeypatch.setattr("hwp_agent._subproc._oem", lambda: "cp437")
    assert decode("café".encode("cp1252")) == "café"


def test_decode_falls_to_oem_then_replaces(monkeypatch):
    monkeypatch.setattr("locale.getpreferredencoding", lambda *_a: "ascii")
    monkeypatch.setattr("hwp_agent._subproc._oem", lambda: "cp949")
    assert decode("한글".encode("cp949")) == "한글"
    monkeypatch.setattr("hwp_agent._subproc._oem", lambda: None)
    assert "\ufffd" in decode(b"\xff\xfe")


@pytest.mark.parametrize("call", ["render", "dump-pages", "java"])
def test_children_are_read_as_bytes(tmp_path, monkeypatch, call):
    """The regression itself: no text/encoding mode reaches subprocess.run (hwp-agent#83).

    Patching the locale does not reach ``text=True`` (TextIOWrapper asks C for the
    locale encoding), so this checks the arguments instead."""
    import subprocess

    seen = {}

    def fake_run(cmd, **kw):  # noqa: ANN001
        seen.update(kw)
        return subprocess.CompletedProcess(cmd, 1, stdout="표".encode(), stderr="오류".encode())

    monkeypatch.setattr(subprocess, "run", fake_run)
    if call == "render":
        with pytest.raises(RuntimeError, match="오류"):
            rhwp_render_fn("rhwp")(tmp_path / "a.hwpx", tmp_path / "a.pdf")
    elif call == "dump-pages":
        from hwp_agent.ops.fit_tails import _rhwp_layout

        assert _rhwp_layout(tmp_path / "a.hwpx", "rhwp") is None
    else:
        from hwp_agent.convert.hwp2hwpx_backend import Hwp2HwpxBackend

        jar = tmp_path / "x.jar"
        jar.write_bytes(b"x")
        src = tmp_path / "a.hwp"
        src.write_bytes(b"x")
        res = Hwp2HwpxBackend(jar_path=jar, normalize=False).convert(src, tmp_path / "a.hwpx")
        assert res.stderr == "오류"
    assert seen.get("capture_output") and not seen.get("text") and "encoding" not in seen


def test_rhwp_failure_keeps_korean_tail(tmp_path):
    render = rhwp_render_fn(_fake_exe(tmp_path, FAKE_RHWP))
    with pytest.raises(RuntimeError) as err:
        render(tmp_path / "서식.hwpx", tmp_path / "out.pdf")
    assert "표를 해석할 수 없음" in str(err.value)
