"""PyInstaller entry point for the Windows single-file `hwp-agent.exe`.

A thin wrapper so PyInstaller has a concrete script to freeze; it just delegates
to the normal CLI ``main``.
"""

import sys

from hwp_agent.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
