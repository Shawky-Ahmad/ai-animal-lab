"""ADMIN FastAPI app: upload console (/), dashboard (/dashboard), /api/admin/*.

Local-only (bound to 127.0.0.1 by run.py), same-origin, no CORS needed.
Whoever runs the laptop (instructor or a helper with their own clone) is the
only one who can reach it. Binding spec: ../API_CONTRACT.md.
"""
from __future__ import annotations

import csv
import io
import json
import re
import shutil
import threading
import uuid
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               Response)
from fastapi.staticfiles import StaticFiles

from . import db
from .config import ADMIN_FRONTEND_DIR, IMAGE_EXTENSIONS, settings

app = FastAPI(title="AI Animal Lab — Admin")

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
# Unsupervised Lab (Phase 2) — page + /api/ml/*
# Binding spec: ../../ML_CONTRACT.md. The ML modules (and through them
# torch/transformers) are imported LAZILY inside the handlers so this app
# boots fine without the ML stack: /api/ml/status then reports
# ml_available=false and POST /api/ml/embed answers 503, while the
# kmeans/data/neighbors endpoints still work from an existing
# embeddings.npz (numpy only).
# --------------------------------------------------------------------------


def _ml_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except Exception:
        return False
    return True


@app.get("/unsupervised", include_in_schema=False)
async def unsupervised_page() -> Response:
    return _page_or_placeholder("unsupervised.html")


@app.get("/api/ml/status")
def ml_status() -> dict:
    import numpy as np

    from . import embeddings, ml_state

    data = embeddings.load_npz()
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, filename FROM images ORDER BY id").fetchall()
    db_ids = {int(r["id"]) for r in rows}
    if data is None or "image_ids" not in data:
        count, stale = 0, len(db_ids)
    else:
        npz_ids = {int(i) for i in data["image_ids"]}
        count = len(npz_ids)
        # stale = ids missing from the store + dead ids + files whose bytes
        # changed since they were embedded (same sha256 check the job uses)
        stale = len(npz_ids - db_ids) + len(db_ids - npz_ids)
        if count and stale == 0:
            hashes = np.asarray(data.get("file_hashes", []))
            stored = {int(i): str(hashes[n]) for n, i in enumerate(data["image_ids"])}
            for r in rows:
                image_id = int(r["id"])
                old = stored.get(image_id)
                if old is None:
                    continue
                path = db.images_dir() / str(r["filename"])
                if not path.is_file() or embeddings.sha256_file(path) != old:
                    stale += 1
    return {
        "ml_available": _ml_available(),
        "model_loaded": embeddings.model_loaded(),
        "embeddings": {"count": count, "images": len(db_ids), "stale": stale},
        "job": ml_state.embed_job.snapshot(),
    }


def _embed_worker() -> None:
    from . import embeddings, ml_state

    try:
        embeddings.run(progress=ml_state.embed_job.progress)
        ml_state.embed_job.finish()
    except Exception as exc:  # surfaced through /api/ml/status
        ml_state.embed_job.fail(f"{type(exc).__name__}: {exc}")


@app.post("/api/ml/embed")
def ml_embed() -> Response:
    from . import ml_state

    if not _ml_available():
        raise HTTPException(
            status_code=503,
            detail="ML stack not installed — run scripts/setup-ml.sh "
                   "(installs CPU torch + transformers into backend/.venv)",
        )
    if not ml_state.embed_job.try_begin():
        return JSONResponse(status_code=409, content={"running": True})
    threading.Thread(target=_embed_worker, name="ml-embed", daemon=True).start()
    return {"started": True}


def _ml_filenames() -> dict[int, str]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, filename FROM images").fetchall()
    return {int(r["id"]): str(r["filename"]) for r in rows}


@app.get("/api/ml/data")
def ml_data() -> dict:
    import numpy as np

    from . import embeddings

    data = embeddings.load_npz()
    if data is None or "image_ids" not in data or "pca" not in data:
        return {"ids": [], "filenames": [], "pca": [], "dim": embeddings.EMBED_DIM}
    # leak guard: NO category / original_name fields in this payload
    ids = [int(i) for i in data["image_ids"]]
    filenames = _ml_filenames()
    pca = np.asarray(data["pca"])
    return {
        "ids": ids,
        "filenames": [filenames.get(i, "") for i in ids],
        "pca": [[float(x), float(y)] for x, y in pca],
        "dim": int(embeddings.EMBED_DIM),
    }


def _no_embeddings_503() -> HTTPException:
    return HTTPException(
        status_code=503, detail="no embeddings yet — POST /api/ml/embed first"
    )


@app.post("/api/ml/kmeans")
def ml_kmeans_start(payload: dict = Body(default={})) -> dict:
    from . import ml_state

    k = payload.get("k")
    seed = payload.get("seed", ml_state.DEFAULT_SEED)
    if isinstance(k, bool) or not isinstance(k, int) or not 2 <= k <= 8:
        raise HTTPException(status_code=400,
                            detail="k must be an integer in 2..8")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise HTTPException(status_code=400, detail="seed must be an integer")
    try:
        return ml_state.start_run(k, seed)
    except FileNotFoundError:
        raise _no_embeddings_503() from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.post("/api/ml/kmeans/step")
def ml_kmeans_step(payload: dict = Body(default={})) -> dict:
    from . import ml_state

    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise HTTPException(status_code=400, detail="run_id must be a string")
    snapshot = ml_state.step_run(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return snapshot


@app.get("/api/ml/neighbors/{image_id}")
def ml_neighbors(image_id: int, k: int = 5) -> dict:
    from . import ml_state

    try:
        return {"image_id": image_id,
                "neighbors": ml_state.neighbors(image_id, k)}
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"image {image_id} has no embedding",
        ) from None
    except FileNotFoundError:
        raise _no_embeddings_503() from None


@app.get("/api/ml/embedding/{image_id}")
def ml_embedding(image_id: int) -> dict:
    from . import ml_state

    try:
        dim, vector = ml_state.embedding_vector(image_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"image {image_id} has no embedding",
        ) from None
    except FileNotFoundError:
        raise _no_embeddings_503() from None
    return {"image_id": image_id, "dim": dim, "vector": vector}


@app.get("/api/ml/reveal/{run_id}")
def ml_reveal(run_id: str) -> dict:
    from . import ml_state

    result = ml_state.reveal(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown run '{run_id}'")
    return result


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
