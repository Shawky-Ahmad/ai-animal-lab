# AI Animal Lab — Test Results (2026-10-31)

Environment: Linux (CachyOS), Python 3.14.7, FastAPI + uvicorn (venv),
SQLite (WAL), Chrome-based in-app browser for GUI checks, real Cloudflare
quick tunnel + real GitHub Pages deployment for the end-to-end test.

---

## Phase 2 — Unsupervised Learning Lab — **all tests PASS**

Built by three sub-agents (ML backend, UI, adversarial QA) against
`ML_CONTRACT.md`, integrated and verified by the lead.

**Real model, real inference.** torch 2.15.0+cpu + transformers 5.22.1;
`facebook/dinov2-small` downloaded from Hugging Face (~90 MB) and run locally.
50 seeded images → 384-dim L2-normalized CLS embeddings, sha256 change
detection, SVD PCA — all stored in `backend/data/embeddings.npz` with stable
image ids.

**ML suite — `backend/tests/test_ml.py` (written by the QA agent): 62/62 PASS**
- Embeddings: ids == db ids, shape [50,384], norms ≈ 1, finite, hashes match,
  re-run embeds 0 (cache), modified file → exactly 1 re-embed.
- **Label leakage (centerpiece): after permuting every category in the
  database, K-means assignments were bit-identical at iteration 0 and at
  convergence** (they must not depend on labels), while reveal stats flipped —
  proving categories touch only the reveal endpoint. Source grep: no
  "category"/"db"/"sqlite" in `ml_kmeans.py`; `/api/ml/data` payload has no
  category/original_name; UI shows "Image #id" only until Reveal.
- Real Lloyd iterations: inertia non-increasing at every step; converged runs
  are no-ops; convergence = a genuine fixed point (independently recomputed).
- Determinism: same (k, seed) twice → identical assignments.
- Different K → different partition (k=2 vs k=5, structure compared).
- Neighbors: endpoint top-5 == independent cosine recompute from the npz.
- Reveal math: distributions sum to cluster sizes; purity/overall purity
  recomputed independently.
- Validation: k∉2..8 → 400; unknown ids/runs → 404; concurrent embed → 409.
- Works without torch installed (numpy-only endpoints), 503 with setup hint
  for the embed job.

**QA challenge found and fixed a real bug (QA-1):** the original `kmeans_step`
re-assigned against the *same* centroids the start had already used, so every
run claimed CONVERGED after one fictional step (observed live as
"Iteration 1 · moved 0 dots"). Fixed to the standard update→re-assign Lloyd
order and proven on overlapping synthetic data where the old code stopped
12.6% above true Lloyd inertia with 28/200 points still movable. A UI
hardening fix rejects assignment snapshots that don't match the loaded
dataset size.

**Browser verification (real page, real model):**
- A: heatmap renders the selected photo's 384 values; NN panel shows the 5
  most-similar cats at 98.7%–97.3% cosine for a cat photo (genuine
  similarity); "Image #id" labels only, no categories.
- B: K=5 start → 5 visibly separated groups in PCA space, centroids marked;
  step/auto-play update colors only ("Dot positions are fixed" caption);
  K=2 re-clusters genuinely (inertia 14 vs 2.2, fish group split out).
- C: clicking a cluster chip shows that cluster's 10 photos (all rabbits for
  the rabbit cluster) with counts and no animal names.
- D: reveal shows per-cluster stacked category bars, purity 100% ×5 on the
  placeholder set, overall purity 100%, with the "K-means never saw these
  labels" note.

**Existing suite after integration: 53/53 PASS** (`test_api.py`, one
data-dependent check now additionally triggers on this dataset).

---

## Update 3 — ADMIN_PIN support removed

Removed at the user's request: helpers run their own clone, so the
instructor tools stay localhost-only wherever the server runs and no PIN
gate is needed. `ADMIN_PIN` / `admin_host` settings, the Basic-auth
middleware, and the second (instructor) tunnel were all removed; default
behavior (admin on 127.0.0.1 only) is unchanged and the full suite was
re-run green afterward.

---

## Update 2 — mature UI, workshop progress bar, repo restructure

* **Student site restyled** ("dark lab" theme: charcoal surfaces, Inter,
  teal accent, matter-of-fact copy) — verified on a phone-size viewport and
  on the live GitHub Pages deployment; full vote flow re-verified in-browser.
* **Workshop progress bar** added to the dashboard: labels toward a
  configurable session goal (`WORKSHOP_GOAL`, default 35 × 25 = 875) with
  milestone ticks, plus exact catalog coverage ("photos with ≥1 label").
  Verified with a demo instance: 84/120 labels rendered as 70% with
  coverage 50/50. Suite check added for the default goal → **52/52 PASS**.
* **Repo restructure**: full project now on `main` (clone + `./start.sh`),
  student frontend on the `site` branch (what GitHub Pages serves).
  Verified: fresh `git clone` contains everything, scripts parse, Pages
  rebuilt from `site` and serves the new theme + live tunnel; production
  vote re-verified end-to-end in the browser.
* **Remote-helper access** (`ADMIN_PIN`): see section below.

---

## Automated integration suite — `backend/tests/test_api.py` — **52/52 PASS**

Launches an isolated server (fresh temp data dir, ports 8820/8821, upload
capped at 1 MB / 12 files) and verifies against the real HTTP API + SQLite.

**Test A — Image loading** (5 checks) — PASS
Config reports 5 categories / 50 seeded images; images served by id with
`image/jpeg` MIME and decodable 640×640 JPEG payloads; unknown id → 404;
`/api/next` returns exactly 3 distinct choices + progress fields.

**Test B — Submission** (3 checks) — PASS
Vote accepted (`labeled: 1`); row verified **directly in the SQLite file** with
correct `choice` and server-computed `is_correct`; duplicate vote returns
`duplicate: true`, table still holds exactly one row with the original choice.

**Test C — Multiple students** (4 checks) — PASS
4 simulated students × 5 votes → 20 independent rows; per-student counts
exact; several students voted on the same images independently; no student
ever voted the same image twice.

**Test D — Progress** (5 checks) — PASS
12 consecutive `next → vote` rounds returned 12 distinct images (no repeats);
a "refresh" (new session call) restored `labeled: 12`; `/api/progress`
agreed; a student run to exhaustion completed at exactly the target of 35.

**Test E — Statistics** (9 checks) — PASS
5 controlled votes (3 correct / 2 wrong, choice set validated against the
image's actual choice triple) on a known image:
`total_votes +5`, `student_count +5` distinct, category accuracy
`3 correct / 5` = expected value, `overall_accuracy` consistent with
per-category sums, all 5 votes stored individually with correct
`is_correct`, `votes_over_time` buckets summing to the true total, and the
**entire top-15 disagreement list matches an independent recomputation from
the raw database** (ids, counts, accuracy, ordering).

**Test F — CSV exports** (6 checks) — PASS
`votes.csv` header exact, row count == votes in SQLite, first row matches DB
values verbatim. `dataset.csv` has one `vote_<category>` column per category;
a deliberate 1–1 tie on a fresh image exports `is_tie=true` with **empty**
`majority_category`; a 3–2 winner exports `majority_category=cat`,
`is_tie=false`.

**Test G — Bulk upload** (9 checks) — PASS
8 mixed JPG/PNG files uploaded in one request; per-category counts updated
and total increased by 8; original filenames preserved in the catalog;
fresh students are served the new images; rejected as expected: `.txt`
extension, fake magic bytes, >1 MB file (cap active), 13th file in a
12-file-cap instance, and a non-configured category (`dragon`).

**Test I — Basic security** (11 checks) — PASS
Through the **public** port: `/dashboard`, `/api/admin/summary`,
`POST /api/admin/upload`, and CSV exports are unreachable (404/405); path
traversal on the preview route blocked (`..%2f..%2f…` → 404); non-image
files not servable; non-integer image ids rejected; malformed `student_id`
rejected; a vote choice outside the image's stored triple rejected (400);
client-supplied `is_correct` ignored (stored 0); CORS `allow-origin: *`
present for cross-origin origins.

---

## GUI verification (real browser, phone-sized 390×844 viewport)

| Screen | Result |
|---|---|
| Welcome card | Renders cleanly; mission text; Start button |
| Labeling screen | Large image card, bold question, 3 big answer buttons, progress bar + "n / 35" |
| Vote cycle | Tap → buttons lock → next image + new choices + progress advance (verified 0→1→2/35) |
| Refresh persistence | Reload skips welcome, restores progress, serves a new image |
| Completion screen | "Thank you! You labeled 35 photos…" after target reached |
| Instructor console | Category dropdown populated; drag-drop zone; per-category count cards; thumbnail grid with vote chips |
| Console upload flow | Injected 2 real PNG Files through the UI's own code path → "Upload complete — 2 images added", counts rabbit 10→12, total 50→52 |
| Live dashboard | 4 KPI cards, line chart (votes/minute), per-category accuracy bars, donut with center total, disputed-images table (15 rows), 5 s auto-refresh ("Updated Xs ago") |

### Bugs found by GUI testing — all fixed and re-verified

1. **Start button never entered the quiz** — the click handler rendered the
   first question but never switched the visible screen (only the
   resume-after-refresh path did). Fix: `showScreen("quiz")` before the first
   fetch. *(frontend/student/app.js)*
2. **"No labels yet" overlay permanently covered the live charts** —
   `.chart-empty { display:flex }` overrode the UA's `[hidden]` rule, so the
   overlay never cleared once real data arrived. Fix:
   `.chart-empty[hidden] { display:none }`. *(frontend/admin/styles.css)*
3. **Dashboard horizontal overflow** — grid items couldn't shrink below the
   Chart.js canvas width (`min-width:auto`), pushing the donut card off-screen.
   Fix: `.charts-grid .chart-card { min-width:0 }`. *(frontend/admin/styles.css)*

---

## Test H — Real deployment — **PASS (verified end-to-end)**

* Cloudflare **quick tunnel** started against the public app:
  `https://hotel-euro-billion-preservation.trycloudflare.com`
  (ephemeral by design — a fresh URL is printed on each `start.sh --tunnel`).
* Over the tunnel: `/api/health` OK; image bytes served (HTTP 200,
  image/jpeg); CORS `access-control-allow-origin: *` present for the
  `github.io` Origin; preflight (OPTIONS) OK.
* **GitHub Pages repo created and deployed**:
  https://shawky-ahmad.github.io/ai-animal-lab/ (repo
  `Shawky-Ahmad/ai-animal-lab`, public, student frontend + generated
  `config.js` pointing at the tunnel URL).
* **Full production path exercised in a real browser**: opened the
  github.io URL (different origin — exactly what students' phones do) →
  welcome → Start → image loaded **cross-origin from the tunnel** → answered
  → progress 1/35 → **vote verified in the laptop's SQLite**:
  `s-04789604… | image 51 | rabbit | is_correct=1 | 23:43:41 UTC`.
* **Admin isolation over the tunnel verified**: `/dashboard` → 404,
  `/api/admin/summary` → 404, `/api/admin/export/votes.csv` → 404,
  `POST /api/admin/upload` → 405. Uploads/exports/dashboard are reachable
  only from the laptop itself (127.0.0.1:8001).

## Not covered / notes

* The OS **file-picker dialog** itself can't be driven from the automated
  browser; the upload path was tested by injecting real `File` objects
  through the page's own event handlers (same code path as drag-drop /
  picker) and at the API level in Test G.
* The deployed `config.js` currently points at the test tunnel URL above,
  which **stops working when this session's tunnel stops**. Workshop
  workflow: `./start.sh --tunnel` → copy the printed URL → `./scripts/deploy-pages.sh <url>`.
* Quick tunnels change URL per restart; a named tunnel gives a permanent URL
  (commands in README).
* Placeholder images are Pillow-drawn geometric animals (no AI image
  generation), auto-seeded only when the database is empty. Replace with real
  photos via the instructor console.
* After testing, `backend/data/` was reset so the workshop starts clean;
  the next `./start.sh` re-seeds 50 placeholders.
