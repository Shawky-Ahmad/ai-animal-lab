# Unsupervised Lab — ML/Interface Contract v1 (binding)

Everything lives under the EXISTING project (read API_CONTRACT.md for the base system).
Admin app (127.0.0.1:8001) gains page `/unsupervised` + `/api/ml/*` endpoints.
The PUBLIC app is untouched. No categories ever enter the clustering code path.

## Files
- `backend/app/embeddings.py` — model + embedding + PCA precompute (module + CLI: `python -m app.embeddings`)
- `backend/app/ml_kmeans.py` — pure NumPy K-means (Lloyd), deterministic, NO db access, NO category access
- `backend/app/ml_state.py` — in-memory clustering runs + neighbors + reveal (admin-app state)
- `backend/requirements-ml.txt` — torch (CPU index), transformers (numpy goes in main requirements)
- `scripts/setup-ml.sh` — installs ML deps with the CPU torch index
- `frontend/admin/unsupervised.html` + `unsupervised.js` (+ styles in admin/styles.css)

## Embeddings store: `DATA_DIR/embeddings.npz`
- `image_ids`: int64 [N] — STABLE ids from the images table
- `embeddings`: float32 [N, 384] — DINOv2-small CLS token, L2-normalized
- `file_hashes`: <sha256 hex> str [N] — change detection (re-embed modified files)
- `pca_mean`: float32 [2], `pca_components`: float32 [384, 2] — SVD PCA fitted on the embeddings
- `pca`: float32 [N, 2] — cached projections

Model: `facebook/dinov2-small` via transformers, local files after first download,
GPU (`torch.cuda.is_available()`) else CPU, batch inference (no_grad, eval).

## Admin ML endpoints (all same-origin, admin app)
- `GET  /api/ml/status` →
  `{"ml_available": bool, "model_loaded": bool, "embeddings": {"count": N, "images": M, "stale": S},
    "job": {"running": bool, "phase": "idle|loading|embedding|pca|saving|done|error", "done": n, "total": m, "error": str|null}}`
- `POST /api/ml/embed` → starts embedding job in a background thread (single-flight lock).
  `{"started": true}` or `409 {"running": true}`. Idempotent: only new/changed files are embedded.
- `GET  /api/ml/data` →
  `{"ids": [int], "filenames": ["<uuid>.jpg"], "pca": [[x, y], ...], "dim": 384}`
  NO category field here (leak guard). Missing/changed images until embed job runs.
- `POST /api/ml/kmeans` body `{"k": 2..8, "seed": 42}` →
  `{"run_id": "k5-s42", "k": 5, "iteration": 0, "assignments": [int]*N,
    "centroids_2d": [[x,y]]*k, "inertia": float, "changed": N, "converged": false}`
  K-means++ init on the 384-dim embeddings with `RandomState(seed)`; deterministic per (k, seed).
- `POST /api/ml/kmeans/step` body `{"run_id": ...}` → same shape, next Lloyd iteration.
  One call = one real iteration (assign → update centroids). `changed` = #points that moved;
  `converged = changed == 0`. Max 100 iterations, then converged=true.
- `GET  /api/ml/neighbors/{image_id}?k=5` →
  `{"image_id": id, "neighbors": [{"image_id": id, "filename": "...", "similarity": 0.87}]*5}`
  similarity = cosine on the L2-normalized embeddings, sorted desc, excluding self.
- `GET  /api/ml/embedding/{image_id}` → `{"image_id": id, "dim": 384, "vector": [float]*384}`
- `GET  /api/ml/reveal/{run_id}` → per-cluster ground-truth stats (the ONLY endpoint touching categories):
  `{"clusters": [{"cluster": c, "size": n, "distribution": {"cat": n, ...},
                  "purity": 0.0-1.0, "majority": "cat",
                  "examples": [{"image_id", "filename", "category"}]*min(6, n)}],
    "overall_purity": float, "note": "..."}`
  purity = share of the cluster's majority category. overall_purity = size-weighted mean.
  404 if run unknown. Categories come ONLY from images.category (instructor labels).

## Leak guards (must hold)
- `ml_kmeans.py` signature: `kmeans_assign(X, centroids)`, `kmeans_init(X, k, rng)`, `step(...)` —
  arrays in, arrays out. No db, no filenames, no categories, no HTTP.
- `/api/ml/kmeans*` handlers pass only the embeddings matrix.
- `/api/ml/data` payload contains no category/original_name fields.
- Original names like "cat_placeholder_01.jpg" must NOT be shown pre-reveal in the UI;
  display "Image #<id>" until Reveal.

## UI sections (frontend/admin/unsupervised.*, Chart.js vendored at assets/chart.umd.js)
A "Image Features": picker (Image #id), original photo, 384-dim heatmap (canvas 24×16 grid),
5 NN thumbs + similarity %, disclaimer that dims are not human-interpretable named features.
B "Live Clustering": Chart.js scatter (pca points, fixed axes), dots colored by CURRENT assignment,
centroids as distinct markers; controls: K 2–8, Start, Step, Auto-play, Reset; iteration counter,
inertia, moved-count/converged badge; dot positions NEVER change when assignments change.
C "Explore Clusters": click dot or cluster chip → image grid of that cluster + count.
D "Reveal Answers": button → per-cluster stacked category bars, purity, mixed-cluster callout,
explicit note that clustering never used these labels and results are not "accuracy".
