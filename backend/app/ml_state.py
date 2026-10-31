"""In-memory state for the admin Unsupervised Lab — Phase 2.

Binding spec: ../../ML_CONTRACT.md ("Admin ML endpoints" + "Leak guards").

Owns exactly three things (no HTTP here — admin_app.py's handlers call in):

1. ``embed_job``  — thread-safe single-flight state for the background
   embedding job (phase / done / total / error).
2. K-means runs   — started from the embeddings.npz matrix, keyed
   ``"k<k>-s<seed>"``; one real Lloyd iteration per ``step_run`` call, max
   100 iterations. Each run state is guarded by its own lock. Deterministic
   per (k, seed) via ``numpy.random.RandomState(seed)``.
3. ``neighbors`` + ``reveal`` — cosine neighbors over the L2-normalized
   embeddings; reveal is the ONLY place in the ML stack that reads
   categories (ground truth from images.category).

numpy (+ the pure ``ml_kmeans``) are needed for the kmeans/data/neighbors
endpoints but NOT torch — those work from an existing embeddings.npz even
when the ML stack is not installed.
"""
from __future__ import annotations

import threading
from typing import Any

import numpy as np

from . import db, ml_kmeans

DEFAULT_SEED = 42
DEFAULT_NEIGHBORS = 5
MAX_ITERATIONS = 100

NO_EMBEDDINGS_MSG = "no embeddings yet — POST /api/ml/embed first"

_REVEAL_NOTE = (
    "Clustering used only the image embeddings — the category labels were "
    "never shown to the algorithm. Purity measures the correspondence "
    "between clusters and instructor labels; it is not a model accuracy."
)


class EmbedJob:
    """Thread-safe, single-flight state for the background embed job."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running = False
        self._phase = "idle"
        self._done = 0
        self._total = 0
        self._error: str | None = None

    def try_begin(self) -> bool:
        """Atomically claim the job slot; False if a job is already running."""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._phase = "loading"
            self._done = 0
            self._total = 0
            self._error = None
            return True

    def is_running(self) -> bool:
        with self._lock:
            return self._running

    def progress(self, phase: str, done: int, total: int) -> None:
        """Callback shape matches embeddings.ProgressFn."""
        with self._lock:
            self._phase = phase
            self._done = int(done)
            self._total = int(total)

    def fail(self, message: str) -> None:
        with self._lock:
            self._running = False
            self._phase = "error"
            self._error = message

    def finish(self) -> None:
        with self._lock:
            self._running = False
            self._phase = "done"

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "running": self._running,
                "phase": self._phase,
                "done": self._done,
                "total": self._total,
                "error": self._error,
            }


embed_job = EmbedJob()


# --------------------------------------------------------------------------
# embeddings store access
# --------------------------------------------------------------------------

def load_store() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(image_ids [N], embeddings [N,384], pca_components [384,2], pca [N,2]).

    Raises FileNotFoundError when DATA_DIR/embeddings.npz does not exist yet.
    """
    from . import embeddings  # lazy: keep the admin app importable sans ML

    data = embeddings.load_npz()
    if data is None or "image_ids" not in data or "embeddings" not in data:
        raise FileNotFoundError(NO_EMBEDDINGS_MSG)
    ids = np.asarray(data["image_ids"], dtype=np.int64)
    X = np.asarray(data["embeddings"], dtype=np.float64)
    components = np.asarray(
        data.get("pca_components", np.zeros((X.shape[1], 2))), dtype=np.float64
    )
    pca = np.asarray(data.get("pca", np.zeros((0, 2))), dtype=np.float64)
    return ids, X, components, pca


def _filename_map() -> dict[int, str]:
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, filename FROM images").fetchall()
    return {int(r["id"]): str(r["filename"]) for r in rows}


# --------------------------------------------------------------------------
# k-means runs
# --------------------------------------------------------------------------

class KmeansRun:
    """Mutable state of one clustering run, guarded by its own lock."""

    def __init__(self, run_id: str, k: int, seed: int, ids: np.ndarray,
                 X: np.ndarray, components: np.ndarray,
                 assignments: np.ndarray, centroids: np.ndarray,
                 inertia: float) -> None:
        self.lock = threading.Lock()
        self.run_id = run_id
        self.k = int(k)
        self.seed = int(seed)
        self.ids = ids
        self.X = X
        self.components = components
        self.center = X.mean(axis=0) if X.shape[0] else np.zeros(X.shape[1])
        self.assignments = assignments
        self.centroids = centroids
        self.inertia = float(inertia)
        self.iteration = 0
        self.changed = int(ids.shape[0])
        self.converged = False

    def snapshot_locked(self) -> dict[str, Any]:
        """Response shape per contract. Caller must hold self.lock."""
        c2d = (self.centroids - self.center) @ self.components  # [k, 2]
        return {
            "run_id": self.run_id,
            "k": self.k,
            "iteration": self.iteration,
            "assignments": [int(a) for a in self.assignments],
            "centroids_2d": [[float(x), float(y)] for x, y in c2d],
            "inertia": float(self.inertia),
            "changed": int(self.changed),
            "converged": bool(self.converged),
        }


_runs: dict[str, KmeansRun] = {}
_runs_lock = threading.Lock()


def start_run(k: int, seed: int = DEFAULT_SEED) -> dict[str, Any]:
    """k-means++ init on the 384-dim embeddings; deterministic per (k, seed).
    Replaces any previous run with the same run_id. Returns the snapshot."""
    ids, X, components, _pca = load_store()
    if X.shape[0] == 0:
        raise ValueError(NO_EMBEDDINGS_MSG)
    if X.shape[0] < k:
        raise ValueError(f"need at least k={k} embedded images, have {X.shape[0]}")

    rng = np.random.RandomState(seed)
    centroids = ml_kmeans.kmeans_init(X, k, rng)
    assignments = ml_kmeans.kmeans_assign(X, centroids)
    inertia = ml_kmeans.kmeans_inertia(X, centroids, assignments)

    run_id = f"k{k}-s{seed}"
    run = KmeansRun(run_id=run_id, k=k, seed=seed, ids=ids, X=X,
                    components=components, assignments=assignments,
                    centroids=centroids, inertia=inertia)
    with _runs_lock:
        _runs[run_id] = run
    with run.lock:
        return run.snapshot_locked()


def step_run(run_id: str) -> dict[str, Any] | None:
    """One real Lloyd iteration. None when the run_id is unknown. Stepping a
    converged (or max-100) run is a no-op that returns the current state."""
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return None
    with run.lock:
        if not run.converged:
            new_assignments, new_centroids, moved, inertia = ml_kmeans.kmeans_step(
                run.X, run.centroids, run.assignments
            )
            run.assignments = new_assignments
            run.centroids = new_centroids
            run.inertia = float(inertia)
            run.changed = int(moved)
            run.iteration += 1
            run.converged = moved == 0 or run.iteration >= MAX_ITERATIONS
        return run.snapshot_locked()


def get_run_snapshot(run_id: str) -> dict[str, Any] | None:
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return None
    with run.lock:
        return run.snapshot_locked()


# --------------------------------------------------------------------------
# neighbors + embedding lookup
# --------------------------------------------------------------------------

def neighbors(image_id: int, k: int = DEFAULT_NEIGHBORS) -> list[dict[str, Any]]:
    """Top-k cosine neighbors (embeddings are L2-normalized -> dot = cosine),
    sorted desc, excluding the query itself. KeyError if no such embedding."""
    ids, X, _components, _pca = load_store()
    hits = np.nonzero(ids == int(image_id))[0]
    if hits.size == 0:
        raise KeyError(image_id)
    i = int(hits[0])

    sims = X @ X[i]
    k = max(1, min(int(k), X.shape[0] - 1))
    order = np.argsort(-sims, kind="stable")  # ties -> lower row index first

    filenames = _filename_map()
    out: list[dict[str, Any]] = []
    for j in order:
        j = int(j)
        if j == i:
            continue
        out.append({
            "image_id": int(ids[j]),
            "filename": filenames.get(int(ids[j]), ""),
            "similarity": float(sims[j]),
        })
        if len(out) == k:
            break
    return out


def embedding_vector(image_id: int) -> tuple[int, list[float]]:
    """(384, [float]*384) for one image. KeyError if no such embedding."""
    ids, X, _components, _pca = load_store()
    hits = np.nonzero(ids == int(image_id))[0]
    if hits.size == 0:
        raise KeyError(image_id)
    return int(X.shape[1]), [float(v) for v in X[int(hits[0])]]


# --------------------------------------------------------------------------
# reveal — the ONLY place categories are read
# --------------------------------------------------------------------------

def reveal(run_id: str) -> dict[str, Any] | None:
    """Per-cluster ground-truth stats. None when the run_id is unknown."""
    with _runs_lock:
        run = _runs.get(run_id)
    if run is None:
        return None
    with run.lock:
        k = run.k
        ids = run.ids
        assignments = np.asarray(run.assignments)

    with db.get_conn() as conn:
        rows = conn.execute("SELECT id, filename, category FROM images").fetchall()
    meta = {int(r["id"]): (str(r["filename"]), str(r["category"])) for r in rows}

    clusters: list[dict[str, Any]] = []
    majority_total = 0
    members_total = 0
    for c in range(k):
        member_ids = [int(i) for i in ids[assignments == c]]
        distribution: dict[str, int] = {}
        for image_id in member_ids:
            info = meta.get(image_id)
            if info is not None:
                distribution[info[1]] = distribution.get(info[1], 0) + 1
        size = len(member_ids)
        if distribution:
            # deterministic: highest count, then alphabetical on ties
            majority = sorted(distribution.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            majority_count = int(distribution[majority])
        else:
            majority, majority_count = None, 0
        majority_total += majority_count
        members_total += size
        clusters.append({
            "cluster": c,
            "size": size,
            "distribution": distribution,
            "purity": round(majority_count / size, 4) if size else 0.0,
            "majority": majority,
            "examples": [
                {
                    "image_id": mid,
                    "filename": meta[mid][0] if mid in meta else "",
                    "category": meta[mid][1] if mid in meta else "",
                }
                for mid in member_ids[:6]
            ],
        })

    overall = majority_total / members_total if members_total else 0.0
    return {
        "clusters": clusters,
        "overall_purity": round(overall, 4),
        "note": _REVEAL_NOTE,
    }
