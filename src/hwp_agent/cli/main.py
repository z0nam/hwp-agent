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


def _cmd_meta(args: argparse.Namespace) -> int:
    from ..ops import read_metadata, update_metadata

    if args.set:
        values: dict[str, str] = {}
        for item in args.set:
            if "=" not in item:
                print(f"error: --set expects KEY=VALUE, got {item!r}", file=sys.stderr)
                return 2
            key, value = item.split("=", 1)
            values[key.strip()] = value
        try:
            written = update_metadata(args.file, output=args.output, **values)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"updated {', '.join(written)} -> {args.output or args.file}")
        return 0

    data = read_metadata(args.file).as_dict()
    if not data:
        print("(no metadata)")
    for key, value in data.items():
        print(f"{key}: {value}")
    return 0


def _cmd_form(args: argparse.Namespace) -> int:
    import json

    from ..ops import analyze_form, fill_form

    if args.action == "analyze":
        spec = analyze_form(args.file)
        if args.json:
            print(json.dumps(spec.as_dict(), ensure_ascii=False, indent=2))
        else:
            if not spec.slots:
                print("(no fillable slots found)")
            for s in spec.slots:
                cur = f"  [{s.current}]" if s.current else ""
                print(f"{s.kind:11} {s.name}  ->  {s.locator}{cur}")
        return 0

    # fill
    mapping: dict[str, str] = {}
    if args.map:
        mapping.update(json.loads(Path(args.map).read_text(encoding="utf-8")))
    for item in args.set or []:
        if "=" not in item:
            print(f"error: --set expects KEY=VALUE, got {item!r}", file=sys.stderr)
            return 2
        key, value = item.split("=", 1)
        mapping[key.strip()] = value
    if not mapping:
        print("error: nothing to fill (use --map FILE or --set KEY=VALUE)", file=sys.stderr)
        return 2

    result = fill_form(args.file, mapping, output=args.output)
    print(f"filled {len(result.filled)} -> {args.output or args.file}")
    if result.filled:
        print(f"  ok: {', '.join(result.filled)}")
    if result.missing:
        print(f"  missing: {', '.join(result.missing)}")
    return 1 if result.missing and not result.filled else 0


def _cmd_classify(args: argparse.Namespace) -> int:
    from ..ops import classify_document

    print(classify_document(args.file))
    return 0


def _cmd_styles(args: argparse.Namespace) -> int:
    import json

    from ..ops import read_style_system, role_map

    roles = role_map(args.file)
    if args.json:
        infos = [i.as_dict() for i in read_style_system(args.file)]
        print(json.dumps({"roles": roles, "styles": infos}, ensure_ascii=False, indent=2))
        return 0
    if not roles:
        print("(no machine roles detected)")
    for role, sid in sorted(roles.items()):
        print(f"{role:<12} -> style {sid}")
    return 0


def _cmd_instructions(args: argparse.Namespace) -> int:
    import json

    from ..ops import read_instructions

    data = read_instructions(args.file)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    instructions = data.get("instructions") or []
    slots = data.get("slots") or []
    if not instructions and not slots:
        print("(no AI:INSTRUCTION directions or {{slots}} found)")
        return 0
    if instructions:
        print("instructions:")
        for line in instructions:
            print(f"  - {line}")
    if slots:
        print("slots:")
        for slot in slots:
            print(f"  - {slot}")
    return 0


def _cmd_author(args: argparse.Namespace) -> int:
    from ..ops import fill_from_markdown

    markdown = Path(args.md).read_text(encoding="utf-8")
    result = fill_from_markdown(
        args.template, markdown, output=args.output, chapter=args.chapter
    )
    print(f"placed {result.placed} block(s) -> {args.output or args.template}")
    if result.instructions_removed:
        print(f"  removed {result.instructions_removed} instruction paragraph(s)")
    if result.unmapped_roles:
        print(f"  unmapped (fell back to BODY): {', '.join(result.unmapped_roles)}")
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

    meta = sub.add_parser("meta", help="show or set HWPX document metadata")
    meta.add_argument("file", type=Path, help=".hwpx file")
    meta.add_argument(
        "--set",
        action="append",
        metavar="KEY=VALUE",
        help="set a field (repeatable); keys: title, language, creator, subject, "
        "description, keyword, date, created, modified, lastsaveby",
    )
    meta.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="write to a different file (default: edit in place)",
    )
    meta.set_defaults(func=_cmd_meta)

    form = sub.add_parser("form", help="discover or fill form slots in an HWPX")
    form_sub = form.add_subparsers(dest="action", metavar="<analyze|fill>")

    fa = form_sub.add_parser("analyze", help="list fillable slots")
    fa.add_argument("file", type=Path, help=".hwpx form")
    fa.add_argument("--json", action="store_true", help="emit slots as JSON (for an AI)")
    fa.set_defaults(func=_cmd_form)

    ff = form_sub.add_parser("fill", help="fill slots by name/path")
    ff.add_argument("file", type=Path, help=".hwpx form")
    ff.add_argument("--map", type=Path, default=None, help="JSON file of {slot: value}")
    ff.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help="fill one slot (repeatable)"
    )
    ff.add_argument("-o", "--output", type=Path, default=None, help="output file")
    ff.set_defaults(func=_cmd_form)

    form.set_defaults(func=lambda _args: (form.print_help(), 0)[1])

    cls = sub.add_parser("classify", help="classify a doc: structured | weak | flat")
    cls.add_argument("file", type=Path, help=".hwpx file")
    cls.set_defaults(func=_cmd_classify)

    sty = sub.add_parser("styles", help="show machine style roles (for an AI)")
    sty.add_argument("file", type=Path, help=".hwpx file")
    sty.add_argument("--json", action="store_true", help="emit roles + styles as JSON")
    sty.set_defaults(func=_cmd_styles)

    ins = sub.add_parser(
        "instructions", help="show a template's AI:INSTRUCTION directions and {{slots}}"
    )
    ins.add_argument("file", type=Path, help=".hwpx template")
    ins.add_argument("--json", action="store_true", help="emit directions + slots as JSON")
    ins.set_defaults(func=_cmd_instructions)

    auth = sub.add_parser(
        "author", help="fill a structured template from Markdown, using its styles"
    )
    auth.add_argument("template", type=Path, help=".hwpx template")
    auth.add_argument("--md", type=Path, required=True, help="Markdown content file")
    auth.add_argument(
        "--chapter",
        default=None,
        help="chapter label/number for table captions (e.g. 7, A); "
        "recommended — auto-detection is unreliable on real documents",
    )
    auth.add_argument("-o", "--output", type=Path, default=None, help="output file")
    auth.set_defaults(func=_cmd_author)

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
