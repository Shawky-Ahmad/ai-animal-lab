"""PUBLIC FastAPI app: /api/* (CORS *) + the static student site at /.

Binding spec: ../API_CONTRACT.md. The student frontend is mounted at "/" with
html=True; it is added AFTER the API routes so /api/* always wins.
"""
from __future__ import annotations

import json
import random
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db
from .config import APP_NAME, STUDENT_FRONTEND_DIR, STUDENT_ID_RE, settings

app = FastAPI(title=APP_NAME + " — Public")

# Public API: any origin, no credentials (contract).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STUDENT_ID_RE = re.compile(STUDENT_ID_RE)

_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class SessionIn(BaseModel):
    student_id: str


class VoteIn(BaseModel):
    student_id: str
    image_id: int
    choice: str


def _require_student_id(student_id: str) -> str:
    if not _STUDENT_ID_RE.match(student_id or ""):
        raise HTTPException(status_code=400, detail="invalid student_id")
    return student_id


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/config")
def get_config() -> dict:
    return {
        "app_name": APP_NAME,
        "categories": list(settings.categories),
        "target": settings.target_per_student,
        "image_count": db.count_images(),
    }


@app.post("/api/session")
def create_session(body: SessionIn) -> dict:
    sid = _require_student_id(body.student_id)
    with db.get_conn() as conn:
        student = db.upsert_student(sid, conn)
        labeled = db.count_labeled(sid, conn)
    target = int(student["target"])
    return {
        "student_id": sid,
        "target": target,
        "labeled": labeled,
        "done": labeled >= target,
    }


@app.get("/api/next")
def next_image(student_id: str = Query(...)) -> dict:
    sid = _require_student_id(student_id)
    with db.get_conn() as conn:
        student = db.upsert_student(sid, conn)
        labeled = db.count_labeled(sid, conn)
        target = int(student["target"])
        if labeled >= target:
            return {"done": True, "labeled": labeled, "target": target}
        row = db.pick_next_image(sid, conn)
    if row is None:
        return {"done": True, "labeled": labeled, "target": target}
    choices = json.loads(row["choices"])  # stored triple, re-shuffled per response
    random.shuffle(choices)
    return {
        "done": False,
        "image_id": int(row["id"]),
        "filename": row["filename"],
        "choices": choices,
        "labeled": labeled,
        "target": target,
    }


@app.post("/api/vote")
def vote(body: VoteIn) -> dict:
    sid = _require_student_id(body.student_id)
    with db.get_conn() as conn:
        image = db.get_image(body.image_id, conn)
        if image is None:
            raise HTTPException(status_code=400, detail="unknown image")
        choices = json.loads(image["choices"])
        if body.choice not in choices:
            raise HTTPException(status_code=400, detail="choice not in image choices")
        # is_correct computed SERVER-side only; INSERT OR IGNORE keeps one vote
        # per student per image forever.
        duplicate = not db.insert_vote(
            sid, int(image["id"]), body.choice,
            body.choice == image["category"], conn,
        )
        student = db.upsert_student(sid, conn)
        labeled = db.count_labeled(sid, conn)
    return {
        "ok": True,
        "duplicate": duplicate,
        "labeled": labeled,
        "target": int(student["target"]),
    }


@app.get("/api/progress")
def progress(student_id: str = Query(...)) -> dict:
    sid = _require_student_id(student_id)
    with db.get_conn() as conn:
        student = db.upsert_student(sid, conn)
        labeled = db.count_labeled(sid, conn)
    target = int(student["target"])
    return {"labeled": labeled, "target": target, "done": labeled >= target}


@app.get("/api/images/{image_id}/file")
def image_file(image_id: int) -> FileResponse:
    # Filename is looked up BY ID in the DB — client paths never touch disk.
    image = db.get_image(image_id)
    if image is None:
        raise HTTPException(status_code=404, detail="image not found")
    path = db.images_dir() / image["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="image file missing")
    media_type = _MIME_BY_EXT.get(Path(image["filename"]).suffix.lower(),
                                  "application/octet-stream")
    return FileResponse(path, media_type=media_type)


@app.get("/api/stats/summary")
def stats_summary() -> dict:
    return db.build_summary()


# Static student site last, so every /api/* route above matches first.
STUDENT_FRONTEND_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/", StaticFiles(directory=str(STUDENT_FRONTEND_DIR), html=True),
          name="student")
