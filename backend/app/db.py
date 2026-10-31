"""SQLite persistence layer for AI Animal Lab.

Plain `sqlite3` (no ORM). WAL mode. Parameterized queries ONLY. Connections
are short-lived and never shared across requests: every helper opens one via
the `get_conn()` contextmanager (commit on success, rollback on error), and
handlers that need several steps pass one connection through explicitly so a
whole request runs on a single connection/transaction.

Schema is exactly the one in ../API_CONTRACT.md.
"""
from __future__ import annotations

import random
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from .config import settings

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filename TEXT UNIQUE NOT NULL,
  original_name TEXT,
  category TEXT NOT NULL,
  choices TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS votes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  student_id TEXT NOT NULL,
  image_id INTEGER NOT NULL REFERENCES images(id),
  choice TEXT NOT NULL,
  is_correct INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE(student_id, image_id)
);
CREATE TABLE IF NOT EXISTS students (
  student_id TEXT PRIMARY KEY,
  target INTEGER NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_votes_image ON votes(image_id);
"""

# --------------------------------------------------------------------------
# data-dir plumbing (seedable to a custom location, e.g. tests / --data)
# --------------------------------------------------------------------------

_data_dir: Path | None = None


def set_data_dir(path: str | Path) -> None:
    """Point the DB + images folder at a custom directory (before first use)."""
    global _data_dir
    _data_dir = Path(path)


def get_data_dir() -> Path:
    return _data_dir if _data_dir is not None else settings.data_dir


def db_path() -> Path:
    return get_data_dir() / "animal_lab.sqlite3"


def images_dir() -> Path:
    return get_data_dir() / "images"


def utc_now_iso() -> str:
    """UTC ISO-8601, e.g. 2026-10-31T10:00:00+00:00 (clients format locally)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# connections
# --------------------------------------------------------------------------

def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(db_path(), timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


def init_db() -> None:
    """Create DATA_DIR, images/ and the schema (idempotent)."""
    get_data_dir().mkdir(parents=True, exist_ok=True)
    images_dir().mkdir(parents=True, exist_ok=True)
    conn = connect()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """One fresh connection per unit of work; commit/rollback/close handled."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def _use(conn: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    """Yield the caller's connection, or open a throwaway one."""
    if conn is not None:
        yield conn
        return
    with get_conn() as c:
        yield c


# --------------------------------------------------------------------------
# shared helpers
# --------------------------------------------------------------------------

def compute_choices(correct: str, categories: Sequence[str],
                    rng: random.Random | None = None) -> list[str]:
    """[correct] + 2 random distinct other categories, shuffled (server-side)."""
    rng = rng or random.Random()
    others = [c for c in categories if c != correct]
    if len(others) < 2:
        raise RuntimeError("need at least 3 categories to build choices")
    choices = [correct] + rng.sample(others, 2)
    rng.shuffle(choices)
    return choices


# --------------------------------------------------------------------------
# images
# --------------------------------------------------------------------------

def insert_image(filename: str, original_name: str | None, category: str,
                 choices_json: str, conn: sqlite3.Connection | None = None) -> int:
    with _use(conn) as c:
        cur = c.execute(
            "INSERT INTO images (filename, original_name, category, choices, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (filename, original_name, category, choices_json, utc_now_iso()),
        )
        return int(cur.lastrowid)


def get_image(image_id: int, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    with _use(conn) as c:
        return c.execute("SELECT * FROM images WHERE id = ?", (image_id,)).fetchone()


def count_images(conn: sqlite3.Connection | None = None) -> int:
    with _use(conn) as c:
        return int(c.execute("SELECT COUNT(*) FROM images").fetchone()[0])


def image_counts_by_category(conn: sqlite3.Connection | None = None) -> dict[str, int]:
    with _use(conn) as c:
        rows = c.execute(
            "SELECT category, COUNT(*) FROM images GROUP BY category"
        ).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def admin_list_images(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """All images, newest first, with per-image vote counts."""
    with _use(conn) as c:
        rows = c.execute(
            """
            SELECT i.id AS image_id, i.filename, i.original_name, i.category,
                   i.created_at, COUNT(v.id) AS vote_count
            FROM images i LEFT JOIN votes v ON v.image_id = i.id
            GROUP BY i.id
            ORDER BY i.id DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# students / progress
# --------------------------------------------------------------------------

def upsert_student(student_id: str,
                   conn: sqlite3.Connection | None = None) -> sqlite3.Row:
    """Create the student row if new (target fixed at first contact),
    otherwise just bump last_seen. Idempotent."""
    now = utc_now_iso()
    with _use(conn) as c:
        default_target = min(settings.target_per_student, count_images(c))
        c.execute(
            "INSERT OR IGNORE INTO students (student_id, target, first_seen, last_seen) "
            "VALUES (?, ?, ?, ?)",
            (student_id, default_target, now, now),
        )
        if c.execute("SELECT changes()").fetchone()[0] != 1:
            c.execute(
                "UPDATE students SET last_seen = ? WHERE student_id = ?",
                (now, student_id),
            )
        return c.execute(
            "SELECT * FROM students WHERE student_id = ?", (student_id,)
        ).fetchone()


def get_student(student_id: str, conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    with _use(conn) as c:
        return c.execute(
            "SELECT * FROM students WHERE student_id = ?", (student_id,)
        ).fetchone()


def count_labeled(student_id: str, conn: sqlite3.Connection | None = None) -> int:
    with _use(conn) as c:
        return int(
            c.execute(
                "SELECT COUNT(*) FROM votes WHERE student_id = ?", (student_id,)
            ).fetchone()[0]
        )


# --------------------------------------------------------------------------
# next image / votes
# --------------------------------------------------------------------------

def pick_next_image(student_id: str,
                    conn: sqlite3.Connection | None = None) -> sqlite3.Row | None:
    """Next image this student has NOT voted on, biased to the LEAST-labeled
    images globally (balance dataset coverage), random within that tier."""
    with _use(conn) as c:
        return c.execute(
            """
            SELECT i.id, i.filename, i.choices
            FROM images i
            LEFT JOIN (SELECT image_id, COUNT(*) AS cnt FROM votes GROUP BY image_id) vc
                   ON vc.image_id = i.id
            WHERE i.id NOT IN (SELECT image_id FROM votes WHERE student_id = ?)
            ORDER BY COALESCE(vc.cnt, 0) ASC, RANDOM()
            LIMIT 1
            """,
            (student_id,),
        ).fetchone()


def insert_vote(student_id: str, image_id: int, choice: str, is_correct: bool,
                conn: sqlite3.Connection | None = None) -> bool:
    """INSERT OR IGNORE on (student_id, image_id). Returns True if a new row
    was written, False if the vote already existed (never overwritten)."""
    with _use(conn) as c:
        c.execute(
            "INSERT OR IGNORE INTO votes (student_id, image_id, choice, is_correct, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (student_id, image_id, choice, 1 if is_correct else 0, utc_now_iso()),
        )
        return c.execute("SELECT changes()").fetchone()[0] == 1


# --------------------------------------------------------------------------
# exports
# --------------------------------------------------------------------------

def export_votes_rows(conn: sqlite3.Connection | None = None) -> list[sqlite3.Row]:
    with _use(conn) as c:
        return c.execute(
            """
            SELECT v.id, v.student_id, v.image_id, i.filename, i.category,
                   v.choice, v.is_correct, v.created_at
            FROM votes v JOIN images i ON i.id = v.image_id
            ORDER BY v.id
            """
        ).fetchall()


def dataset_rows(conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
    """Per-image aggregate for model training (one dict per image).

    majority_category is empty on tie / no votes; is_tie flags ties (never
    resolved silently); labeled_by = distinct students; student_accuracy =
    % of the image's votes matching the instructor label (empty when no votes).
    """
    with _use(conn) as c:
        images = c.execute(
            "SELECT id, filename, category FROM images ORDER BY id"
        ).fetchall()
        totals: dict[int, sqlite3.Row] = {
            int(r["image_id"]): r
            for r in c.execute(
                "SELECT image_id, COUNT(*) AS total, "
                "COUNT(DISTINCT student_id) AS students, "
                "COALESCE(SUM(is_correct), 0) AS correct "
                "FROM votes GROUP BY image_id"
            )
        }
        per_choice: dict[int, dict[str, int]] = {}
        for image_id, choice, n in c.execute(
            "SELECT image_id, choice, COUNT(*) FROM votes GROUP BY image_id, choice"
        ):
            per_choice.setdefault(int(image_id), {})[choice] = int(n)

    rows: list[dict[str, Any]] = []
    for img in images:
        image_id = int(img["id"])
        t = totals.get(image_id)
        total = int(t["total"]) if t else 0
        counts = per_choice.get(image_id, {})
        majority = ""
        is_tie = False
        if total > 0 and counts:
            top = max(counts.values())
            winners = [ch for ch, n in counts.items() if n == top]
            if len(winners) == 1:
                majority = winners[0]
            else:
                is_tie = True
        rows.append({
            "image_id": image_id,
            "filename": img["filename"],
            "instructor_category": img["category"],
            "total_votes": total,
            "counts": counts,
            "majority_category": majority,
            "is_tie": "true" if is_tie else "false",
            "labeled_by": int(t["students"]) if t else 0,
            "student_accuracy": (
                round(100.0 * int(t["correct"]) / total, 1) if total else ""
            ),
        })
    return rows


# --------------------------------------------------------------------------
# aggregated anonymous stats (public /api/stats/summary == admin /api/admin/summary)
# --------------------------------------------------------------------------

def _minute_buckets(created_at_list: Sequence[str]) -> list[dict[str, Any]]:
    """Per-minute vote buckets, zero-filled from first to last vote."""
    if not created_at_list:
        return []
    counts: dict[str, int] = {}
    for ca in created_at_list:
        key = str(ca)[:16]  # YYYY-MM-DDTHH:MM
        counts[key] = counts.get(key, 0) + 1
    keys = sorted(counts)
    start = datetime.fromisoformat(keys[0] + ":00+00:00")
    end = datetime.fromisoformat(keys[-1] + ":00+00:00")
    out: list[dict[str, Any]] = []
    cur = start
    while cur <= end:
        key = cur.strftime("%Y-%m-%dT%H:%M")
        out.append({"t": key + ":00+00:00", "count": counts.get(key, 0)})
        cur += timedelta(minutes=1)
    return out


def build_summary(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    categories = list(settings.categories)
    with _use(conn) as c:
        total_votes = int(c.execute("SELECT COUNT(*) FROM votes").fetchone()[0])
        student_count = int(
            c.execute("SELECT COUNT(DISTINCT student_id) FROM votes").fetchone()[0]
        )
        images_total = count_images(c)
        images_labeled = int(
            c.execute("SELECT COUNT(DISTINCT image_id) FROM votes").fetchone()[0]
        )

        overall_accuracy = None
        if total_votes:
            correct = int(
                c.execute("SELECT COALESCE(SUM(is_correct), 0) FROM votes").fetchone()[0]
            )
            overall_accuracy = round(100.0 * correct / total_votes, 1)

        votes_over_time = _minute_buckets(
            [r[0] for r in c.execute("SELECT created_at FROM votes ORDER BY created_at")]
        )

        # distribution of student choices across all votes (0-filled for categories)
        dist = {cat: 0 for cat in categories}
        for choice, n in c.execute("SELECT choice, COUNT(*) FROM votes GROUP BY choice"):
            dist[choice] = dist.get(choice, 0) + int(n)

        # accuracy per instructor category (only categories with >= 1 vote)
        per_cat = {cat: {"votes": 0, "correct": 0} for cat in categories}
        for cat, votes, correct in c.execute(
            "SELECT i.category, COUNT(*), COALESCE(SUM(v.is_correct), 0) "
            "FROM votes v JOIN images i ON i.id = v.image_id GROUP BY i.category"
        ):
            if cat in per_cat:
                per_cat[cat] = {"votes": int(votes), "correct": int(correct)}
        category_accuracy = [
            {
                "category": cat,
                "votes": v["votes"],
                "correct": v["correct"],
                "accuracy": round(100.0 * v["correct"] / v["votes"], 1),
            }
            for cat, v in per_cat.items()
            if v["votes"] > 0
        ]

        # most-disagreed images (>= 2 votes), worst accuracy first, top 15
        by_image: dict[int, dict[str, Any]] = {}
        for image_id, total, correct in c.execute(
            "SELECT i.id, COUNT(*), COALESCE(SUM(v.is_correct), 0) "
            "FROM votes v JOIN images i ON i.id = v.image_id "
            "GROUP BY i.id HAVING COUNT(*) >= 2"
        ):
            by_image[int(image_id)] = {
                "total": int(total), "correct": int(correct), "counts": {}
            }
        meta: dict[int, tuple[str, str]] = {}
        if by_image:
            ids = list(by_image)
            qmarks = ",".join("?" * len(ids))
            for image_id, filename, category in c.execute(
                f"SELECT id, filename, category FROM images WHERE id IN ({qmarks})", ids
            ):
                meta[int(image_id)] = (filename, category)
            for image_id, choice, n in c.execute(
                f"SELECT image_id, choice, COUNT(*) FROM votes "
                f"WHERE image_id IN ({qmarks}) GROUP BY image_id, choice",
                ids,
            ):
                by_image[int(image_id)]["counts"][choice] = int(n)

        disagreement = []
        for image_id, info in by_image.items():
            filename, category = meta[image_id]
            disagreement.append({
                "image_id": image_id,
                "filename": filename,
                "instructor_category": category,
                "total_votes": info["total"],
                "counts": info["counts"],
                "accuracy": round(100.0 * info["correct"] / info["total"], 1),
            })
        disagreement.sort(key=lambda d: (d["accuracy"], -d["total_votes"]))
        disagreement = disagreement[:15]

    return {
        "generated_at": utc_now_iso(),
        "total_votes": total_votes,
        "student_count": student_count,
        "images_total": images_total,
        "images_labeled": images_labeled,
        "overall_accuracy": overall_accuracy,
        # session goal for the projected progress bar (config: WORKSHOP_GOAL)
        "workshop": {
            "goal": settings.workshop_goal,
            "labels": total_votes,
            "percent": (
                round(100.0 * total_votes / settings.workshop_goal, 1)
                if settings.workshop_goal > 0 else None
            ),
        },
        "votes_over_time": votes_over_time,
        "label_distribution": dist,
        "category_accuracy": category_accuracy,
        "disagreement": disagreement,
    }
