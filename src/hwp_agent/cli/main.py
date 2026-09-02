"""Command-line entry point: ``hwp-agent``."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import __version__
from ..convert import Hwp2HwpxBackend

#: input file the current command is processing, for the manifest-fallback note
_ACTIVE_HWPX: str | None = None


class _ManifestFallbackFilter(logging.Filter):
    """Collapse python-hwpx's benign manifest-fallback warnings into one clear note.

    python-hwpx logs a warning whenever an .hwpx's OPC manifest doesn't declare its
    history/version parts and it falls back to searching by filename — once per part,
    on every open, so a single command emits the pair several times (``author`` opens
    the document three times). It almost always means the file was saved directly by
    Hangul (the desktop app), which omits those manifest entries; the fallback is
    harmless. We rewrite the first such warning into a plain-language note (naming the
    file) and drop the repeats.
    """

    _NOISE = ("찾지 못해", "fallback")

    def __init__(self) -> None:
        super().__init__()
        self._noted = False

    def filter(self, record: logging.LogRecord) -> bool:
        if not any(hint in record.getMessage() for hint in self._NOISE):
            return True  # a different hwpx warning — let it through unchanged
        if self._noted:
            return False  # benign repeat — drop
        self._noted = True
        where = f"'{_ACTIVE_HWPX}'" if _ACTIVE_HWPX else "입력 hwpx"
        record.msg = (
            f"참고: {where}의 manifest에 history/version 파트가 선언돼 있지 않아 "
            "파일명 기반 탐색으로 대체합니다 — 한글에서 직접 저장한 hwpx에서 흔한 "
            "정상 동작이며 결과에는 영향이 없습니다."
        )
        record.args = ()
        return True


def _quiet_hwpx_logging() -> None:
    """Route python-hwpx's logging through the manifest-fallback filter (CLI only)."""
    lg = logging.getLogger("hwpx")
    handler = logging.StreamHandler()  # stderr, like logging's last-resort handler
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(_ManifestFallbackFilter())
    lg.addHandler(handler)
    lg.propagate = False  # don't also hit the root/last-resort handler (avoids dupes)


def _guard_output(intended: Path):
    """Pick a safe output path that won't clobber an externally-edited file."""
    from ..ops import plan_output

    return plan_output(intended)


def _guard_finalize(gr, *, quiet: bool = False) -> None:
    """Stamp the provenance fingerprint; warn if we versioned to avoid a clobber."""
    from ..ops import stamp_fingerprint

    stamp_fingerprint(gr.target)
    if gr.versioned and not quiet:
        why = (
            "기존 출력이 외부에서 수정됨"
            if gr.reason == "versioned-drift"
            else "기존 파일에 hwp-agent 지문 없음"
        )
        print(f"  ⚠ 덮어쓰기 가드: {why} → 새 버전으로 저장 ({gr.target.name})")


def _cmd_convert(args: argparse.Namespace) -> int:
    backend = Hwp2HwpxBackend(jar_path=args.jar)
    if not backend.is_available():
        if not backend.has_java():
            print(
                "error: .hwp 변환에는 Java(JRE)가 필요합니다.\n"
                "  - 설치 프로그램(hwp-agent-setup.exe)으로 설치하면 Java가 함께 깔립니다, 또는\n"
                "  - Temurin JRE 17을 직접 설치: https://adoptium.net/temurin/releases/?version=17\n"
                "  (폼 채우기·편집은 Java 없이 됩니다 — .hwp→.hwpx 변환에만 필요)",
                file=sys.stderr,
            )
        else:
            print(
                f"error: 변환기 jar를 찾을 수 없습니다 (jar={backend.jar_path}).\n"
                "  `hwp-agent setup` 으로 내려받으세요 (또는 scripts/bootstrap.sh 로 빌드).",
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


def _cmd_setup(args: argparse.Namespace) -> int:
    """Fetch the converter jar and report runtime readiness (Windows-friendly)."""
    import shutil

    from ..convert.fetch_jar import JAR_URL, download_jar, jar_dest

    dest = jar_dest()
    if dest.is_file() and not args.force:
        print(f"converter jar already present: {dest}")
    else:
        print(f"downloading converter jar\n  from {JAR_URL}\n  to   {dest}")
        try:
            dest = download_jar(force=args.force)
        except Exception as exc:  # noqa: BLE001 — surface any network/IO error plainly
            print(f"error: could not download jar: {exc}", file=sys.stderr)
            print(
                "  set HWP2HWPX_JAR_URL to a reachable mirror, or build with "
                "scripts/bootstrap.sh.",
                file=sys.stderr,
            )
            return 1
        print(f"ok: jar ready at {dest}")

    java = shutil.which("java")
    if java:
        print(f"ok: java runtime found ({java})")
    else:
        print(
            "warn: no `java` on PATH — install a JRE 17+ to run conversions "
            "(e.g. Temurin: https://adoptium.net/).",
            file=sys.stderr,
        )
    print("\nyou can now run:  hwp-agent convert input.hwp output.hwpx")
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    """Run the self-hosted HTTP server (web UI + REST API + OpenAPI)."""
    try:
        import uvicorn

        from ..server import create_app
    except ModuleNotFoundError:
        print(
            "error: the server needs extra packages. Install them with:\n"
            '  pip install "hwp-agent[serve]"\n'
            '  (or: uv pip install fastapi "uvicorn[standard]" python-multipart)',
            file=sys.stderr,
        )
        return 2
    print(
        f"hwp-agent serve → http://{args.host}:{args.port}  "
        "(web UI at /, OpenAPI at /openapi.json)"
    )
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


def _cmd_mcp(args: argparse.Namespace) -> int:
    """Run the local stdio MCP server (for Claude Desktop / Claude Code / Codex)."""
    from ..mcp_server import main as mcp_main

    return mcp_main()


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

    from ..ops import analyze_form, dump_grid, fill_form

    if args.action == "autofit":
        from ..ops import autofit_table

        mins: dict[int, int] = {}
        for item in args.min_width or []:
            k, _, v = item.partition("=")
            try:
                mins[int(k)] = int(v)
            except ValueError:
                print(f"error: --min-width expects COL=N, got {item!r}", file=sys.stderr)
                return 2
        gr = _guard_output(args.output or args.file)
        try:
            summary = autofit_table(
                args.file, args.table, min_widths=mins or None, output=gr.target
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        _guard_finalize(gr)
        if args.json:
            print(json.dumps(summary, ensure_ascii=False, indent=2))
        else:
            olds = ", ".join(str(w) for w in summary["old_widths"])
            news = ", ".join(str(w) for w in summary["new_widths"])
            print(f"table {summary['table_index']} ({summary['ncols']} cols) -> {gr.target}")
            print(f"  widths: [{olds}]  ->  [{news}]  (total {summary['total']})")
        return 0

    if args.action == "analyze":
        if args.grid:
            cells = dump_grid(args.file)
            if args.json:
                print(
                    json.dumps(
                        {"cells": [c.as_dict() for c in cells]},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                if not cells:
                    print("(no tables found)")
                for c in cells:
                    preview = c.text.replace("\n", " ")
                    if len(preview) > 60:
                        preview = preview[:57] + "..."
                    print(f"{c.cell_path:16} {preview}")
            return 0

        spec = analyze_form(args.file)
        if args.json:
            print(json.dumps(spec.as_dict(), ensure_ascii=False, indent=2))
        else:
            if not spec.slots:
                print("(no fillable slots found)")
            for s in spec.slots:
                cur = f"  [{s.current}]" if s.current else ""
                print(f"{s.kind:11} {s.name}  ->  {s.locator}{cur}")
            if spec.slots:
                print(
                    "\nnote: slots cover label -> empty-neighbour cells only. "
                    "To overwrite a cell that already has text, address it "
                    "directly with --set 'cell:<table>:<row>:<col>=...' "
                    "(list them with --grid)."
                )
        return 0

    # fill from a personal-data profile (auto-map standing data to slots)
    if args.profile is not None:
        from ..ops import fill_from_profile

        prof_path = args.profile or None  # "" (bare --profile) -> default location
        gr = _guard_output(args.output or args.file)
        try:
            result = fill_from_profile(
                args.file, prof_path, output=gr.target, date=args.date,
                precise=args.precise,
            )
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.json:
            _guard_finalize(gr, quiet=True)
            d = result.as_dict()
            d["output"] = str(gr.target)
            d["versioned"] = gr.versioned
            print(json.dumps(d, ensure_ascii=False, indent=2))
            return 0
        _guard_finalize(gr)
        print(f"filled {len(result.filled)} from profile -> {gr.target}")
        for m in result.filled:
            print(f"  ok: {m.slot}  <-  {m.field} = {m.value}")
        if result.blank:
            print(f"  left blank ({len(result.blank)}): {', '.join(result.blank)}")
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

    gr = _guard_output(args.output or args.file)
    result = fill_form(
        args.file, mapping, output=gr.target, precise=args.precise,
        allow_table_flow=getattr(args, "allow_table_flow", False),
    )
    _guard_finalize(gr)
    print(f"filled {len(result.filled)} -> {gr.target}")
    if result.filled:
        print(f"  ok: {', '.join(result.filled)}")
    if result.missing:
        print(f"  missing: {', '.join(result.missing)}")
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    return 1 if result.missing and not result.filled else 0


def _render_cmd(args: argparse.Namespace, *, fmt: str, engine: str) -> int:
    import dataclasses

    from ..render import render_document, resolve_hwp2pdf_config

    src: Path = args.input
    if not src.is_file():
        print(f"error: input not found: {src}", file=sys.stderr)
        return 2
    out: Path = args.output or src.with_suffix(f".{fmt}")

    config = resolve_hwp2pdf_config(getattr(args, "config", None))
    if config is not None and getattr(args, "timeout", None):
        config = dataclasses.replace(config, convert_timeout=args.timeout)

    result = render_document(
        src, out, fmt=fmt, engine=engine, config=config,
        rhwp_bin=getattr(args, "rhwp", None),
    )
    if result.ok:
        where = "/namun-ji" if result.remote else ""
        print(f"wrote {out}  (via {result.backend}{where})")
        return 0
    print(f"error: {result.stderr}", file=sys.stderr)
    return result.returncode or 1


def _cmd_pdf(args: argparse.Namespace) -> int:
    return _render_cmd(args, fmt="pdf", engine=args.engine)


def _cmd_docx(args: argparse.Namespace) -> int:
    return _render_cmd(args, fmt="docx", engine="hwp2pdf")


def _cmd_verify(args: argparse.Namespace) -> int:
    import json
    import os

    from ..ops import verify_document

    # vision verification goes through the Anthropic API; fail fast (before
    # rendering/rasterizing) if the key is missing.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "error: 비전 검증에는 ANTHROPIC_API_KEY 환경변수가 필요합니다.\n"
            "  export ANTHROPIC_API_KEY=sk-ant-...  후 다시 실행하세요.",
            file=sys.stderr,
        )
        return 2
    try:
        result = verify_document(
            args.file, dpi=args.dpi, model=args.model, max_pages=args.max_pages,
            rhwp_bin=args.rhwp,
        )
    except FileNotFoundError as exc:  # rhwp binary missing (.hwp/.hwpx input)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except ModuleNotFoundError:
        print(
            "error: 검증에는 추가 패키지가 필요합니다:\n"
            '  pip install "hwp-agent[verify]"\n'
            "  (또는: uv pip install pymupdf anthropic)",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0 if result.passed else 1

    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
        return 1
    status = "PASS" if result.passed else "FAIL"
    print(
        f"{status}  {result.pdf}  "
        f"({len(result.pages)}/{result.page_count} pages @ {result.dpi}dpi, {result.model})"
    )
    for p in result.pages:
        mark = "✓" if p.ok else "✗"
        print(f"  {mark} p.{p.page}")
        for i in p.issues:
            print(f"      - {i.type}: {i.detail}")
        if p.note and not p.ok:
            print(f"      note: {p.note}")
    if result.problem_pages:
        print(f"problem pages: {', '.join(map(str, result.problem_pages))}")
    return 0 if result.passed else 1


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


def _cmd_extract(args: argparse.Namespace) -> int:
    from ..ops import extract_markdown

    md = extract_markdown(args.file, body_only=args.body_only)
    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
        print(f"wrote {args.output}  ({len(md):,} chars, {md.count(chr(10)):,} lines)")
    else:
        sys.stdout.write(md)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    import json

    from ..ops import diagnose_template

    r = diagnose_template(str(args.file))
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    print(f"{r['file']}  [{r['classification']}]   "
          f"styles={r['styles_total']} roled={r['roles_mapped']}")
    for name in ("HEADING", "BULLET", "ORDERED"):
        rungs = r["ladders"].get(name) or []
        if not rungs and name == "ORDERED":
            continue
        print(f"{name} ladder:")
        if not rungs:
            print("  (none)")
        for rung in rungs:
            print(f"  {rung['role']:<11} -> style {rung['style']:<3} "
                  f"{rung['name']:<16} {rung['size']}pt")
    if r["body"]:
        b = r["body"]
        print(f"BODY        -> style {b['style']:<3} {b['name']:<16} {b['size']}pt")
    print(f"INSTRUCTION -> {r['instruction'] or '(none)'}")

    if r["unmapped_ladder_siblings"]:
        print("un-mapped ladder siblings (author can't target these):")
        for e in r["unmapped_ladder_siblings"][:10]:
            print(f"  style {e['style']:<3} {e['name']:<16} {e['size']}pt  "
                  f"{e['heading']} L{e['level']}  used {e['use']}×")
    if r["unmapped_structural"]:
        print(f"un-mapped structural styles ({len(r['unmapped_structural'])}):")
        for e in r["unmapped_structural"][:12]:
            print(f"  style {e['style']:<3} {e['name']:<16} {e['size']}pt  used {e['use']}×")

    if r["warnings"]:
        print("\nfindings:")
        for w in r["warnings"]:
            print(f"  ⚠ {w}")
    else:
        print("\n✓ no issues found")
    return 0


def _cmd_normalize(args: argparse.Namespace) -> int:
    import json

    from ..ops import apply_normalization, plan_normalization

    plan = plan_normalization(args.file)
    output = args.output or args.file.with_suffix(".normalized.hwpx")

    if args.json:
        report = plan.as_dict()
        report["output"] = None if args.dry_run else str(output)
        report["applied"] = bool(plan.actions) and not args.dry_run
        if report["applied"]:
            apply_normalization(args.file, plan, output)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"{plan.file}  [{plan.classification_before}]")
    if plan.actions:
        print("선언 계획:")
        for a in plan.actions:
            size = f"{a.size}pt" if a.size is not None else "?pt"
            print(f"  style {a.style_id:<3} '{a.name}' {size:<7} → {a.declaration}"
                  f"  (근거: {a.rationale})")
    else:
        print("선언할 항목 없음.")
    if plan.already_declared:
        print(f"기존 선언 {len(plan.already_declared)}건:")
        for d in plan.already_declared:
            print(f"  style {d['style_id']:<3} '{d['name']}' → {d['role']}")
    if plan.skipped:
        print("건너뜀 (사람 판단 필요):")
        for s in plan.skipped:
            print(f"  style {s.style_id:<3} '{s.name}' (사용 {s.use_count}×) — {s.reason}")
    for w in plan.warnings:
        print(f"  ⚠ {w}")

    if args.dry_run or not plan.actions:
        print("\n(변경 없음 — dry-run이거나 적용할 선언이 없습니다)")
        return 0

    apply_normalization(args.file, plan, output)
    print(f"\nwrote {output}  [{plan.classification_before} → {plan.classification_expected}]")
    print("다음 단계:")
    print("  1. 한글에서 결과 파일을 열어 보안 경고가 없는지, 스타일(F6) 영문 이름에")
    print("     AI:HEADING_n/AI:BULLET_n이 들어갔는지 확인")
    print(f"  2. hwp-agent classify '{output}' / hwp-agent check '{output}'")
    print(f"  3. hwp-agent write content.md --template '{output}' -o result.hwpx 로 시험")
    print("  ※ 이 서식의 헤딩 번호는 리터럴 텍스트입니다 — 마크다운에 번호를 직접")
    print("     쓰세요 (예: '# Ⅰ. 서론', '## 1. 추진 배경').")
    return 0


def _cmd_image(args: argparse.Namespace) -> int:
    import json

    from ..ops import list_images, replace_image

    if args.action == "list":
        pics = list_images(args.file)
        if args.json:
            print(json.dumps([p.as_dict() for p in pics], ensure_ascii=False, indent=2))
            return 0
        if not pics:
            print("(no figure images found)")
            return 0
        for p in pics:
            ratio = f"{p.extent_ratio}" if p.extent_ratio is not None else "?"
            px = f"{p.px_w}x{p.px_h}px" if p.px_w and p.px_h else "?px"
            print(f"{p.ref:<8} .{p.slot_format:<4} {px:<12} ratio={ratio:<7} {p.caption}")
        return 0

    # replace
    if bool(args.ref) == bool(args.caption):
        print("error: pass exactly one of --ref or --caption", file=sys.stderr)
        return 2

    gr = _guard_output(args.output or args.file)
    result = replace_image(
        args.file,
        ref=args.ref,
        caption=args.caption,
        image=args.image,
        fit=args.fit,
        output=gr.target,
    )
    for o in result.outcomes:
        detail = f"  ({o.detail})" if o.detail else ""
        print(f"{o.status}: {o.selector} <- {o.image}{detail}")
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    if result.replaced:
        _guard_finalize(gr)
        print(f"replaced {result.replaced} -> {gr.target}")
        return 0
    return 1


def _cmd_text(args: argparse.Namespace) -> int:
    import json

    from ..ops import TextEditError, find_anchors, insert_markdown

    if args.action == "find":
        hits = find_anchors(args.file, args.anchor)
        if args.json:
            print(json.dumps([h.__dict__ for h in hits], ensure_ascii=False, indent=2))
            return 0
        if not hits:
            print("(no paragraph matched)")
            return 1
        for i, h in enumerate(hits, 1):
            print(f"[{i}] {h.section} #{h.index} depth={h.depth}  {h.text[:70]}")
        return 0

    # insert
    if bool(args.after) == bool(args.before):
        print("error: pass exactly one of --after or --before", file=sys.stderr)
        return 2
    if bool(args.md) == bool(args.text):
        print("error: pass exactly one of --md or --text", file=sys.stderr)
        return 2

    markdown = Path(args.md).read_text(encoding="utf-8") if args.md else args.text
    gr = _guard_output(args.output or args.file)
    try:
        result = insert_markdown(
            args.file,
            markdown,
            anchor=args.after or args.before,
            where="after" if args.after else "before",
            output=gr.target,
            occurrence=args.occurrence,
            anchor_level=args.anchor_level,
        )
    except TextEditError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for w in result.warnings:
        print(f"  warning: {w}", file=sys.stderr)
    _guard_finalize(gr)
    print(
        f"inserted {result.inserted} paragraph(s) {result.where} "
        f"{result.anchor.section} #{result.anchor.index} -> {gr.target}"
    )
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


def _cmd_write(args: argparse.Namespace) -> int:
    from ..ops import fill_from_markdown
    from ..ops.template import describe_template_source, resolve_template_path

    template = resolve_template_path(args.template)
    if not template.is_file():
        print(f"template not found: {template}", file=sys.stderr)
        return 2
    if args.template is None:
        print(f"using {describe_template_source(None)} template: {template}")
    markdown = Path(args.md).read_text(encoding="utf-8")
    # in-place default targets the markdown's stem, never the (possibly shared)
    # template — writing back onto the bundled default would corrupt it.
    default_out = args.output or Path(args.md).with_suffix(".hwpx")
    gr = _guard_output(default_out)
    result = fill_from_markdown(
        template,
        markdown,
        output=gr.target,
        chapter=args.chapter,
        table_template=args.table_template,
    )
    _guard_finalize(gr)
    print(f"placed {result.placed} block(s) -> {gr.target}")
    if result.instructions_removed:
        print(f"  removed {result.instructions_removed} instruction paragraph(s)")
    if result.unmapped_roles:
        print(f"  unmapped (fell back to BODY): {', '.join(result.unmapped_roles)}")
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)
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

    setup = sub.add_parser(
        "setup", help="download the converter jar + check the Java runtime"
    )
    setup.add_argument(
        "--force", action="store_true", help="re-download even if the jar exists"
    )
    setup.set_defaults(func=_cmd_setup)

    srv = sub.add_parser("serve", help="run the self-hosted web UI + REST API server")
    srv.add_argument("--host", default="127.0.0.1", help="bind address (default 127.0.0.1)")
    srv.add_argument("--port", type=int, default=8765, help="port (default 8765)")
    srv.set_defaults(func=_cmd_serve)

    mcpp = sub.add_parser("mcp", help="run the local stdio MCP server (for AI clients)")
    mcpp.set_defaults(func=_cmd_mcp)

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
    fa.add_argument(
        "--grid",
        action="store_true",
        help="list every table cell with its cell:<table>:<row>:<col> fill path",
    )
    fa.set_defaults(func=_cmd_form)

    faf = form_sub.add_parser(
        "autofit", help="rebalance a table's column widths by content (no text change)"
    )
    faf.add_argument("file", type=Path, help=".hwpx form")
    faf.add_argument("--table", type=int, default=0, help="table index (document order, default 0)")
    faf.add_argument(
        "--min-width", action="append", metavar="COL=N", dest="min_width",
        help="pin a column's minimum width in HWPUNIT (repeatable)",
    )
    faf.add_argument("--json", action="store_true", help="emit the width summary as JSON")
    faf.add_argument(
        "-o", "--output", type=Path, default=None, help="destination (default in place)"
    )
    faf.set_defaults(func=_cmd_form)

    ff = form_sub.add_parser("fill", help="fill slots by name/path/profile")
    ff.add_argument("file", type=Path, help=".hwpx form")
    ff.add_argument("--map", type=Path, default=None, help="JSON file of {slot: value}")
    ff.add_argument(
        "--set", action="append", metavar="KEY=VALUE", help="fill one slot (repeatable)"
    )
    ff.add_argument(
        "--profile",
        nargs="?",
        const="",
        default=None,
        metavar="PATH",
        help="auto-fill from a personal-data profile "
        "(default: ~/.config/hwp-agent/profile.json)",
    )
    ff.add_argument(
        "--date", choices=["today"], default=None, help="resolve date slots to today"
    )
    ff.add_argument(
        "--allow-table-flow",
        dest="allow_table_flow",
        action="store_true",
        help="let long cell content flow across pages (treatAsChar=0, pageBreak=CELL) "
        "instead of being truncated — changes the table's layout semantics (issue #12)",
    )
    ff.add_argument(
        "--precise",
        dest="precise",
        action="store_true",
        default=True,
        help="precise label-cell detection (default)",
    )
    ff.add_argument(
        "--no-precise",
        dest="precise",
        action="store_false",
        help="emit every empty-neighbour cell as a slot (blank templates)",
    )
    ff.add_argument("--json", action="store_true", help="emit fill report as JSON")
    ff.add_argument("-o", "--output", type=Path, default=None, help="output file")
    ff.set_defaults(func=_cmd_form)

    form.set_defaults(func=lambda _args: (form.print_help(), 0)[1])

    vfy = sub.add_parser(
        "verify",
        help="check a rendered PDF's quality with a vision model "
        "(missing figures, broken layout, blank pages)",
    )
    vfy.add_argument(
        "file",
        type=Path,
        help="PDF (Hancom-rendered, authoritative), or .hwp/.hwpx (rendered locally via rhwp)",
    )
    vfy.add_argument(
        "--rhwp", default=None,
        help="path to the rhwp CLI for .hwp/.hwpx input (default: $RHWP_BIN or PATH)",
    )
    vfy.add_argument("--dpi", type=int, default=150, help="rasterization DPI (default 150)")
    vfy.add_argument(
        "--model", default="claude-opus-4-8", help="vision model (default claude-opus-4-8)"
    )
    vfy.add_argument(
        "--max-pages", type=int, default=None, dest="max_pages",
        help="only check the first N pages",
    )
    vfy.add_argument("--json", action="store_true", help="emit the report as JSON")
    vfy.set_defaults(func=_cmd_verify)

    rpdf = sub.add_parser(
        "pdf", help="render an HWP/HWPX to PDF (hwp2pdf/namun-ji if reachable, else rhwp)",
    )
    rpdf.add_argument("input", type=Path, help="source .hwp/.hwpx file")
    rpdf.add_argument(
        "-o", "--output", type=Path, default=None,
        help="destination .pdf (default: alongside input)",
    )
    rpdf.add_argument(
        "--engine", choices=("auto", "hwp2pdf", "rhwp"), default="auto",
        help="auto = hwp2pdf if reachable, else rhwp (Tier-1)",
    )
    rpdf.add_argument("--rhwp", default=None, help="path to the rhwp CLI (Tier-1)")
    rpdf.add_argument(
        "--config", type=Path, default=None,
        help="hwp2pdf endpoint config (default: ~/.config/hwp-agent/hwp2pdf.json)",
    )
    rpdf.add_argument(
        "--timeout", type=int, default=None, dest="timeout",
        help="Tier-2 convert timeout in seconds (overrides config)",
    )
    rpdf.set_defaults(func=_cmd_pdf)

    rdocx = sub.add_parser(
        "docx", help="render an HWP/HWPX to DOCX (Hancom hwp2pdf/namun-ji only)",
    )
    rdocx.add_argument("input", type=Path, help="source .hwp/.hwpx file")
    rdocx.add_argument(
        "-o", "--output", type=Path, default=None,
        help="destination .docx (default: alongside input)",
    )
    rdocx.add_argument(
        "--config", type=Path, default=None, help="hwp2pdf endpoint config",
    )
    rdocx.add_argument(
        "--timeout", type=int, default=None, dest="timeout",
        help="convert timeout in seconds (overrides config)",
    )
    rdocx.set_defaults(func=_cmd_docx)

    cls = sub.add_parser("classify", help="classify a doc: structured | weak | flat")
    cls.add_argument("file", type=Path, help=".hwpx file")
    cls.set_defaults(func=_cmd_classify)

    sty = sub.add_parser("styles", help="show machine style roles (for an AI)")
    sty.add_argument("file", type=Path, help=".hwpx file")
    sty.add_argument("--json", action="store_true", help="emit roles + styles as JSON")
    sty.set_defaults(func=_cmd_styles)

    chk = sub.add_parser(
        "check",
        aliases=["doctor"],
        help="check a template's style system (gaps, hierarchy, unmapped styles)",
    )
    chk.add_argument("file", type=Path, help=".hwpx template")
    chk.add_argument("--json", action="store_true", help="emit the full report as JSON")
    chk.set_defaults(func=_cmd_check)

    norm = sub.add_parser(
        "normalize",
        help="declare AI:HEADING_n/AI:BULLET_n on a flat template so classify/write work",
    )
    norm.add_argument("file", type=Path, help=".hwpx flat template (never modified)")
    norm.add_argument(
        "-o", "--output", type=Path, default=None,
        help="output path (default: <input>.normalized.hwpx)",
    )
    norm.add_argument("--dry-run", action="store_true", help="print the plan, write nothing")
    norm.add_argument("--json", action="store_true", help="emit the report as JSON")
    norm.set_defaults(func=_cmd_normalize)

    ins = sub.add_parser(
        "instructions", help="show a template's AI:INSTRUCTION directions and {{slots}}"
    )
    ins.add_argument("file", type=Path, help=".hwpx template")
    ins.add_argument("--json", action="store_true", help="emit directions + slots as JSON")
    ins.set_defaults(func=_cmd_instructions)

    ext = sub.add_parser(
        "extract",
        help="extract an HWPX as body-focused Markdown (complex tables unmerged)",
    )
    ext.add_argument("file", type=Path, help=".hwpx file")
    ext.add_argument(
        "--body-only",
        action="store_true",
        help="skip front matter (cover, TOC, ...): emit from the first level-1 heading on",
    )
    ext.add_argument("-o", "--output", type=Path, default=None, help="output .md file")
    ext.set_defaults(func=_cmd_extract)

    img = sub.add_parser(
        "image", help="list or replace embedded figure images in an HWPX"
    )
    img_sub = img.add_subparsers(dest="action", metavar="<list|replace>")

    il = img_sub.add_parser("list", help="list figure image slots (ref, format, size, caption)")
    il.add_argument("file", type=Path, help=".hwpx file")
    il.add_argument("--json", action="store_true", help="emit slots as JSON (for an AI)")
    il.set_defaults(func=_cmd_image)

    ir = img_sub.add_parser("replace", help="replace one figure image in place")
    ir.add_argument("file", type=Path, help=".hwpx file")
    ir.add_argument("image", type=Path, help="new image file")
    ir.add_argument("--ref", default=None, help="target by binaryItemIDRef (e.g. image7)")
    ir.add_argument(
        "--caption", default=None, help="target by caption text (exact, then substring)"
    )
    ir.add_argument(
        "--fit",
        choices=("aspect", "none"),
        default="aspect",
        help="aspect: keep width, recompute height so the image isn't stretched "
        "(default); none: leave the box as-is",
    )
    ir.add_argument("-o", "--output", type=Path, default=None, help="output file")
    ir.set_defaults(func=_cmd_image)

    img.set_defaults(func=lambda _args: (img.print_help(), 0)[1])

    txt = sub.add_parser(
        "text",
        help="insert paragraphs into an already type-set HWPX (no re-typeset)",
    )
    txt_sub = txt.add_subparsers(dest="action", metavar="<find|insert>")

    tf = txt_sub.add_parser("find", help="list paragraphs matching an anchor")
    tf.add_argument("file", type=Path, help=".hwpx file")
    tf.add_argument("anchor", help="text to look for (substring)")
    tf.add_argument("--json", action="store_true", help="emit matches as JSON")
    tf.set_defaults(func=_cmd_text)

    ti = txt_sub.add_parser("insert", help="insert Markdown paragraphs next to an anchor")
    ti.add_argument("file", type=Path, help=".hwpx file")
    ti.add_argument("--after", default=None, help="anchor paragraph to insert after")
    ti.add_argument("--before", default=None, help="anchor paragraph to insert before")
    ti.add_argument("--md", type=Path, default=None, help="Markdown file to insert")
    ti.add_argument("--text", default=None, help="Markdown text to insert")
    ti.add_argument(
        "--occurrence",
        type=int,
        default=None,
        help="1-based pick when the anchor matches several paragraphs",
    )
    ti.add_argument(
        "--anchor-level",
        type=int,
        default=1,
        help="which Markdown bullet level the anchor paragraph itself sits at "
        "(default 1: a top-level bullet lands where the anchor is)",
    )
    ti.add_argument("-o", "--output", type=Path, default=None, help="output file")
    ti.set_defaults(func=_cmd_text)

    txt.set_defaults(func=lambda _args: (txt.print_help(), 0)[1])

    wr = sub.add_parser(
        "write",
        aliases=["author"],
        help="write Markdown into a structured template, using its styles",
    )
    wr.add_argument("md", type=Path, help="Markdown content file to write")
    wr.add_argument(
        "--template",
        type=Path,
        default=None,
        help=".hwpx template to fill (default: $HWP_AGENT_TEMPLATE, "
        "~/.config/hwp-agent/template.hwpx, or the bundled content template)",
    )
    wr.add_argument(
        "--chapter",
        default=None,
        help="chapter label/number for table captions (e.g. 7, A); "
        "recommended — auto-detection is unreliable on real documents",
    )
    wr.add_argument(
        "--table-template",
        default=None,
        metavar="CAPTION",
        help="caption text of the table whose format generated tables copy "
        "(use when the {{table_template}} token was consumed by a prior run)",
    )
    wr.add_argument("-o", "--output", type=Path, default=None, help="output file")
    wr.set_defaults(func=_cmd_write)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    global _ACTIVE_HWPX
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # validate input files up front so a missing path is a clear message, not a
    # Python traceback (output paths are never checked — they're created).
    for attr in ("input", "file", "template", "md", "image"):
        path = getattr(args, attr, None)
        if path is not None and not Path(path).is_file():
            print(f"error: 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
            return 2
    _quiet_hwpx_logging()
    src = (
        getattr(args, "template", None)
        or getattr(args, "file", None)
        or getattr(args, "input", None)
    )
    _ACTIVE_HWPX = str(src) if src else None
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
