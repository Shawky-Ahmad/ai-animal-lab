"""ADMIN FastAPI app: upload console (/), dashboard (/dashboard), /api/admin/*.

Bound to 127.0.0.1 by default (never reachable off the laptop). When the
ADMIN_PIN env var is set, the app additionally requires that PIN (HTTP Basic
auth, any username) and run.py binds it to all interfaces so a helper can
open the dashboard from another device via the LAN or a second tunnel.
Binding spec: ../API_CONTRACT.md.
"""
from __future__ import annotations

import asyncio
import base64
import csv
import hmac
import io
import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from . import db
from .config import ADMIN_FRONTEND_DIR, IMAGE_EXTENSIONS, settings

app = FastAPI(title="AI Animal Lab — Admin")

_ADMIN_PIN = settings.admin_pin
_UNAUTHORIZED = {
    "status_code": 401,
    "headers": {"WWW-Authenticate": 'Basic realm="AI Animal Lab instructor tools", charset="UTF-8"'},
}


@app.middleware("http")
async def require_pin(request: Request, call_next):
    """Optional shared-PIN gate (HTTP Basic; any username, PIN as password)."""
    if not _ADMIN_PIN:
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:].strip()).decode("utf-8", "replace")
            _user, _, password = decoded.partition(":")
            if hmac.compare_digest(password, _ADMIN_PIN):
                return await call_next(request)
        except Exception:
            pass
        await asyncio.sleep(0.3)  # slow PIN guessing down a little
    return Response(**_UNAUTHORIZED)

_PREVIEW_RE = re.compile(r"^[a-f0-9]{32}\.(jpg|jpeg|png|webp)$")

_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# extension groups that may carry each magic-byte kind
_EXT_FOR_KIND = {
    "jpeg": {".jpg", ".jpeg"},
    "png": {".png"},
    "webp": {".webp"},
}


def _magic_kind(head: bytes) -> str | None:
    """Identify jpg/png/webp from leading bytes; None if none matches."""
    if head[:3] == b"\xff\xd8\xff":                      # JPEG: FF D8 FF
        return "jpeg"
    if head[:4] == b"\x89\x50\x4e\x47":                  # PNG: 89 50 4E 47
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":    # WEBP: RIFF....WEBP
        return "webp"
    return None


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

@app.get("/api/admin/config")
def admin_config() -> dict:
    """Same shape as the public /api/config — lets the console read the
    category list same-origin without falling back to catalog keys."""
    return {"app_name": "AI Animal Lab — Admin", "categories": list(settings.categories)}


@app.get("/api/admin/summary")
def admin_summary() -> dict:
    return db.build_summary()


@app.post("/api/admin/upload")
async def admin_upload(category: str = Form(...),
                       files: list[UploadFile] = File(...)) -> dict:
    categories = list(settings.categories)
    uploaded: list[dict] = []
    failed: list[dict] = []

    if category not in categories:
        reason = f"invalid category '{category}' (configured: {', '.join(categories)})"
        for f in files:
            failed.append({"name": f.filename or "<unnamed>", "reason": reason})
        return {"uploaded": uploaded, "failed": failed}

    max_files = settings.max_files_per_upload
    for idx, f in enumerate(files):
        name = f.filename or "<unnamed>"
        if idx >= max_files:
            failed.append({"name": name,
                           "reason": f"too many files per request (max {max_files})"})
            continue

        ext = Path(name).suffix.lower()
        if ext not in IMAGE_EXTENSIONS:
            failed.append({"name": name, "reason": "not a jpg/png/webp image"})
            continue

        # size cap: inspect the spooled file without loading it into memory
        f.file.seek(0, io.SEEK_END)
        size = f.file.tell()
        f.file.seek(0)
        if size > settings.max_upload_bytes:
            failed.append({"name": name,
                           "reason": f"file too large (max {settings.max_upload_mb} MB)"})
            continue

        head = await f.read(16)
        await f.seek(0)
        kind = _magic_kind(head)
        if kind is None:
            failed.append({"name": name, "reason": "not a jpg/png/webp image"})
            continue
        if ext not in _EXT_FOR_KIND[kind]:
            failed.append({"name": name,
                           "reason": f"file content is {kind} but extension is '{ext}'"})
            continue

        filename = uuid.uuid4().hex + ext  # server-generated, never client path
        dest = db.images_dir() / filename
        try:
            with dest.open("wb") as out:
                shutil.copyfileobj(f.file, out, length=1024 * 256)
            choices = db.compute_choices(category, categories)
            image_id = db.insert_image(filename, name, category, json.dumps(choices))
        except OSError as exc:
            failed.append({"name": name, "reason": f"could not store file: {exc}"})
            continue
        uploaded.append({"image_id": image_id, "filename": filename,
                         "category": category})

    return {"uploaded": uploaded, "failed": failed}


@app.get("/api/admin/images")
def admin_images() -> dict:
    with db.get_conn() as conn:
        counts = db.image_counts_by_category(conn)
        total = db.count_images(conn)
        images = db.admin_list_images(conn)
    filled = {cat: counts.get(cat, 0) for cat in settings.categories}
    return {"counts": filled, "total": total, "images": images}


@app.get("/api/admin/files/{filename}")
def admin_file(filename: str) -> FileResponse:
    # strict server-generated-name pattern -> no traversal possible
    if not _PREVIEW_RE.match(filename):
        raise HTTPException(status_code=404, detail="not found")
    path = db.images_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    media_type = _MIME_BY_EXT.get(Path(filename).suffix.lower(),
                                  "application/octet-stream")
    return FileResponse(path, media_type=media_type)


def _csv_response(filename: str, rows: list[list]) -> Response:
    buf = io.StringIO()
    writer = csv.writer(buf)  # csv module handles quoting/escaping
    writer.writerows(rows)
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/admin/export/votes.csv")
def export_votes() -> Response:
    rows: list[list] = [[
        "vote_id", "student_id", "image_id", "filename",
        "instructor_category", "student_choice", "is_correct", "created_at",
    ]]
    for r in db.export_votes_rows():
        rows.append([
            r["id"], r["student_id"], r["image_id"], r["filename"],
            r["category"], r["choice"], r["is_correct"], r["created_at"],
        ])
    return _csv_response("votes.csv", rows)


@app.get("/api/admin/export/dataset.csv")
def export_dataset() -> Response:
    categories = list(settings.categories)
    header = (["image_id", "filename", "instructor_category", "total_votes"]
              + [f"vote_{c}" for c in categories]
              + ["majority_category", "is_tie", "labeled_by", "student_accuracy"])
    rows: list[list] = [header]
    for r in db.dataset_rows():
        rows.append(
            [r["image_id"], r["filename"], r["instructor_category"], r["total_votes"]]
            + [r["counts"].get(c, 0) for c in categories]
            + [r["majority_category"], r["is_tie"], r["labeled_by"],
               r["student_accuracy"]]
        )
    return _csv_response("dataset.csv", rows)


# --------------------------------------------------------------------------
# pages + static (explicit routes first, mount last)
# --------------------------------------------------------------------------

def _page_or_placeholder(filename: str) -> Response:
    path = ADMIN_FRONTEND_DIR / filename
    if path.is_file():
        return FileResponse(path)
    # frontend not deployed yet — keep / and /dashboard navigable
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>AI Animal Lab</title></head>"
        "<body style='font-family:sans-serif;padding:2rem'>"
        "<h1>AI Animal Lab — admin</h1>"
        f"<p><code>frontend/admin/{filename}</code> is not deployed yet.</p>"
        "<p><a href='/docs'>API docs</a></p></body></html>",
        status_code=200,
    )


@app.get("/", include_in_schema=False)
async def index_page() -> Response:
    return _page_or_placeholder("index.html")


@app.get("/dashboard", include_in_schema=False)
async def dashboard_page() -> Response:
    return _page_or_placeholder("dashboard.html")


ADMIN_FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(ADMIN_FRONTEND_DIR), html=True),
          name="admin")
