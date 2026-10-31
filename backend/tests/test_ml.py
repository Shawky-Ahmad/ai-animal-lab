#!/usr/bin/env python3
"""AI Animal Lab — Phase 2 (Unsupervised Lab) independent QA suite.

Adversarial integration tests for the ML backend. Launches a PRIVATE server
instance (fresh temp data dir, ports 8870/8871, --no-seed), seeds its own
placeholder images, builds REAL DINOv2 embeddings via `python -m app.embeddings`,
then challenges the two binding risks of ML_CONTRACT.md:

  1. LABEL LEAKAGE   — the clustering must produce IDENTICAL assignments when
                       every images.category value is rewritten in SQLite.
  2. VISUALIZATION
     HONESTY         — iterations are real Lloyd steps (real moved-counts,
                       inertia non-increasing, converged step is a no-op at a
                       genuine fixed point), and neighbors/reveal numbers match
                       independent recomputation from embeddings.npz.

Run:  backend/tests $  ../.venv/bin/python test_ml.py
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
PYTHON = BACKEND / ".venv" / "bin/python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

PUBLIC_PORT, ADMIN_PORT = 8870, 8871
PUB = f"http://127.0.0.1:{PUBLIC_PORT}"
ADM = f"http://127.0.0.1:{ADMIN_PORT}"
DATA_DIR = Path(f"/tmp/al-mlqa-{int(time.time())}")

sys.path.insert(0, str(BACKEND))  # so the test can import app.ml_kmeans

K, SEED = 5, 42
SAMPLE_K = 5
UUID_NAME_RE = re.compile(r"^[0-9a-f]{32}\.(jpg|jpeg|png|webp)$")

results: list[tuple[str, bool, str]] = []


def check(section: str, name: str, ok: bool, detail: str = "") -> bool:
    results.append((f"{section} · {name}", ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DIR / "animal_lab.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def jpg_bytes(w: int = 320, h: int = 240, color=(120, 200, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def run_cli(module: str, timeout: float = 540.0) -> subprocess.CompletedProcess:
    env = {**os.environ, "ANIMAL_LAB_DATA": str(DATA_DIR)}
    return subprocess.run(
        [str(PYTHON), "-m", module], cwd=str(BACKEND), env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def start_kmeans(k: int, seed: int = SEED) -> dict:
    r = requests.post(f"{ADM}/api/ml/kmeans",
                      json={"k": k, "seed": seed}, timeout=30)
    assert r.status_code == 200, f"kmeans start k={k} -> HTTP {r.status_code}: {r.text}"
    return r.json()


def step(run_id: str) -> dict:
    r = requests.post(f"{ADM}/api/ml/kmeans/step",
                      json={"run_id": run_id}, timeout=30)
    assert r.status_code == 200, f"step {run_id} -> HTTP {r.status_code}: {r.text}"
    return r.json()


def run_to_convergence(run_id: str) -> tuple[list[dict], dict]:
    """Step until converged; returns (snapshots, final)."""
    snaps = []
    for _ in range(120):
        snap = step(run_id)
        snaps.append(snap)
        if snap["converged"]:
            break
    return snaps, snaps[-1]


def co_membership(assignments: list[int]) -> float:
    """Fraction of unordered point pairs that share a cluster."""
    n = len(assignments)
    same = sum(
        1 for i in range(n) for j in range(i + 1, n)
        if assignments[i] == assignments[j])
    return same / (n * (n - 1) / 2)


def first_diff(a: list[int], b: list[int]):
    for i, (x, y) in enumerate(zip(a, b)):
        if x != y:
            return i
    return None if a == b else "length"


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def test_m1_embeddings_store():
    print("\n=== M1 — Embeddings store (real DINOv2 via CLI) ===")
    proc = run_cli("app.embeddings")
    check("M1", "python -m app.embeddings exits 0", proc.returncode == 0,
          proc.stderr.strip()[-300:] if proc.returncode else "")

    with db_connect() as conn:
        db_rows = conn.execute(
            "SELECT id, filename FROM images ORDER BY id").fetchall()
    db_ids = [int(r["id"]) for r in db_rows]

    data = np.load(DATA_DIR / "embeddings.npz", allow_pickle=False)
    ids = data["image_ids"]
    emb = data["embeddings"]
    hashes = data["file_hashes"]

    check("M1", "npz image_ids == db ids (same order)",
          [int(i) for i in ids] == db_ids,
          f"npz={len(ids)} db={len(db_ids)}")
    check("M1", f"embeddings shape [N,384] (N={len(db_ids)})",
          emb.shape == (len(db_ids), 384), f"shape={tuple(emb.shape)} dtype={emb.dtype}")
    norms = np.linalg.norm(emb.astype(np.float64), axis=1)
    check("M1", "L2 norms ~= 1 for every row",
          bool(np.all(np.abs(norms - 1.0) < 2e-3)),
          f"min={norms.min():.6f} max={norms.max():.6f}")
    check("M1", "all values finite", bool(np.all(np.isfinite(emb))))

    fname_by_id = {int(r["id"]): str(r["filename"]) for r in db_rows}
    bad = sum(
        1 for row, image_id in enumerate(db_ids)
        if hashlib.sha256(
            (DATA_DIR / "images" / fname_by_id[image_id]).read_bytes()).hexdigest()
        != str(hashes[row]))
    check("M1", "file_hashes match recomputed sha256 of every image",
          bad == 0, f"mismatches={bad}")

    proc2 = run_cli("app.embeddings")
    check("M1", "re-run embeds 0 new (idempotent)",
          proc2.returncode == 0 and "embedded now : 0" in proc2.stdout,
          proc2.stdout.strip().replace("\n", " | ")[:200])
    return ids, emb.astype(np.float64)


def test_m2_leak_guards_static():
    print("\n=== M2 — Leak guards: source + /api/ml/data payload ===")
    src = (BACKEND / "app" / "ml_kmeans.py").read_text(encoding="utf-8").lower()
    for banned in ("category", "db", "sqlite"):
        check("M2", f"ml_kmeans.py source contains no '{banned}'",
              banned not in src,
              f"found at offset {src.find(banned)}" if banned in src else "")

    payload = requests.get(f"{ADM}/api/ml/data", timeout=10).json()
    check("M2", "/api/ml/data keys are exactly ids/filenames/pca/dim",
          set(payload.keys()) == {"ids", "filenames", "pca", "dim"},
          str(sorted(payload.keys())))
    blob = json.dumps(payload).lower()
    check("M2", "payload has no 'category' or 'original_name' anywhere",
          "category" not in blob and "original_name" not in blob, "")
    names = payload["filenames"]
    check("M2", "filenames are server uuid names, never original names",
          all(UUID_NAME_RE.match(f) for f in names),
          f"example={names[0] if names else None}")
    check("M2", "pca rows align with ids (one [x,y] per image, dim 384)",
          len(payload["pca"]) == len(payload["ids"])
          and all(len(p) == 2 for p in payload["pca"])
          and payload["dim"] == 384, f"n={len(payload['pca'])}")


def independent_lloyd(X: np.ndarray, k: int, seed: int, max_iter: int = 100):
    """Re-implementation of the Lloyd loop from the contract (update -> assign),
    independent of app.ml_kmeans.kmeans_step. Returns (a0, inertia0, trajectory,
    a_final, inertia_final, n_iterations)."""
    from app.ml_kmeans import kmeans_init  # init seeding only; steps are ours

    rng = np.random.RandomState(seed)
    C = kmeans_init(X, k, rng)
    a = np.argmin(
        (X * X).sum(axis=1)[:, None] - 2.0 * (X @ C.T) + (C * C).sum(axis=1)[None, :],
        axis=1).astype(np.int64)
    a0 = a.copy()
    inertia0 = float(((X - C[a]) ** 2).sum())
    trajectory = []
    prev = a0
    for t in range(1, max_iter + 1):
        newC = C.copy()
        for c in range(k):
            members = X[prev == c]
            if members.shape[0] > 0:
                newC[c] = members.mean(axis=0)
        a = np.argmin(
            (X * X).sum(axis=1)[:, None] - 2.0 * (X @ newC.T)
            + (newC * newC).sum(axis=1)[None, :], axis=1).astype(np.int64)
        moved = int((a != prev).sum())
        inertia = float(((X - newC[a]) ** 2).sum())
        trajectory.append((t, a.copy(), moved, inertia))
        C, prev = newC, a
        if moved == 0:
            break
    return a0, inertia0, trajectory, a.copy(), float(((X - C[a]) ** 2).sum()), len(trajectory)


def test_m3_lloyd_iterations(ids, X):
    print("\n=== M3 — K-means: real Lloyd iterations (k=5, seed=42) ===")
    n = len(requests.get(f"{ADM}/api/ml/data", timeout=10).json()["ids"])
    snap0 = start_kmeans(K, SEED)
    ok0 = (snap0["run_id"] == f"k{K}-s{SEED}" and snap0["iteration"] == 0
           and snap0["changed"] == n and snap0["converged"] is False
           and len(snap0["assignments"]) == n
           and len(snap0["centroids_2d"]) == K
           and all(0 <= a < K for a in snap0["assignments"]))
    check("M3", "start: iteration 0, changed=N, k centroids, valid labels", ok0,
          f"k={snap0['k']} changed={snap0['changed']} iter={snap0['iteration']}")

    snaps, final = run_to_convergence(snap0["run_id"])
    inertias = [snap0["inertia"]] + [s["inertia"] for s in snaps]
    drops = [b <= a + 1e-6 for a, b in zip(inertias, inertias[1:])]
    check("M3", f"inertia non-increasing at every one of {len(snaps)} steps",
          all(drops), f"{[round(v, 4) for v in inertias]}")
    check("M3", "assignments eventually stable (converged, changed=0)",
          final["converged"] is True and final["changed"] == 0,
          f"iterations={final['iteration']} changed={final['changed']}")

    noop = step(snap0["run_id"])
    check("M3", "stepping a converged run is a no-op (changed=0, same state)",
          noop["changed"] == 0 and noop["iteration"] == final["iteration"]
          and noop["assignments"] == final["assignments"]
          and noop["inertia"] == final["inertia"],
          f"iter {final['iteration']} -> {noop['iteration']}")

    # The first step must do REAL work: either points move, or the centroid
    # mean-update strictly lowers inertia. A first step with moved=0 AND
    # unchanged inertia would mean the server reported a state without
    # iterating at all.
    check("M3", "first step did real Lloyd work (points moved or inertia fell)",
          snaps and (snaps[0]["changed"] > 0
                     or snaps[0]["inertia"] < snap0["inertia"]),
          f"iter0 inertia={snap0['inertia']:.4f} -> step1 "
          f"inertia={snaps[0]['inertia']:.4f}, moved={snaps[0]['changed']}")

    # HONESTY (QA-1 regression guard): a "converged" claim must be a genuine
    # Lloyd fixed point — re-assigning against the mean-updated centroids of
    # the final assignment must change NOTHING, recomputed here from the npz.
    fa = np.asarray(final["assignments"], dtype=np.int64)
    k = final["k"]
    means = np.zeros((k, X.shape[1]), dtype=np.float64)
    nonempty = 0
    for c in range(k):
        members = X[fa == c]
        if members.shape[0]:
            means[c] = members.mean(axis=0)
            nonempty += 1
        else:  # empty cluster keeps its previous position; final centroids are
            means[c] = np.nan  # not exposed, so flag it instead of guessing
    if not np.isnan(means).any():
        d2 = ((X * X).sum(axis=1)[:, None] - 2.0 * (X @ means.T)
              + (means * means).sum(axis=1)[None, :])
        would = np.argmin(d2, axis=1).astype(np.int64)
        check("M3", "converged state is a genuine fixed point "
                    f"({nonempty}/{k} clusters, recomputed from npz)",
              np.array_equal(would, fa),
              f"{int((would != fa).sum())} points would move at 'converged' state")
    else:
        check("M3", "converged state is a genuine fixed point (recomputed)",
              False, "empty cluster in final assignment — cannot recompute")

    # Full-trajectory replication: the API's iteration-0 and converged states
    # must match an independent Lloyd implementation run on the same npz.
    a0_ref, inertia0_ref, _traj, a_final_ref, inertia_ref, n_iter = \
        independent_lloyd(X, K, SEED)
    check("M3", "iteration-0 assignments + inertia match independent replication",
          snap0["assignments"] == [int(v) for v in a0_ref]
          and abs(snap0["inertia"] - inertia0_ref) < 1e-9,
          f"inertia api={snap0['inertia']:.6f} ref={inertia0_ref:.6f}")
    check("M3", "converged assignments match independent replication",
          final["assignments"] == [int(v) for v in a_final_ref],
          f"api iters={final['iteration']} ref iters={n_iter}, first diff at "
          f"{first_diff(final['assignments'], [int(v) for v in a_final_ref])}")
    check("M3", "converged inertia + step count match independent replication",
          abs(final["inertia"] - inertia_ref) < 1e-9
          and final["iteration"] == n_iter,
          f"inertia api={final['inertia']:.6f} ref={inertia_ref:.6f}; "
          f"steps api={final['iteration']} ref={n_iter}")
    return snap0, final


def test_m4_determinism(a0: dict, af: dict):
    print("\n=== M4 — Determinism: same (k, seed) from scratch twice ===")
    b0 = start_kmeans(K, SEED)
    check("M4", "iteration-0 assignments identical on restart",
          b0["assignments"] == a0["assignments"],
          f"first diff at {first_diff(a0['assignments'], b0['assignments'])}")
    check("M4", "iteration-0 inertia identical", b0["inertia"] == a0["inertia"],
          f"{a0['inertia']} vs {b0['inertia']}")
    _snaps, bf = run_to_convergence(b0["run_id"])
    check("M4", "converged assignments identical on restart",
          bf["assignments"] == af["assignments"],
          f"iters {af['iteration']} vs {bf['iteration']}, "
          f"first diff at {first_diff(af['assignments'], bf['assignments'])}")


def test_m5_different_k():
    print("\n=== M5 — Different K gives a genuinely different partition ===")
    s5 = start_kmeans(K, SEED)
    _snaps, f5 = run_to_convergence(s5["run_id"])
    s2 = start_kmeans(2, SEED)
    _snaps2, f2 = run_to_convergence(s2["run_id"])

    sizes5 = sorted([f5["assignments"].count(c) for c in range(K)])
    sizes2 = sorted([f2["assignments"].count(c) for c in range(2)])
    check("M5", "k=2 and k=5 both produce full partitions",
          len(set(f5["assignments"])) == K and len(set(f2["assignments"])) == 2,
          f"sizes5={sizes5} sizes2={sizes2}")
    check("M5", "partition sizes genuinely differ (not a relabeling)",
          sizes2 != sizes5, f"sizes2={sizes2} vs sizes5={sizes5}")
    cm5, cm2 = co_membership(f5["assignments"]), co_membership(f2["assignments"])
    check("M5", "pair co-membership differs substantially between k=2 and k=5",
          abs(cm5 - cm2) > 0.05, f"k2={cm2:.3f} k5={cm5:.3f}")
    check("M5", "k=2 is not the trivial single-cluster or singleton partition",
          all(s > 1 for s in sizes2) and len(sizes2) == 2, f"sizes2={sizes2}")


def test_m6_neighbors(ids, X):
    print("\n=== M6 — Neighbors: independent cosine recomputation ===")
    samples = [int(ids[0]), int(ids[len(ids) // 2]), int(ids[-1])]
    for image_id in samples:
        i = int(np.nonzero(ids == image_id)[0][0])
        sims = X @ X[i]
        order = np.argsort(-sims, kind="stable")
        expected = [int(ids[j]) for j in order if int(j) != i][:SAMPLE_K]
        r = requests.get(f"{ADM}/api/ml/neighbors/{image_id}?k={SAMPLE_K}",
                         timeout=10).json()
        got = [nb["image_id"] for nb in r["neighbors"]]
        sims_got = [nb["similarity"] for nb in r["neighbors"]]
        check("M6", f"image {image_id}: top-{SAMPLE_K} cosine ids exact match",
              got == expected and len(got) == SAMPLE_K,
              f"api={got} npz={expected}")
        idx_ok = all(
            abs(s - float(sims[int(np.nonzero(ids == g)[0][0])])) < 1e-9
            for s, g in zip(sims_got, got))
        check("M6", f"image {image_id}: sims sorted desc, exact, self excluded",
              all(a >= b - 1e-12 for a, b in zip(sims_got, sims_got[1:]))
              and image_id not in got and idx_ok,
              f"sims={[round(s, 4) for s in sims_got]}")


def test_m7_reveal(run_id: str):
    print("\n=== M7 — Reveal stats recomputed independently ===")
    r = requests.get(f"{ADM}/api/ml/reveal/{run_id}", timeout=10)
    check("M7", "reveal endpoint 200 for a live run", r.status_code == 200)
    rev = r.json()
    clusters = rev["clusters"]
    ok_sum = all(sum(cl["distribution"].values()) == cl["size"] for cl in clusters)
    check("M7", "each distribution sums to its cluster size", ok_sum,
          str([(cl["cluster"], cl["size"], cl["distribution"]) for cl in clusters[:2]]))
    ok_purity = all(
        0.0 <= cl["purity"] <= 1.0
        and cl["purity"] == round((max(cl["distribution"].values())
                                   / cl["size"]) if cl["size"] else 0.0, 4)
        for cl in clusters)
    check("M7", "per-cluster purity in [0,1] and equals majority share", ok_purity,
          str([cl["purity"] for cl in clusters]))
    overall = round(sum(max(cl["distribution"].values()) for cl in clusters)
                    / sum(cl["size"] for cl in clusters), 4)
    check("M7", "overall_purity == size-weighted mean recomputed in test",
          rev["overall_purity"] == overall,
          f"api={rev['overall_purity']} recomputed={overall}")
    check("M7", "reveal note disclaims 'accuracy' interpretation",
          "never" in rev["note"].lower() and "accuracy" in rev["note"].lower(),
          rev["note"][:90])
    return rev


def test_m8_label_leakage(a0: dict, af: dict, rev_before: dict):
    print("\n=== M8 — LEAKAGE: rewrite every category, clustering must not move ===")
    with db_connect() as conn:
        rows = conn.execute("SELECT id, category FROM images ORDER BY id").fetchall()
        ids = [int(r["id"]) for r in rows]
        cats = [str(r["category"]) for r in rows]
        # deterministic shuffle: permute the category column across images
        permuted = [str(v) for v in np.random.RandomState(7).permutation(cats)]
        changed_rows = sum(1 for a, b in zip(cats, permuted) if a != b)
        for image_id, new_cat in zip(ids, permuted):
            conn.execute("UPDATE images SET category=? WHERE id=?",
                         (new_cat, image_id))
        after = dict(conn.execute("SELECT id, category FROM images").fetchall())
    check("M8", "images.category column rewritten in SQLite (permuted)",
          changed_rows >= len(ids) // 2 and dict(zip(ids, cats)) != after,
          f"{changed_rows}/{len(ids)} rows changed category")

    fresh = start_kmeans(K, SEED)
    check("M8", "iteration-0 assignments IDENTICAL after category rewrite",
          fresh["assignments"] == a0["assignments"],
          f"first diff at {first_diff(a0['assignments'], fresh['assignments'])}")
    _snaps, ff = run_to_convergence(fresh["run_id"])
    check("M8", "converged assignments IDENTICAL after category rewrite",
          ff["assignments"] == af["assignments"],
          f"first diff at {first_diff(af['assignments'], ff['assignments'])}")

    rev_after = requests.get(f"{ADM}/api/ml/reveal/{fresh['run_id']}",
                             timeout=10).json()
    dist_changed = [cl["cluster"] for cl, before in
                    zip(rev_after["clusters"], rev_before["clusters"])
                    if cl["distribution"] != before["distribution"]]
    check("M8", "reveal distributions CHANGED after rewrite (reveal reads labels)",
          len(dist_changed) > 0, f"clusters changed: {dist_changed}")
    purity_changed = [cl["cluster"] for cl, before in
                      zip(rev_after["clusters"], rev_before["clusters"])
                      if cl["purity"] != before["purity"]]
    check("M8", "reveal purity values CHANGED after rewrite",
          len(purity_changed) > 0
          or rev_after["overall_purity"] != rev_before["overall_purity"],
          f"purity now {[cl['purity'] for cl in rev_after['clusters']]} "
          f"(was {[cl['purity'] for cl in rev_before['clusters']]})")
    check("M8", "categories affect ONLY reveal — assignments stayed bit-identical",
          ff["assignments"] == af["assignments"] and len(dist_changed) > 0, "")


def test_m9_validation():
    print("\n=== M9 — Validation + 404s ===")
    for bad_k in (1, 9, 0, -3):
        r = requests.post(f"{ADM}/api/ml/kmeans", json={"k": bad_k, "seed": 42},
                          timeout=10)
        check("M9", f"k={bad_k} rejected with 400", r.status_code == 400,
              f"HTTP {r.status_code}")
    r = requests.post(f"{ADM}/api/ml/kmeans", json={"k": "5", "seed": 42}, timeout=10)
    check("M9", "non-integer k rejected with 400", r.status_code == 400,
          f"HTTP {r.status_code}")

    r = requests.post(f"{ADM}/api/ml/kmeans/step", json={"run_id": "nope"},
                      timeout=10)
    check("M9", "step unknown run_id -> 404", r.status_code == 404,
          f"HTTP {r.status_code}")
    r = requests.get(f"{ADM}/api/ml/reveal/nope", timeout=10)
    check("M9", "reveal unknown run_id -> 404", r.status_code == 404,
          f"HTTP {r.status_code}")
    r = requests.get(f"{ADM}/api/ml/neighbors/999999?k=5", timeout=10)
    check("M9", "neighbors unknown image_id -> 404", r.status_code == 404,
          f"HTTP {r.status_code}")
    r = requests.get(f"{ADM}/api/ml/embedding/999999", timeout=10)
    check("M9", "embedding unknown image_id -> 404", r.status_code == 404,
          f"HTTP {r.status_code}")

    first_id = requests.get(f"{ADM}/api/ml/data", timeout=10).json()["ids"][0]
    emb1 = requests.get(f"{ADM}/api/ml/embedding/{first_id}", timeout=10).json()
    check("M9", "embedding vector is 384 floats",
          emb1["dim"] == 384 and len(emb1["vector"]) == 384
          and all(isinstance(v, float) for v in emb1["vector"][:5]),
          f"dim={emb1['dim']}")


def test_m10_embed_single_flight():
    print("\n=== M10 — Embed job: 409 single-flight + idempotent finish ===")
    up = requests.post(
        f"{ADM}/api/admin/upload", data={"category": "dog"},
        files=[("files", ("qa-extra.jpg", jpg_bytes(), "image/jpeg"))], timeout=30)
    extra = up.json()["uploaded"]
    check("M10", "extra image uploaded for the job-race test",
          up.status_code == 200 and len(extra) == 1,
          f"image_id={extra[0]['image_id'] if extra else None}")

    r1 = requests.post(f"{ADM}/api/ml/embed", json={}, timeout=10)
    check("M10", "POST /api/ml/embed starts the job",
          r1.status_code == 200 and r1.json().get("started") is True,
          f"HTTP {r1.status_code} {r1.text[:80]}")
    r2 = requests.post(f"{ADM}/api/ml/embed", json={}, timeout=10)
    check("M10", "second concurrent POST /api/ml/embed -> 409 running",
          r2.status_code == 409 and r2.json().get("running") is True,
          f"HTTP {r2.status_code} {r2.text[:80]}")

    def wait_job_done(seconds: float) -> dict:
        deadline = time.time() + seconds
        status = {}
        while time.time() < deadline:
            status = requests.get(f"{ADM}/api/ml/status", timeout=10).json()
            if not status["job"]["running"]:
                return status
            time.sleep(2)
        return status

    status = wait_job_done(420)
    check("M10", "job finished (phase=done) and store synced (stale=0)",
          status.get("job", {}).get("phase") == "done"
          and status.get("embeddings", {}).get("stale") == 0,
          f"phase={status.get('job', {}).get('phase')} emb={status.get('embeddings')}")

    r3 = requests.post(f"{ADM}/api/ml/embed", json={}, timeout=10)
    status2 = wait_job_done(120)
    emb2 = status2.get("embeddings", {})
    check("M10", "re-embed after finish accepted and idempotent",
          r3.status_code == 200 and emb2.get("stale") == 0
          and emb2.get("count") == emb2.get("images"),
          f"HTTP {r3.status_code} emb={emb2}")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

def main() -> int:
    server = None
    log_fh = None
    try:
        seed = run_cli("app.seed_placeholders", timeout=300)
        if seed.returncode != 0 or "seeded" not in seed.stdout:
            print(f"seeding failed:\n{seed.stdout}\n{seed.stderr}")
            return 2
        print(f"seeded placeholders into {DATA_DIR}")

        log_fh = open(f"/tmp/al-mlqa-server-{int(time.time())}.log", "w")
        server = subprocess.Popen(
            [str(PYTHON), str(BACKEND / "run.py"), "--no-seed"],
            env={
                **os.environ,
                "ANIMAL_LAB_DATA": str(DATA_DIR),
                "PUBLIC_PORT": str(PUBLIC_PORT),
                "ADMIN_PORT": str(ADMIN_PORT),
            },
            stdout=log_fh, stderr=subprocess.STDOUT,
        )
        for _ in range(120):
            try:
                if requests.get(f"{PUB}/api/health", timeout=2).json().get("ok"):
                    break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            print("server never became healthy — log:")
            print(open(log_fh.name).read()[-2000:])
            return 2
        print(f"server up — data dir {DATA_DIR}")

        ids, X = test_m1_embeddings_store()
        test_m2_leak_guards_static()
        a0, af = test_m3_lloyd_iterations(ids, X)
        test_m4_determinism(a0, af)
        test_m5_different_k()
        test_m6_neighbors(ids, X)
        rev_before = test_m7_reveal(af["run_id"])
        test_m8_label_leakage(a0, af, rev_before)
        test_m9_validation()
        test_m10_embed_single_flight()
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if log_fh is not None:
            log_fh.close()

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 64)
    print(f"RESULTS: {len(results) - len(failed)}/{len(results)} passed")
    for name, ok, detail in failed:
        print(f"  FAIL: {name}  —  {detail}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
