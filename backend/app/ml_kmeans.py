"""Pure NumPy K-means (Lloyd) for the Unsupervised Lab — Phase 2.

Binding spec: ../../ML_CONTRACT.md ("Leak guards").

ARRAYS IN, ARRAYS OUT. This module knows NOTHING about the database, files,
filenames, categories or HTTP — the admin-app handlers pass only the
embeddings matrix. Deterministic given the same ``rng``
(``numpy.random.RandomState``) and the same input matrix.

One Lloyd iteration (``kmeans_step``) = update centroids -> re-assign:

1. UPDATE:  each centroid becomes the mean of its members (an empty cluster
   keeps its previous position).
2. ASSIGN:  each point moves to its nearest UPDATED centroid (squared
   Euclidean); ``moved_count`` = points that changed cluster in this step.

The order matters for correctness: the run state handed in already holds the
assignments of the CURRENT centroids (``ml_state.start_run`` computed them),
so an assign-first step would compare those assignments against themselves,
measure ``moved_count == 0`` on the very first step and declare a fake
convergence while the centroids had just moved away under the points' feet.
Update-first (scikit-learn's Lloyd formulation) makes ``moved_count == 0``
mean a genuine fixed point: a full update + re-assignment pass changes
nothing, so both J and the partition are at a local optimum.

With inertia measured as ``sum_i ||x_i - C_new[a_new_i]||^2`` (the returned
pairing of assignments and centroids), Lloyd's algorithm makes the sequence
monotonically non-increasing: the mean update minimizes J over centroids for
fixed assignments, and the reassignment minimizes J over assignments for the
updated centroids.
"""
from __future__ import annotations

import numpy as np

__all__ = ["kmeans_init", "kmeans_assign", "kmeans_step", "kmeans_inertia"]


def _as_matrix(X: np.ndarray) -> np.ndarray:
    X = np.asarray(X, dtype=np.float64)
    if X.ndim != 2:
        raise ValueError(f"X must be 2-D [n_points, n_features], got shape {X.shape}")
    return X


def kmeans_init(X: np.ndarray, k: int, rng: np.random.RandomState) -> np.ndarray:
    """k-means++ seeding: first center uniform, each next center sampled with
    probability proportional to the squared distance to the nearest chosen
    center. Returns float64 centroids [k, n_features]. Deterministic given rng."""
    X = _as_matrix(X)
    n = X.shape[0]
    k = int(k)
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")
    if k > n:
        raise ValueError(f"k={k} exceeds the number of points ({n})")

    centroids = np.empty((k, X.shape[1]), dtype=np.float64)
    first = int(rng.randint(n))
    centroids[0] = X[first]
    closest_sq = ((X - centroids[0]) ** 2).sum(axis=1)

    for c in range(1, k):
        total = float(closest_sq.sum())
        if total > 0.0 and np.isfinite(total):
            probs = closest_sq / total
            idx = int(rng.choice(n, p=probs))
        else:
            # all remaining points coincide with a chosen center (or are
            # degenerate) — fall back to uniform so we still return k centers
            idx = int(rng.randint(n))
        centroids[c] = X[idx]
        np.minimum(closest_sq, ((X - centroids[c]) ** 2).sum(axis=1), out=closest_sq)
    return centroids


def kmeans_assign(X: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """Index of the nearest centroid (squared Euclidean) for every point.
    Returns int64 [n_points]. Ties resolve to the lowest centroid index."""
    X = _as_matrix(X)
    C = np.asarray(centroids, dtype=np.float64)
    if C.ndim != 2 or C.shape[1] != X.shape[1]:
        raise ValueError(
            f"centroids must be [k, {X.shape[1]}], got shape {C.shape}"
        )
    # ||x||^2 - 2 x.c + ||c||^2, computed row-wise (no [N, k, D] blowup)
    d2 = (
        (X * X).sum(axis=1)[:, None]
        - 2.0 * (X @ C.T)
        + (C * C).sum(axis=1)[None, :]
    )
    return d2.argmin(axis=1).astype(np.int64)


def kmeans_step(
    X: np.ndarray, centroids: np.ndarray, assignments: np.ndarray
) -> tuple[np.ndarray, np.ndarray, int, float]:
    """One real Lloyd iteration: update centroids -> re-assign points.

    Returns ``(new_assignments, new_centroids, moved_count, inertia)`` where
    ``moved_count`` is the number of points that changed cluster during the
    re-assignment against the UPDATED centroids and
    ``inertia = sum_i ||x_i - new_centroids[new_assignments[i]]||^2``.
    """
    X = _as_matrix(X)
    C = np.asarray(centroids, dtype=np.float64)
    old = np.asarray(assignments, dtype=np.int64)
    if old.shape[0] != X.shape[0]:
        raise ValueError(
            f"assignments length {old.shape[0]} != number of points {X.shape[0]}"
        )

    # 1. UPDATE: each centroid becomes the mean of its members; an empty
    #    cluster keeps its previous position.
    k = C.shape[0]
    new_centroids = C.copy()
    for c in range(k):
        members = X[old == c]
        if members.shape[0] > 0:
            new_centroids[c] = members.mean(axis=0)

    # 2. ASSIGN: re-elect against the updated centroids, so moved_count is a
    #    real change measurement (never a self-compare of the input state).
    new_assignments = kmeans_assign(X, new_centroids)
    moved_count = int((new_assignments != old).sum())
    inertia = kmeans_inertia(X, new_centroids, new_assignments)
    return new_assignments, new_centroids, moved_count, inertia


def kmeans_inertia(
    X: np.ndarray, centroids: np.ndarray, assignments: np.ndarray
) -> float:
    """Sum of squared distances of each point to its assigned centroid."""
    X = _as_matrix(X)
    C = np.asarray(centroids, dtype=np.float64)
    a = np.asarray(assignments, dtype=np.int64)
    if X.shape[0] == 0:
        return 0.0
    diffs = X - C[a]
    return float((diffs * diffs).sum())
