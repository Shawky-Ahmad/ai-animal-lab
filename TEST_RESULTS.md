# AI Animal Lab — Test Results (2026-10-31)

Environment: Linux (CachyOS), Python 3.14.7, FastAPI + uvicorn (venv),
SQLite (WAL), Chrome-based in-app browser for GUI checks, real Cloudflare
quick tunnel + real GitHub Pages deployment for the end-to-end test.

---

## Automated integration suite — `backend/tests/test_api.py` — **51/51 PASS**

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

## Remote helper access (`ADMIN_PIN`) — **PASS**

Verified on an isolated instance (`ADMIN_PIN=paw-2026`, ports 8842/8843,
`--tunnel`):

* Without auth / wrong PIN → `401` with a `WWW-Authenticate` Basic challenge
  (browser shows a login box); wrong attempts are delayed (rate-limited).
* With the PIN (any username) → `200` for `/dashboard`, `/` (upload console),
  `/api/admin/summary`, `POST /api/admin/upload`, and CSV exports.
* The server binds to all interfaces when a PIN is set: the dashboard is also
  reachable from the venue Wi-Fi via the laptop's LAN IP (still PIN-gated).
* With `--tunnel` + PIN, a **second tunnel** is started for the instructor
  tools; through that public URL: no PIN → 401, correct PIN → 200.
* Student public app unaffected (no auth, `/api/health` 200), and the full
  integration suite re-ran green (52/52) in default (no-PIN) mode.

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
