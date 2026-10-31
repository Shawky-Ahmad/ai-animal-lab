# AI Animal Lab — Context Handoff
**Date:** 2026-10-31 · **Author:** ZCode session (build + test of Phases 1–2) · **Status: COMPLETE & DEPLOYED**

This document hands off everything about the **AI Animal Lab** project: what was built,
how it was tested, what is currently running, and what comes next. A new developer,
a new ZCode session, or the workshop helper should be able to continue from here
without re-reading the whole history.

---

## 1. What this project is

An AI Club workshop tool for ~30 students (ages 13–18):

- **Students** (phones, GitHub Pages site): look at animal photos, pick one of three
  answers, ~35 photos each. Answers are anonymous (random localStorage id).
- **Instructor laptop**: Python FastAPI backend + SQLite stores every individual vote;
  local-only admin console (bulk image upload) and live statistics dashboard.
- **Phase 2 demo**: an Unsupervised Learning Lab (DINOv2 embeddings + live K-means)
  shown on the projector BEFORE students label.
- **Future (NOT built yet)**: supervised learning on the student-labeled dataset.

Repo layout (branch `main` = full project; branch `site` = student frontend for GitHub Pages):

```
/home/kuro/AUS/AI Club/animal-lab/
├── start.sh                  # ONE COMMAND launcher (venv, deps, cloudflared download, servers, tunnel)
├── API_CONTRACT.md           # Phase 1 API/schema spec (binding)
├── ML_CONTRACT.md            # Phase 2 ML/interface spec (binding)
├── README.md                 # workshop operations manual (read this first)
├── TEST_RESULTS.md           # all test evidence, chronological updates
├── backend/
│   ├── run.py                # starts public app (0.0.0.0:8000) + admin app (127.0.0.1:8001), --tunnel flag
│   ├── app/                  # config.py, db.py, public_app.py, admin_app.py, seed_placeholders.py,
│   │                         # embeddings.py (DINOv2), ml_kmeans.py, ml_state.py, config.json (categories)
│   ├── tests/test_api.py     # Phase 1 suite (53 checks)
│   ├── tests/test_ml.py      # Phase 2 suite (62 checks, incl. label-leakage permutation proof)
│   ├── requirements.txt      # fastapi, uvicorn, python-multipart, pillow, requests, numpy
│   ├── requirements-ml.txt   # transformers (+ torch installed separately, CPU index)
│   └── data/                 # RUNTIME DATA (gitignored): animal_lab.sqlite3, images/, embeddings.npz, tunnel_url.txt
├── frontend/student/         # student site (dark "lab" theme; published to `site` branch)
├── frontend/admin/           # instructor console (index.html), dashboard (dashboard.html), unsupervised.html
├── scripts/setup-ml.sh       # one-time ML deps (CPU torch + transformers)
├── scripts/deploy-pages.sh   # publish student site to `site` branch + set tunnel URL in config.js
├── scripts/reset-data.sh     # wipe db+images for a fresh workshop
└── bin/cloudflared           # downloaded by start.sh when missing (gitignored)
```

## 2. Current live state (as of handoff)

| Item | State |
|---|---|
| Live server | RUNNING (started from this session as a background task — **it may stop when this session closes**) |
| Restart procedure | `cd "/home/kuro/AUS/AI Club/animal-lab" && ./start.sh --tunnel` (Ctrl+C stops it when run in a terminal) |
| Current tunnel | https://monitor-moved-geometry-williams.trycloudflare.com (quick tunnel — **URL changes on every restart**) |
| Student site | https://shawky-ahmad.github.io/ai-animal-lab/ (GitHub Pages, `site` branch) |
| After each server restart | MUST re-run: `./scripts/deploy-pages.sh <new-tunnel-url>` (Pages caches config.js ~10 min; the page's "Try again" self-heals stale configs) |
| Instructor tools | http://127.0.0.1:8001/ (upload console) · http://127.0.0.1:8001/dashboard (live stats) · http://127.0.0.1:8001/unsupervised (Phase 2 demo) |
| Database | `backend/data/animal_lab.sqlite3` — 50 seeded placeholder images (PIL-drawn geometric animals, NOT AI-generated), a few test votes |
| Embeddings | `backend/data/embeddings.npz` — 50 images × 384-dim DINOv2 + PCA (Phase 2 precompute already done) |
| GitHub | Repo `Shawky-Ahmad/ai-animal-lab` (public). gh CLI authenticated as Shawky-Ahmad. Pages serves `site` branch. Latest commit: Phase 2 (8fb7c5d). |
| Fresh workshop reset | `./scripts/reset-data.sh` → next start re-seeds 50 placeholders |

## 3. What was built, chronologically

### Phase 1 — Labeling website + backend (complete)
- Public FastAPI app (port 8000, tunnel-facing): `/api/config|session|next|vote|progress|images/{id}/file|stats/summary`, static student site at `/`. CORS `*`. Votes: one per student per image (`UNIQUE` + INSERT OR IGNORE), `is_correct` computed server-side.
- Admin FastAPI app (port 8001, 127.0.0.1 ONLY): upload console (bulk multipart upload, magic-byte validation, size/count caps, uuid filenames), dashboard page, `/api/admin/*` (summary, images, uploads, CSV exports `votes.csv` + `dataset.csv` with tie flagging), Phase 2 ML endpoints.
- SQLite (WAL, parameterized): `images` (id, uuid filename, original_name, category, choices JSON, created_at), `votes` (student_id, image_id, choice, is_correct, created_at, UNIQUE(student_id,image_id)), `students` (student_id, target, first/last_seen).
- Student site (vanilla JS, mobile-first, dark "lab" theme, teal accent, Inter): welcome → label (3 big buttons, progress bar, "Saved" pill, prefetch) → completion. Refresh-proof via session restore. Self-heals stale `config.js` (re-fetches bypassing cache on boot failure — GitHub Pages caches 10 min).
- Dashboard (Chart.js, 5 s polling): KPI cards, workshop progress bar toward `WORKSHOP_GOAL` (default 35×25=875, env-configurable), labels-over-time line, per-category accuracy bars, label-distribution donut, most-disputed-images table, CSV export links. Explicit "student labeling accuracy — no ML model trained yet" badge.
- Placeholder seed images: 50 PIL-drawn geometric animal faces (10 each of cat/dog/rabbit/bird/fish) with pastel gradients; auto-seeded when db is empty. Replace with real photos via the upload console (target 300–400 images eventually).
- Deployment (REAL, verified): GitHub Pages repo live; cloudflared binary vendored/downloaded; quick tunnels tested end-to-end (vote from production Pages site verified in SQLite).

### Ops decisions made along the way
- `ADMIN_PIN` remote-helper feature was built, then **removed at user request** (helpers clone the repo and run their own instance; admin stays localhost-only wherever it runs).
- Frontend restyled to a more mature dark theme (was lavender/rounded/friendly).
- `config.js` supports `?api=<url>` override (sticky in localStorage) — useful for tests.
- ML deps are OPTIONAL (`requirements-ml.txt` + `scripts/setup-ml.sh`); core works without torch; `/unsupervised` shows the setup command if missing.

### Phase 2 — Unsupervised Learning Lab (complete)
- `app/embeddings.py`: real `facebook/dinov2-small` (downloaded from HF), local PyTorch inference (GPU auto, CPU fine), 384-dim L2-normalized CLS embeddings, batched; sha256 change detection; numpy-SVD PCA; saves `embeddings.npz` (stable image ids). CLI: `python -m app.embeddings`.
- `app/ml_kmeans.py`: pure-NumPy deterministic K-means (k-means++ init, RandomState seed). **No db/categories/HTTP access — leak-proof by construction.**
- `app/ml_state.py`: single-flight embed jobs, in-memory kmeans runs (`k{k}-s{seed}`), cosine neighbors, reveal computation (the ONLY category reader).
- `/unsupervised` page (admin app): A) image features (384-dim heatmap + 5 nearest neighbors + "dimensions have no human names" explanation), B) live K-means on 2-D PCA scatter (K 2–8, Start/Step/Auto-play/Reset, fixed dot positions, real Lloyd iterations), C) cluster explorer (click dot/chip → cluster's images, no names), D) reveal (per-cluster category bars, purity, mixed clusters, honest framing).
- Built by THREE sub-agents (ML backend, UI, adversarial QA) against ML_CONTRACT.md; lead integrated + browser-verified.

## 4. Test status (all actually run)

| Suite | Result | Location |
|---|---|---|
| Phase 1 API integration (`backend/tests/test_api.py`) | **53/53 PASS** | launches own instance (temp data, ports 8820/8821) |
| Phase 2 ML (`backend/tests/test_ml.py`) | **62/62 PASS** | ports 8870/8871; real model inference |
| Browser GUI (student site, console, dashboard, unsupervised) | verified across sessions | screenshots in session history |

Run them: `cd backend/tests && ../.venv/bin/python test_api.py` and `... test_ml.py`.

**Key QA finding (fixed):** original `kmeans_step` re-assigned against unchanged centroids → fake "CONVERGED" after one step. Fixed to standard update→re-assign Lloyd order; QA proved old code stopped 12.6% above true convergence on overlapping data.

**Label-leakage proof:** permuting every category in the DB leaves K-means assignments bit-identical; categories touch only `/api/ml/reveal`. UI shows "Image #id" only until reveal (original_name like "cat_placeholder_01.jpg" never leaks pre-reveal).

## 5. Known quirks / notes

1. **Quick tunnels change URL every restart** → always re-run `./scripts/deploy-pages.sh <url>`. For a permanent URL use a named Cloudflare tunnel (README has the recipe).
2. **GitHub Pages caches ~10 min** → after redeploy, the site may briefly use the old API URL; the app self-heals (fresh config re-fetch + retry), or Ctrl+Shift+R.
3. The live server from this session runs as a background task — if this ZCode session ends and the server stops, just start it again (`./start.sh --tunnel` + redeploy script).
4. `backend/data/` currently holds TEST votes + placeholder images. For a clean workshop: `./scripts/reset-data.sh` then upload real photos.
5. Python 3.14.7 with torch 2.15.0+cpu / transformers 5.22.1 — working combination (documented in TEST_RESULTS).
6. The workshop venue needs: instructor laptop online (tunnel) + students online (GitHub Pages). Students never touch the laptop.

## 6. Next steps (Phase 3 — NOT built, by design)

1. Collect real photos (300–400 across 5 categories) via the upload console; keep an independent test set aside (the dataset CSV supports this).
2. Run the workshop: students label ~35 images each; dashboard monitors; export `votes.csv` + `dataset.csv` (majority vote per image, ties flagged).
3. Supervised phase (future): reuse `embeddings.npz` (stable image ids!) — e.g., logistic regression / small head on DINOv2 features, train on majority student labels, evaluate on the held-out test set, compare "model accuracy" vs the dashboard's "student labeling accuracy" (deliberately kept distinct everywhere).
4. Optional niceties: named tunnel for a fixed URL, delete/deactivate-image support, embedding auto-refresh on upload.

## 7. Cheat sheet

```bash
cd "/home/kuro/AUS/AI Club/animal-lab"
./start.sh --tunnel                                  # run everything (Ctrl+C to stop)
./scripts/deploy-pages.sh <tunnel-url>               # after every tunnel restart
./scripts/reset-data.sh                              # fresh workshop state
bash scripts/setup-ml.sh                             # one-time ML deps (done on this laptop)
cd backend && .venv/bin/python -m app.embeddings     # (re-)embed images
cd backend/tests && ../.venv/bin/python test_api.py  # Phase 1 tests
cd backend/tests && ../.venv/bin/python test_ml.py   # Phase 2 tests
pkill -f "animal-lab" ; pkill -f cloudflared         # force-stop everything
```

Key docs: `README.md` (operations) · `API_CONTRACT.md` (Phase 1 spec) · `ML_CONTRACT.md` (Phase 2 spec) · `TEST_RESULTS.md` (evidence).
