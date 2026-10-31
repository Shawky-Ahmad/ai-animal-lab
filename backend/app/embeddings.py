"""DINOv2 image embeddings + PCA cache for the Unsupervised Lab — Phase 2.

Binding spec: ../../ML_CONTRACT.md ("Embeddings store").

Builds/refreshes ``DATA_DIR/embeddings.npz``:

- ``image_ids``      int64   [N]      stable ids from the images table
- ``embeddings``     float32 [N, 384] DINOv2-small CLS token, L2-normalized
- ``file_hashes``    str     [N]      sha256 hex of the image bytes on disk
- ``pca_mean``       float32 [2]      mean of the cached 2D projections
- ``pca_components`` float32 [384, 2] top-2 right singular vectors (SVD PCA)
- ``pca``            float32 [N, 2]   cached projections of the centered data

torch / transformers / PIL are imported LAZILY inside functions so the admin
app boots fine without the ML stack (it reports ``ml_available: false``).
numpy + the app's own db module are always safe to import.

Model: ``facebook/dinov2-small`` via transformers — downloaded once, then
loaded from the local cache. GPU when ``torch.cuda.is_available()`` else CPU.
Batched inference under ``torch.no_grad()`` with the model in eval mode.
Change detection is sha256 over the image bytes: only new/changed files are
re-embedded; ids deleted from the images table are dropped from the store.

CLI (run from the backend/ directory):
    python -m app.embeddings
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Callable

import numpy as np

from . import db

MODEL_NAME = "facebook/dinov2-small"
EMBED_DIM = 384
BATCH_SIZE = 16
HASH_CHUNK = 1024 * 1024

# progress callback: (phase, done, total) with phase in
# "loading" | "embedding" | "pca" | "saving" | "done"
ProgressFn = Callable[[str, int, int], None]

# in-process model cache (loaded once per process; ~90 MB resident)
_model_cache: tuple | None = None


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------

def model_loaded() -> bool:
    """True once facebook/dinov2-small has been loaded in this process."""
    return _model_cache is not None


def _get_model():
    """Lazy-load the model (GPU when torch sees one, else CPU)."""
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    import torch
    from transformers import AutoImageProcessor, AutoModel

    device = "cuda" if torch.cuda.is_available() else "cpu"
    try:
        processor = AutoImageProcessor.from_pretrained(MODEL_NAME, use_fast=True)
    except TypeError:  # older transformers without the use_fast kwarg
        processor = AutoImageProcessor.from_pretrained(MODEL_NAME)
    model = AutoModel.from_pretrained(MODEL_NAME)
    model.to(device)
    model.eval()
    _model_cache = (processor, model, device)
    return _model_cache


# --------------------------------------------------------------------------
# files + hashing
# --------------------------------------------------------------------------

def npz_path() -> Path:
    """DATA_DIR/embeddings.npz."""
    return db.get_data_dir() / "embeddings.npz"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_db_images() -> list[tuple[int, str, Path]]:
    """(image_id, filename, absolute path) for every row, ordered by id."""
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, filename FROM images ORDER BY id").fetchall()
    return [
        (int(r["id"]), str(r["filename"]), db.images_dir() / str(r["filename"]))
        for r in rows
    ]


# --------------------------------------------------------------------------
# npz store
# --------------------------------------------------------------------------

def load_npz() -> dict | None:
    """Load embeddings.npz as {name: array}, or None if missing/corrupt."""
    path = npz_path()
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            return {name: data[name] for name in data.files}
    except Exception:
        return None  # corrupt store -> treat as absent, rebuild from scratch


def fit_pca(embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(pca_mean [2], pca_components [384, 2], pca [N, 2]) via numpy SVD
    on the centered embeddings. Contract fixes pca_mean at shape [2]; it
    stores the mean of the cached 2D projections (numerically ~[0, 0])."""
    X = np.asarray(embeddings, dtype=np.float64)
    if X.shape[0] == 0:
        return (
            np.zeros(2, dtype=np.float32),
            np.zeros((EMBED_DIM, 2), dtype=np.float32),
            np.zeros((0, 2), dtype=np.float32),
        )
    Xc = X - X.mean(axis=0)
    _u, _s, vt = np.linalg.svd(Xc, full_matrices=False)
    components = np.ascontiguousarray(vt[:2].T, dtype=np.float32)   # [384, 2]
    pca = (Xc @ vt[:2].T).astype(np.float32)                        # [N, 2]
    pca_mean = pca.mean(axis=0).astype(np.float32)                  # [2]
    return pca_mean, components, pca


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

def embed_image_files(
    paths: list[Path], progress: ProgressFn | None = None
) -> np.ndarray:
    """CLS-token embeddings, L2-normalized — float32 [len(paths), 384]."""
    if not paths:
        return np.zeros((0, EMBED_DIM), dtype=np.float32)
    import torch
    from PIL import Image
    from torch.nn import functional as F

    processor, model, device = _get_model()
    out = np.zeros((len(paths), EMBED_DIM), dtype=np.float32)
    total = len(paths)
    with torch.no_grad():
        for start in range(0, total, BATCH_SIZE):
            stop = min(start + BATCH_SIZE, total)
            images = [Image.open(p).convert("RGB") for p in paths[start:stop]]
            inputs = processor(images=images, return_tensors="pt")
            inputs = {key: value.to(device) for key, value in inputs.items()}
            features = model(**inputs).last_hidden_state[:, 0, :]  # CLS token
            features = F.normalize(features, p=2, dim=1)
            out[start:stop] = features.to("cpu").numpy().astype(np.float32)
            if progress is not None:
                progress("embedding", stop, total)
    return out


# --------------------------------------------------------------------------
# one full sync pass: images table -> embeddings.npz
# --------------------------------------------------------------------------

def run(progress: ProgressFn | None = None) -> dict:
    """Sync DATA_DIR/embeddings.npz with the images table.

    Idempotent: only new/changed files are embedded (sha256 detection),
    deleted ids are dropped. Returns a stats dict.
    """
    def report(phase: str, done: int, total: int) -> None:
        if progress is not None:
            progress(phase, done, total)

    db_images = list_db_images()
    db_hashes: dict[int, str | None] = {
        image_id: (sha256_file(path) if path.is_file() else None)
        for image_id, _filename, path in db_images
    }

    existing = load_npz()
    kept: dict[int, tuple[str, np.ndarray]] = {}
    dropped = 0
    if existing is not None and "image_ids" in existing and "embeddings" in existing:
        old_ids = existing["image_ids"]
        old_emb = np.asarray(existing["embeddings"])
        old_hashes = existing.get("file_hashes")
        for row in range(old_emb.shape[0]):
            image_id = int(old_ids[row])
            current_hash = db_hashes.get(image_id)
            if current_hash is None:
                dropped += 1  # deleted from db, or file vanished from disk
                continue
            old_hash = (
                str(old_hashes[row])
                if old_hashes is not None and row < len(old_hashes)
                else None
            )
            if old_hash == current_hash:
                kept[image_id] = (current_hash, old_emb[row].astype(np.float32))
            else:
                dropped += 1  # changed on disk -> re-embed

    to_embed = [
        (image_id, filename, path)
        for image_id, filename, path in db_images
        if image_id not in kept and db_hashes[image_id] is not None
    ]
    unreadable = sum(1 for i in db_hashes if db_hashes[i] is None)

    if to_embed:
        report("loading", 0, 1)
        vectors = embed_image_files([p for _i, _f, p in to_embed], progress=report)
        for (image_id, _filename, _path), vec in zip(to_embed, vectors):
            kept[image_id] = (str(db_hashes[image_id]), vec)

    ordered_ids = sorted(kept)
    matrix = (
        np.stack([kept[i][1] for i in ordered_ids]).astype(np.float32)
        if ordered_ids
        else np.zeros((0, EMBED_DIM), dtype=np.float32)
    )
    hash_array = np.array([kept[i][0] for i in ordered_ids], dtype=np.str_)

    # write only when something actually changed (or the PCA payload is stale)
    pca_ok = (
        existing is not None
        and "pca" in existing
        and "pca_mean" in existing
        and "pca_components" in existing
        and np.asarray(existing["pca"]).shape == (len(ordered_ids), 2)
    )
    if to_embed or dropped > 0 or existing is None or not pca_ok:
        report("pca", 0, 1)
        pca_mean, components, pca = fit_pca(matrix)
        report("saving", 0, 1)
        npz_path().parent.mkdir(parents=True, exist_ok=True)
        np.savez(
            npz_path(),
            image_ids=np.array(ordered_ids, dtype=np.int64),
            embeddings=matrix,
            file_hashes=hash_array,
            pca_mean=pca_mean,
            pca_components=components,
            pca=pca,
        )
    report("done", len(ordered_ids), len(ordered_ids))

    return {
        "images": len(db_images),
        "embedded": len(to_embed),
        "reused": len(kept) - len(to_embed),
        "dropped": dropped,
        "unreadable": unreadable,
        "total": len(ordered_ids),
        "path": str(npz_path()),
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _cli_progress(phase: str, done: int, total: int) -> None:
    print(f"[{phase}] {done}/{total}", file=sys.stderr, flush=True)


def main(_argv: list[str] | None = None) -> int:
    stats = run(_cli_progress)
    print(
        f"images in db : {stats['images']}"
        f" (unreadable files: {stats['unreadable']})"
    )
    print(f"embedded now : {stats['embedded']}")
    print(f"reused       : {stats['reused']}")
    print(f"dropped      : {stats['dropped']}")
    print(f"stored total : {stats['total']}  ->  {stats['path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
