"""Verify the rendering quality of a converted PDF with a vision model.

Step 1 of the output-verification loop: given a PDF rendered by the **Hancom
engine** (the source of truth — the LibreOffice/H2Orestart path produces a
"translated-through-docx" look and is excluded from precise verification),
rasterize each page and ask a vision model whether the page rendered cleanly:
no missing figures, no broken layout (split tables, truncated text, overlapping
objects), no blank/failed pages.

This module is **pure Python and Hancom-independent**: it only needs a PDF as
input, so it runs anywhere (e.g. the Mac Studio), decoupled from how the PDF was
produced. The vision call is injectable (``vision_fn``) so tests and offline
runs need no API key or network.

Privacy: pages are rasterized **in memory** and sent only to the Anthropic API
(the sanctioned vision path). No page image is written to disk or sent anywhere
else — institutional documents stay local except for that one model call.
"""

from __future__ import annotations

import base64
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..render.rhwp import RenderFn
from ..render.rhwp import resolve_rhwp as _resolve_rhwp
from ..render.rhwp import rhwp_render_fn as _rhwp_render_fn

DEFAULT_MODEL = "claude-opus-4-8"
DEFAULT_DPI = 150

#: rendering-defect vocabulary the vision model classifies into
ISSUE_TYPES = (
    "missing_image",  # a figure/picture is gone (blank box, broken image, placeholder)
    "layout_broken",  # tables split, cells misaligned, structural collapse
    "text_truncated",  # text clipped at a margin or box edge
    "object_overlap",  # text/objects drawn on top of each other
    "empty_page",  # an unexpectedly blank page
    "render_failure",  # render artefacts (black blocks, garbled glyph runs)
)


@dataclass
class PageIssue:
    type: str
    detail: str

    def as_dict(self) -> dict:
        return {"type": self.type, "detail": self.detail}


@dataclass
class PageVerdict:
    page: int  # 1-based
    ok: bool
    issues: list[PageIssue] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "page": self.page,
            "ok": self.ok,
            "issues": [i.as_dict() for i in self.issues],
            "note": self.note,
        }


@dataclass
class VerifyResult:
    pdf: str
    page_count: int  # total pages in the PDF
    dpi: int
    model: str
    pages: list[PageVerdict] = field(default_factory=list)  # pages actually checked
    error: str = ""  # set when a guard failed (missing/empty/corrupt PDF)

    @property
    def passed(self) -> bool:
        return (
            not self.error
            and self.page_count > 0
            and bool(self.pages)
            and all(p.ok for p in self.pages)
        )

    @property
    def problem_pages(self) -> list[int]:
        return [p.page for p in self.pages if not p.ok]

    def as_dict(self) -> dict:
        return {
            "pdf": self.pdf,
            "page_count": self.page_count,
            "checked": len(self.pages),
            "dpi": self.dpi,
            "model": self.model,
            "passed": self.passed,
            "problem_pages": self.problem_pages,
            "pages": [p.as_dict() for p in self.pages],
            "error": self.error,
        }


#: a vision function: (png_bytes, page_number, total_pages) -> PageVerdict
VisionFn = Callable[[bytes, int, int], PageVerdict]

def _render_pages(pdf_path: Path, dpi: int) -> list[bytes]:
    """Rasterize each PDF page to PNG bytes, in memory. Raises on a corrupt PDF."""
    import fitz  # PyMuPDF — lazy (optional dependency)

    images: list[bytes] = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            images.append(page.get_pixmap(dpi=dpi).tobytes("png"))
    return images


def verify_pdf(
    pdf_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    model: str = DEFAULT_MODEL,
    vision_fn: VisionFn | None = None,
    max_pages: int | None = None,
) -> VerifyResult:
    """Rasterize *pdf_path* and judge each page's rendering quality.

    Returns a :class:`VerifyResult`. Guard failures (missing/empty/corrupt PDF)
    set ``result.error`` and leave ``passed`` False rather than raising, so a
    caller gets a structured verdict either way. ``vision_fn`` defaults to the
    Anthropic-backed judge; inject a fake to run without API/network.
    """
    pdf_path = Path(pdf_path)
    result = VerifyResult(pdf=str(pdf_path), page_count=0, dpi=dpi, model=model)

    if not pdf_path.is_file():
        result.error = f"PDF not found: {pdf_path}"
        return result
    if pdf_path.stat().st_size == 0:
        result.error = "PDF is empty (0 bytes)"
        return result

    try:
        images = _render_pages(pdf_path, dpi)
    except ModuleNotFoundError:
        raise  # surfaced by the CLI as an install hint
    except Exception as exc:  # noqa: BLE001 — any open/decode failure is a guard fail
        result.error = f"could not rasterize PDF (corrupt?): {exc}"
        return result

    result.page_count = len(images)
    if result.page_count == 0:
        result.error = "PDF has 0 pages"
        return result

    to_check = images if max_pages is None else images[:max_pages]
    judge = vision_fn or _anthropic_vision_fn(model)
    total = len(to_check)
    for idx, png in enumerate(to_check, start=1):
        result.pages.append(judge(png, idx, total))
    return result


# --------------------------------------------------------------------------- #
# .hwp/.hwpx input — render to PDF locally via rhwp, then verify (Tier 1).
# The rhwp helpers (_resolve_rhwp / _rhwp_render_fn / RenderFn) live in
# hwp_agent.render.rhwp and are imported above.
# --------------------------------------------------------------------------- #
def verify_hwp(
    hwp_path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    model: str = DEFAULT_MODEL,
    vision_fn: VisionFn | None = None,
    max_pages: int | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
) -> VerifyResult:
    """Render an .hwp/.hwpx to PDF (via rhwp) then verify it, all locally.

    Tier-1 verification: needs **no Hancom**. rhwp's layout differs from Hancom on
    exact pagination/spacing (see ``docs/output-verification.md``), so this is for
    *content / gross* defects (missing figures, blank pages, broken tables); use the
    Hancom-rendered PDF path (:func:`verify_pdf`) for authoritative layout sign-off.

    ``render_fn`` is injectable (like ``vision_fn``) so tests run without the rhwp
    binary. The reported ``pdf`` is the original source path.
    """
    hwp_path = Path(hwp_path)
    result = VerifyResult(pdf=str(hwp_path), page_count=0, dpi=dpi, model=model)
    if not hwp_path.is_file():
        result.error = f"input not found: {hwp_path}"
        return result

    render = render_fn
    if render is None:
        rb = _resolve_rhwp(rhwp_bin)
        if rb is None:
            raise FileNotFoundError(
                "rhwp not found — install the rhwp CLI on PATH or set RHWP_BIN "
                "(https://github.com/edwardkim/rhwp/releases)."
            )
        render = _rhwp_render_fn(rb)

    with tempfile.TemporaryDirectory(prefix="hwp_verify_") as td:
        out_pdf = Path(td) / "rendered.pdf"
        try:
            render(hwp_path, out_pdf)
        except Exception as exc:  # noqa: BLE001 — render failure is a guard fail
            result.error = f"render failed: {exc}"
            return result
        r = verify_pdf(
            out_pdf, dpi=dpi, model=model, vision_fn=vision_fn, max_pages=max_pages
        )
    r.pdf = str(hwp_path)  # report the source, not the temp render
    return r


def verify_document(
    path: str | Path,
    *,
    dpi: int = DEFAULT_DPI,
    model: str = DEFAULT_MODEL,
    vision_fn: VisionFn | None = None,
    max_pages: int | None = None,
    rhwp_bin: str | None = None,
    render_fn: RenderFn | None = None,
) -> VerifyResult:
    """Verify a document, dispatching by extension: .hwp/.hwpx via rhwp, else PDF."""
    if Path(path).suffix.lower() in (".hwp", ".hwpx"):
        return verify_hwp(
            path, dpi=dpi, model=model, vision_fn=vision_fn, max_pages=max_pages,
            rhwp_bin=rhwp_bin, render_fn=render_fn,
        )
    return verify_pdf(path, dpi=dpi, model=model, vision_fn=vision_fn, max_pages=max_pages)


# --------------------------------------------------------------------------- #
# default vision judge (Anthropic API)
# --------------------------------------------------------------------------- #
_VISION_TOOL = {
    "name": "report_page_quality",
    "description": "Report rendering defects found on this rendered document page.",
    "input_schema": {
        "type": "object",
        "properties": {
            "ok": {
                "type": "boolean",
                "description": "true only if the page rendered cleanly with no defect",
            },
            "issues": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": list(ISSUE_TYPES)},
                        "detail": {"type": "string"},
                    },
                    "required": ["type", "detail"],
                },
            },
            "note": {"type": "string"},
        },
        "required": ["ok", "issues"],
    },
}

_PROMPT = (
    "이 이미지는 한컴 엔진으로 렌더링한 문서(주로 한국 정부/정책 문서)의 한 페이지입니다.\n"
    "'내용의 옳고그름'이 아니라 '제대로 그려졌는가'만 판정하세요. 결함 분류:\n"
    "- missing_image: 그림/도표가 빠짐(빈 상자·깨진 이미지·자리표시자)\n"
    "- layout_broken: 표 깨짐·칸 어긋남 등 레이아웃 붕괴\n"
    "- text_truncated: 텍스트가 여백/상자 밖으로 잘림\n"
    "- object_overlap: 텍스트·객체가 서로 겹침\n"
    "- empty_page: 의도치 않게 빈 페이지\n"
    "- render_failure: 검은 블록·깨진 글리프 등 렌더 실패 흔적\n"
    "정상이면 ok=true, issues=[]. 결함이 있으면 ok=false로 두고 해당 type을 기록하세요.\n"
    "표지·간지처럼 원래 그림/글이 적은 페이지를 결함으로 오판하지 마세요.\n"
    "반드시 report_page_quality 도구로만 답하세요."
)


def _anthropic_vision_fn(model: str) -> VisionFn:
    """Build the default vision judge backed by the Anthropic API.

    Uses a forced tool call so the verdict is always structured. Reads
    ``ANTHROPIC_API_KEY`` from the environment.
    """

    def call(png: bytes, page: int, total: int) -> PageVerdict:
        import anthropic  # lazy (optional dependency)

        client = anthropic.Anthropic()
        b64 = base64.standard_b64encode(png).decode("ascii")
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            tools=[_VISION_TOOL],
            tool_choice={"type": "tool", "name": "report_page_quality"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"(페이지 {page}/{total})\n{_PROMPT}"},
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                    ],
                }
            ],
        )
        data = _tool_input(msg)
        issues = [
            PageIssue(type=str(i.get("type", "")), detail=str(i.get("detail", "")))
            for i in (data.get("issues") or [])
        ]
        # a clean page is ok=true AND no issues listed (defensive against models
        # that say ok but still report defects)
        return PageVerdict(
            page=page,
            ok=bool(data.get("ok", False)) and not issues,
            issues=issues,
            note=str(data.get("note", "")),
        )

    return call


def _tool_input(msg) -> dict:
    """Pull the forced tool-call input out of an Anthropic message."""
    for block in getattr(msg, "content", []) or []:
        if getattr(block, "type", None) == "tool_use":
            inp = getattr(block, "input", None)
            if isinstance(inp, dict):
                return inp
    return {
        "ok": False,
        "issues": [{"type": "render_failure", "detail": "no structured output from model"}],
    }
