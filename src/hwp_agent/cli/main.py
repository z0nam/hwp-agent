"""Command-line entry point: ``hwp-agent``."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import __version__
from ..convert import Hwp2HwpxBackend


def _cmd_convert(args: argparse.Namespace) -> int:
    backend = Hwp2HwpxBackend(jar_path=args.jar)
    if not backend.is_available():
        print(
            f"error: backend '{backend.name}' is not available "
            f"(jar={backend.jar_path}, java={backend.java_bin}).\n"
            f"       Build it first:  ./scripts/bootstrap.sh",
            file=sys.stderr,
        )
        return 2

    result = backend.convert(args.input, args.output)
    if not result.ok:
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        print(f"error: conversion failed (exit {result.returncode})", file=sys.stderr)
        return 1

    print(f"wrote {result.hwpx_path}")
    if result.normalized:
        print(f"  normalized: {', '.join(result.normalized)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hwp-agent",
        description="Edit HWP/HWPX documents directly, without a lossy DOCX round-trip.",
    )
    parser.add_argument(
        "--version", action="version", version=f"hwp-agent {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="<command>")

    conv = sub.add_parser("convert", help="convert an HWP file to HWPX")
    conv.add_argument("input", type=Path, help="source .hwp file (never modified)")
    conv.add_argument("output", type=Path, help="destination .hwpx file")
    conv.add_argument(
        "--jar",
        type=Path,
        default=None,
        help="path to hwp2hwpx.jar (default: vendor/hwp2hwpx.jar or $HWP2HWPX_JAR)",
    )
    conv.set_defaults(func=_cmd_convert)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
