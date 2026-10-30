"""Decode a child process's captured output without trusting the locale.

``subprocess.run(text=True)`` decodes with the locale's code page — cp949 on a
Korean Windows — while rhwp (Rust) prints UTF-8, so Korean paths in its output
kill the reader threads (a ``UnicodeDecodeError`` traceback on screen, and
``stdout``/``stderr`` left ``None``, losing the error tail). Callers capture
bytes and decode here instead, never raising:

1. UTF-8 (rhwp; Python children honouring ``PYTHONIOENCODING``)
2. the ANSI code page (``locale.getpreferredencoding``) — what Java and the frozen
   hwp2pdf-cli print when their output is redirected
3. the console (OEM) code page on Windows, strictly too
4. ANSI again with replacement characters

Korean Windows has ANSI = OEM = 949, so there this is jaix's ``proc.decode``
order (UTF-8, then cp949). Elsewhere (e.g. ANSI 1252 / OEM 437) ANSI comes first
because the OEM codec decodes any byte and would win wrongly.
"""

from __future__ import annotations

import locale
import sys


def _oem() -> str | None:
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes

        return f"cp{ctypes.windll.kernel32.GetOEMCP()}"
    except (AttributeError, OSError):
        return None


def decode(data: bytes | None) -> str:
    if not data:
        return ""
    ansi = locale.getpreferredencoding(False) or "utf-8"
    for codec in ("utf-8", ansi, _oem()):
        if not codec:
            continue
        try:
            return data.decode(codec)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode(ansi, errors="replace")
