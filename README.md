# AI Animal Lab 🐾

A mobile-first animal image-labeling activity for a school AI workshop.
Students open a website on their phones, look at animal photos, and pick one of
three answers. Every answer is stored as an individual vote in a SQLite
database on the instructor's laptop and shown live on a statistics dashboard.
The collected dataset (with the instructor's ground-truth labels) is exported
as CSV for training a model **later** — no ML in this phase.

```
Phone (GitHub Pages) ──HTTPS──> Cloudflare Tunnel ──> laptop FastAPI ──> SQLite
                                        │
Instructor laptop ────────────────────► 127.0.0.1:8001 (upload console + dashboard, local only)
```

## Repository layout

```
animal-lab/                       (git branch `main` = full project)
├── start.sh                 # ONE COMMAND to start everything (laptop)
├── backend/
│   ├── run.py               # starts public API (0.0.0.0:8000) + admin app (127.0.0.1:8001)
│   ├── app/                 # FastAPI apps, SQLite layer, placeholder-image generator
│   │   └── config.json      # ← animal categories (edit + restart to change)
│   ├── tests/test_api.py    # integration test suite (52 checks, tests A–G + I)
│   └── data/                # created at runtime: SQLite db + images/  (never commit)
├── frontend/student/        # the student website (published to the `site` branch → GitHub Pages)
├── frontend/admin/          # instructor console (/) + live dashboard (/dashboard)
├── scripts/deploy-pages.sh  # publish student site + point it at the tunnel
├── scripts/reset-data.sh    # wipe db + images for a fresh workshop
├── bin/cloudflared          # downloaded automatically by start.sh (not in git)
└── API_CONTRACT.md          # exact API/schema specification
```

---

## Workshop day — quick start (laptop)

Get the project (full project lives on the `main` branch; the `site` branch is
what GitHub Pages serves):

```bash
git clone https://github.com/Shawky-Ahmad/ai-animal-lab.git
cd ai-animal-lab
./start.sh --tunnel
```

That single command:

1. creates the Python venv on first run and installs dependencies,
2. creates `backend/data/` (SQLite) and **auto-seeds 50 placeholder images**
   (10 each of cat / dog / rabbit / bird / fish) if the database is empty,
3. starts the **public API** on `0.0.0.0:8000` and the **instructor tools** on
   `127.0.0.1:8001` (localhost only — never reachable through the tunnel),
4. starts a **Cloudflare quick tunnel** and prints a big banner with the
   public URL, e.g. `https://something-words.trycloudflare.com`.

Then publish that URL to the student website (one command):

```bash
./scripts/deploy-pages.sh https://something-words.trycloudflare.com
```

Students open **https://shawky-ahmad.github.io/ai-animal-lab/** and start
labeling. You watch progress at **http://127.0.0.1:8001/dashboard**.

Stop everything with `Ctrl+C` in the terminal (servers + tunnel shut down).

> **Quick-tunnel URLs change on every restart.** After restarting
> `./start.sh --tunnel`, re-run `deploy-pages.sh` with the new URL (takes
> ~10 s: it updates one line in `config.js` and pushes). If you want a URL
> that never changes, use a *named* Cloudflare tunnel — see below.

### Letting someone else run the session when you're not there

The instructor tools are intentionally reachable **only on whichever laptop
is running the server** (`127.0.0.1:8001`) — that's what keeps them private
from students, on any network. Since everything is one clone plus one
command, hand-off is simple: whoever runs the workshop clones the repo on
their laptop and starts it:

```bash
git clone https://github.com/Shawky-Ahmad/ai-animal-lab.git
cd ai-animal-lab
./start.sh --tunnel
./scripts/deploy-pages.sh <the printed tunnel URL>
```

They then use `http://127.0.0.1:8001/dashboard` and `http://127.0.0.1:8001/`
on that laptop. Nothing to maintain, nothing for students to reach.

### Using real photos instead of placeholders

Open **http://127.0.0.1:8001/** on the laptop (instructor console):

1. pick the correct category from the dropdown,
2. drag in / select any number of photos (JPG/PNG/WebP, ≤15 MB each, ≤60 per
   batch — repeat batches for more),
3. Upload — images appear instantly in the student rotation, biased toward
   the least-labeled images so coverage stays balanced.

Per-category counts and recent uploads with vote counts are shown on the same
page. Photos are stored in `backend/data/images/` with the category in SQLite;
students never upload anything.

### Students per category / count tweaks

* Categories: edit `backend/app/config.json`, restart `start.sh`
  (minimum 3 categories — each image gets *correct + 2 wrong* choices).
* Images per student: `TARGET_PER_STUDENT=40 ./start.sh --tunnel` (default 35).
* Dashboard progress-bar goal: `WORKSHOP_GOAL=1000 ./start.sh --tunnel`
  (default 35 × 25 = 875 labels).

---

## During the workshop

| What | Where |
|---|---|
| Student website (give this to students) | https://shawky-ahmad.github.io/ai-animal-lab/ |
| Live statistics dashboard | http://127.0.0.1:8001/dashboard |
| Upload console | http://127.0.0.1:8001/ |
| CSV exports | buttons at the bottom of the dashboard |

The dashboard polls every 5 s and shows: total labels, participating
students, images labeled, overall student accuracy, labels-over-time line
chart, per-category accuracy bars, label-distribution donut, and the most
disputed images. **All numbers come from real stored votes.** The dashboard is
explicitly labeled *student labeling accuracy* — no model has been trained, so
there is no "model accuracy" yet.

Each student's browser generates an anonymous random ID (localStorage, no
accounts). Refreshing keeps their progress; each image is voteable once per
student; votes are never overwritten.

---

## Data & exports (after the workshop)

Buttons on the dashboard, or directly:

* `http://127.0.0.1:8001/api/admin/export/votes.csv` — one row per vote:
  `vote_id, student_id, image_id, filename, instructor_category,
  student_choice, is_correct, created_at`. Individual answers are preserved
  (no majority-collapsing).
* `http://127.0.0.1:8001/api/admin/export/dataset.csv` — one row per image:
  instructor category, total votes, one `vote_<category>` column per category,
  `majority_category`, **`is_tie`** (ties are flagged and `majority_category`
  is left empty — never silently resolved), `labeled_by`,
  `student_accuracy`.

For model training later: use `majority_category` where `is_tie=false`
(and enough votes per image), keep `instructor_category` as ground truth for
measuring labeling quality. Raw per-student votes stay in `votes.csv` for
agreement analysis.

Reset everything for the next workshop:

```bash
./scripts/reset-data.sh    # deletes db + images; re-seeds on next start
```

---

## One-time setup already done

* **GitHub Pages repo**: https://github.com/Shawky-Ahmad/ai-animal-lab
  (public; contains only `frontend/student` — no secrets, no data).
  Redeploy any time with `./scripts/deploy-pages.sh <tunnel-url>`.
* **cloudflared** binary is vendored at `bin/cloudflared`.

## Optional: stable tunnel URL (named tunnel)

Quick tunnels need a `deploy-pages.sh` re-run after each restart. For a fixed
URL (requires a free Cloudflare account + a domain on it):

```bash
./bin/cloudflared tunnel login
./bin/cloudflared tunnel create animal-lab
./bin/cloudflared tunnel route dns animal-lab lab.yourdomain.com
./bin/cloudflared tunnel run --url http://127.0.0.1:8000 animal-lab
./scripts/deploy-pages.sh https://lab.yourdomain.com   # once — never again
```

## Security posture (intentionally proportional to a workshop)

* The tunnel exposes **only** port 8000, which serves `/api/*` (config,
  session, next image, vote, progress, anonymized stats, image files by id)
  and the static student site. Verified: `/dashboard`, `/api/admin/*` and the
  CSV exports return 404/405 through the tunnel.
* Uploads, exports, and the dashboard are on 127.0.0.1:8001 only.
* Image files are served **by database id** — client paths never touch the
  filesystem; the preview route accepts only `^[a-f0-9]{32}\.(jpg|jpeg|png|webp)$`.
* All SQL is parameterized; request sizes are capped; uploaded content is
  validated by magic bytes, not by client headers; categories are validated
  against the configured list; student IDs must match a strict pattern.
* `is_correct` is computed server-side from the instructor label; clients
  cannot influence it, and a repeated vote is ignored (`INSERT OR IGNORE`),
  so no student can change anyone's recorded answer.
* The public stats endpoint contains aggregate counts only — no free-text,
  no device or personal data. Student IDs are random client-generated tokens.

## Running the tests

```bash
cd backend/tests && ../.venv/bin/python test_api.py
```

Spins up an isolated instance (temp data dir, ports 8820/8821, 1 MB upload
cap, 12 files/request cap) and runs 51 checks covering image loading, vote
persistence, multi-student isolation, progress/refresh, statistics math
(independently recomputed), CSV exports (incl. tie flagging), bulk upload +
validation, and the security checks. See `TEST_RESULTS.md` for the latest
results.
