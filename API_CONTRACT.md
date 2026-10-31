# AI Animal Lab — API Contract v1 (binding for backend + frontends)

Two separate FastAPI apps run in ONE python process (`run.py`):

- **PUBLIC app** — binds `0.0.0.0:$PUBLIC_PORT` (default **8000**). Serves ONLY `/api/*`
  plus the static student site at `/`. This is the only port exposed through Cloudflare Tunnel.
- **ADMIN app** — binds `127.0.0.1:$ADMIN_PORT` (default **8001**). Serves instructor upload
  console at `/`, dashboard at `/dashboard`, admin API under `/api/admin/*`. NEVER exposed publicly.

## Environment / configuration

| Env var | Default | Meaning |
|---|---|---|
| `ANIMAL_LAB_DATA` | `backend/data` | SQLite db + `images/` folder live here |
| `PUBLIC_PORT` | `8000` | public app port |
| `ADMIN_PORT` | `8001` | admin app port |
| `TARGET_PER_STUDENT` | `35` | images assigned per student |
| `MAX_UPLOAD_MB` | `15` | per-file upload cap |
| `MAX_FILES_PER_UPLOAD` | `60` | per-request file cap |

Categories live in `backend/app/config.json`: `{"categories": ["cat","dog","rabbit","bird","fish"]}`.
Minimum 3 categories required (3-choice scheme). Editing the file + restart changes categories.

## Data model (SQLite at `$DATA_DIR/animal_lab.sqlite3`, WAL mode, per-request connections, parameterized queries ONLY)

```sql
CREATE TABLE images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT UNIQUE NOT NULL,        -- server-generated: "<32-hex-uuid>.<ext>", stored in DATA_DIR/images/
  original_name TEXT,                   -- instructor's original filename (display only)
  category TEXT NOT NULL,               -- instructor label (ground truth)
  choices TEXT NOT NULL,                -- JSON array of exactly 3 categories, choices[0..2], incl. correct one
  created_at TEXT NOT NULL              -- UTC ISO-8601
);
CREATE TABLE votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id TEXT NOT NULL,
  image_id INTEGER NOT NULL REFERENCES images(id),
  choice TEXT NOT NULL,                 -- what the student picked
  is_correct INTEGER NOT NULL,          -- computed SERVER-side: choice == images.category
  created_at TEXT NOT NULL,             -- UTC ISO-8601
  UNIQUE(student_id, image_id)          -- one vote per student per image, INSERT OR IGNORE (never updated)
);
CREATE TABLE students (
  student_id TEXT PRIMARY KEY,
  target INTEGER NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
```

- `student_id` must match `^[A-Za-z0-9_-]{6,64}$` (client generates `s-<uuid4>`; server rejects otherwise, 400).
- `is_correct` is NEVER accepted from the client.
- Timestamps stored UTC ISO; clients format to local time.

## PUBLIC endpoints (CORS: `allow_origins=["*"]`, no credentials; no auth)

All bodies JSON unless noted. Errors: FastAPI default `{"detail": "..."}` with 4xx.

### `GET /api/health`
`200 {"ok": true}`

### `GET /api/config`
`200 {"app_name": "AI Animal Lab", "categories": ["cat","dog","rabbit","bird","fish"], "target": 35, "image_count": 50}`

### `POST /api/session`  body `{"student_id": "s-ab12cd34"}`
Creates the student row if new (`target = min(TARGET_PER_STUDENT, active image count)` at first contact;
idempotent afterwards). Updates `last_seen`.
`200 {"student_id": "...", "target": 35, "labeled": 12, "done": false}`

### `GET /api/next?student_id=s-...`
Picks the next image to label: among images this student has NOT voted on, biased toward the
LEAST-labeled images (balance dataset coverage), random within that tier. `choices` is the stored
triple, **shuffled server-side per response**.
- `200 {"done": false, "image_id": 3, "filename": "ab12....jpg", "choices": ["dog","cat","fox"], "labeled": 11, "target": 35}`
- `200 {"done": true, "labeled": 35, "target": 35}`  (no unvoted images remain OR labeled >= target)
- Frontend builds the image URL itself: `API_BASE + "/api/images/" + image_id + "/file"`.

### `POST /api/vote`  body `{"student_id": "s-...", "image_id": 3, "choice": "cat"}`
Validates: student_id pattern, image exists, choice ∈ that image's `choices`. Inserts with
`INSERT OR IGNORE` on `(student_id, image_id)` — a repeated vote never overwrites or duplicates.
- `200 {"ok": true, "duplicate": false, "labeled": 12, "target": 35}` (`duplicate: true` if row already existed)
- `400` invalid student_id / unknown image / choice not in image choices

### `GET /api/progress?student_id=s-...`
`200 {"labeled": 12, "target": 35, "done": false}`

### `GET /api/images/{image_id}/file`
Serves the image bytes from `DATA_DIR/images/<filename>` (filename looked up BY ID in the DB —
no client-supplied paths ever touch the filesystem). `404` unknown id. Correct MIME type.

### `GET /api/stats/summary`
Aggregated, anonymous. Shape (used by dashboard):

```json
{
  "generated_at": "2026-10-31T10:00:00+00:00",
  "total_votes": 120,
  "student_count": 8,
  "images_total": 50,
  "images_labeled": 41,
  "overall_accuracy": 83.3,
  "votes_over_time": [{"t": "2026-10-31T09:58:00+00:00", "count": 7}],
  "label_distribution": {"cat": 30, "dog": 25, "rabbit": 20, "bird": 22, "fish": 23},
  "category_accuracy": [
    {"category": "cat", "votes": 30, "correct": 27, "accuracy": 90.0}
  ],
  "disagreement": [
    {"image_id": 3, "filename": "ab12....jpg", "instructor_category": "cat",
     "total_votes": 9, "counts": {"cat": 4, "dog": 3, "rabbit": 2}, "accuracy": 44.4}
  ]
}
```

- `overall_accuracy`: null when 0 votes. `votes_over_time`: per-minute buckets, zero-filled from first to last vote.
- `category_accuracy`: one entry per configured category that has ≥1 vote on its images; accuracy = share of votes on that category's images where `choice == category`.
- `disagreement`: images with ≥2 votes, sorted by accuracy ascending then votes descending, top 15.

## ADMIN endpoints (admin app only, same-origin, no CORS needed)

### Pages
- `GET /` → instructor upload console (static)
- `GET /dashboard` → statistics dashboard (static)

### API
- `GET /api/admin/config` → same shape as public `/api/config` (same-origin mirror so the console can read categories)
- `GET /api/admin/summary` → identical shape to `/api/stats/summary`
- `POST /api/admin/upload` — multipart: field `category` (must be in configured categories) + field
  `files` (repeatable). Each file: jpg/jpeg/png/webp, magic bytes verified, ≤ `MAX_UPLOAD_MB`,
  ≤ `MAX_FILES_PER_UPLOAD` files/request. Server generates uuid filename, computes `choices`
  (correct category + 2 random distinct other categories) and inserts.
  `200 {"uploaded": [{"image_id": 51, "filename": "...", "category": "cat"}],
        "failed": [{"name": "notes.txt", "reason": "not a jpg/png/webp image"}]}`
- `GET /api/admin/images` →
  `{"counts": {"cat": 10, "dog": 10}, "total": 50,
    "images": [{"image_id": 1, "filename": "...", "original_name": "rabbit1.jpg",
                "category": "rabbit", "created_at": "...", "vote_count": 3}]}`
  (images sorted newest first)
- `GET /api/admin/files/{filename}` → serves preview; filename must match `^[a-f0-9]{32}\.(jpg|jpeg|png|webp)$` (no traversal possible)
- `GET /api/admin/export/votes.csv` → `text/csv` attachment:
  `vote_id,student_id,image_id,filename,instructor_category,student_choice,is_correct,created_at`
- `GET /api/admin/export/dataset.csv` → per-image aggregate for future model training:
  `image_id,filename,instructor_category,total_votes,vote_cat,vote_dog,...(one column per category),majority_category,is_tie,labeled_by,student_accuracy`
  - `majority_category` EMPTY when tie or no votes; `is_tie` = `true`/`false` string. Ties are flagged, never resolved silently.
  - `student_accuracy` = % of votes matching instructor label (empty when no votes).

## Static hosting

- PUBLIC app mounts `frontend/student` at `/` (html=True) → local dev + "open tunnel URL directly" both work with `API_BASE=""`.
- ADMIN app mounts `frontend/admin` at `/` (html=True) with `/dashboard` → `dashboard.html`.

## Frontend `config.js` (student site)

```js
window.ANIMAL_LAB_CONFIG = { API_BASE: "" };  // "" = same origin (local / tunnel URL).
                                              // GitHub Pages: set to "https://<tunnel-host>" (no trailing slash).
```
`?api=https://...` query param overrides config (useful for testing). All fetches go through
`API_BASE + "/api/..."` with a 10s AbortController timeout.
