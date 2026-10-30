"""Decode a child process's captured output without trusting the locale.

``subprocess.run(text=True)`` decodes with the locale's code page — cp949 on a
Korean Windows — while rhwp (Rust) prints UTF-8, so Korean paths in its output
kill the reader threads (a ``UnicodeDecodeError`` traceback on screen, and
``stdout``/``stderr`` left ``None``, losing the error tail). Callers capture
bytes and decode here instead: UTF-8 first, then the console (OEM) code page that
Windows programs such as Java and the frozen hwp2pdf-cli print in, then the
locale's — never raising. Same order as jaix's ``proc.decode``.
"""

from __future__ import annotations

import locale
import sys


def decode(data: bytes | None) -> str:
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if sys.platform.startswith("win"):
        try:
            import ctypes

            return data.decode(f"cp{ctypes.windll.kernel32.GetOEMCP()}", errors="replace")
        except (AttributeError, LookupError, OSError):
            pass
    return data.decode(locale.getpreferredencoding(False) or "utf-8", errors="replace")
