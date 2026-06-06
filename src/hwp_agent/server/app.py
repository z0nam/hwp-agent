"""FastAPI app: web upload UI + REST API for hwp-agent."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.background import BackgroundTask

from ..ops import analyze_form, fill_from_profile, load_profile

API_KEY_ENV = "HWP_AGENT_API_KEY"  # optional; if set, REST API requires X-API-Key
_API_KEY_HEADER = Header(default=None, alias="X-API-Key")


def _require_key(request_key: str | None) -> None:
    expected = os.environ.get(API_KEY_ENV)
    if expected and request_key != expected:
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def _ensure_hwpx(src: Path, workdir: Path) -> Path:
    """Return an .hwpx path: convert in place if a .hwp was uploaded."""
    if src.suffix.lower() == ".hwpx":
        return src
    from ..convert import Hwp2HwpxBackend

    backend = Hwp2HwpxBackend()
    if not backend.is_available():
        raise HTTPException(
            status_code=503,
            detail="conversion needs Java + the converter jar; run `hwp-agent setup` "
            "on the server, or upload an .hwpx instead.",
        )
    out = workdir / (src.stem + ".hwpx")
    result = backend.convert(src, out)
    if result.returncode != 0 or not out.is_file():
        raise HTTPException(status_code=500, detail="hwp→hwpx conversion failed")
    return out


def _save_upload(upload: UploadFile, workdir: Path) -> Path:
    name = Path(upload.filename or "form.hwpx").name
    dest = workdir / name
    with dest.open("wb") as fh:
        shutil.copyfileobj(upload.file, fh)
    return dest


def _download(path: Path, workdir: Path, filename: str) -> FileResponse:
    return FileResponse(
        path,
        filename=filename,
        media_type="application/octet-stream",
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>HWP 폼 자동 채우기</title>
<style>
 body{font-family:system-ui,sans-serif;max-width:640px;margin:6vh auto;padding:0 1.2rem;color:#222}
 h1{font-size:1.4rem} .card{border:1px solid #ddd;border-radius:12px;padding:1.4rem;margin-top:1rem}
 label{display:block;margin:.8rem 0 .3rem;font-weight:600} input[type=file]{width:100%}
 .row{display:flex;gap:1.2rem;align-items:center;margin:.6rem 0}
 button{margin-top:1.2rem;background:#1a6;color:#fff;border:0;border-radius:8px;padding:.8rem 1.4rem;font-size:1rem;cursor:pointer}
 .muted{color:#777;font-size:.9rem}
</style></head><body>
<h1>HWP 평가위원 폼 자동 채우기</h1>
<p class="muted">폼 파일(.hwp 또는 .hwpx)을 올리면 저장된 내 정보로 채워 돌려드립니다. 파일은 이 서버에만 머뭅니다.</p>
<div class="card">
<form action="fill" method="post" enctype="multipart/form-data">
  <label>① 폼 파일</label>
  <input type="file" name="file" accept=".hwp,.hwpx" required>
  <div class="row"><input type="checkbox" id="t" name="date" value="today" checked><label for="t" style="margin:0">오늘 날짜로 작성일 채우기</label></div>
  __TOKEN_FIELD__
  <button type="submit">② 내 정보로 채우기 → 내려받기</button>
</form>
</div>
<p class="muted">채워지지 않은 칸(주소·계좌 등 서식마다 다름)은 받은 파일에서 직접 확인하세요.</p>
</body></html>"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="hwp-agent",
        version="0.1.0",
        description="Fill and convert Korean HWP/HWPX forms. Self-hosted; files stay "
        "on this server. Use as a ChatGPT custom-GPT Action or a web upload page.",
    )
    web_token = os.environ.get("HWP_AGENT_WEB_TOKEN", "")

    def _check_web_token(token: str | None) -> None:
        if web_token and token != web_token:
            raise HTTPException(status_code=403, detail="missing or wrong token")

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True}

    @app.get("/", response_class=HTMLResponse)
    def home(token: str | None = None) -> str:
        _check_web_token(token)
        field = (
            f'<input type="hidden" name="token" value="{token}">' if web_token else ""
        )
        return _PAGE.replace("__TOKEN_FIELD__", field)

    @app.post("/fill")
    def web_fill(
        file: UploadFile = File(...),
        date: str | None = Form(None),
        token: str | None = Form(None),
    ):
        """Web form + GPT Action: fill an uploaded form from the server profile."""
        _check_web_token(token)
        workdir = Path(tempfile.mkdtemp(prefix="hwpsrv-"))
        try:
            src = _save_upload(file, workdir)
            hwpx = _ensure_hwpx(src, workdir)
            out = workdir / (Path(file.filename or "form").stem + "_채움.hwpx")
            try:
                load_profile(None)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            fill_from_profile(hwpx, None, output=out, date=date or None)
            return _download(out, workdir, out.name)
        except HTTPException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/analyze")
    def api_analyze(file: UploadFile = File(...), x_api_key: str | None = _API_KEY_HEADER):
        _require_key(x_api_key)
        workdir = Path(tempfile.mkdtemp(prefix="hwpsrv-"))
        try:
            src = _save_upload(file, workdir)
            hwpx = _ensure_hwpx(src, workdir)
            return JSONResponse(analyze_form(hwpx).as_dict())
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    @app.post("/api/fill-profile")
    def api_fill_profile(
        file: UploadFile = File(...),
        date: str | None = Form(None),
        x_api_key: str | None = _API_KEY_HEADER,
    ):
        """Fill from the server-side profile; returns the filled .hwpx."""
        _require_key(x_api_key)
        workdir = Path(tempfile.mkdtemp(prefix="hwpsrv-"))
        try:
            src = _save_upload(file, workdir)
            hwpx = _ensure_hwpx(src, workdir)
            out = workdir / (Path(file.filename or "form").stem + "_채움.hwpx")
            fill_from_profile(hwpx, None, output=out, date=date or None)
            return _download(out, workdir, out.name)
        except HTTPException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise
        except Exception as exc:  # noqa: BLE001
            shutil.rmtree(workdir, ignore_errors=True)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/convert")
    def api_convert(file: UploadFile = File(...), x_api_key: str | None = _API_KEY_HEADER):
        _require_key(x_api_key)
        workdir = Path(tempfile.mkdtemp(prefix="hwpsrv-"))
        try:
            src = _save_upload(file, workdir)
            if src.suffix.lower() != ".hwp":
                raise HTTPException(status_code=400, detail="upload a .hwp file")
            hwpx = _ensure_hwpx(src, workdir)
            return _download(hwpx, workdir, hwpx.name)
        except HTTPException:
            shutil.rmtree(workdir, ignore_errors=True)
            raise

    return app
