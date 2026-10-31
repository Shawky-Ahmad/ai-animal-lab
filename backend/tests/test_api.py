#!/usr/bin/env python3
"""AI Animal Lab — integration test suite (workshop spec tests A-G + I).

Launches a PRIVATE server instance (fresh temp data dir, ports 8820/8821,
MAX_UPLOAD_MB=1, MAX_FILES_PER_UPLOAD=12), exercises the public + admin APIs,
and verifies votes land in SQLite with correct statistics math.

Run:  backend/tests $  ../.venv/bin/python test_api.py
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import requests
from PIL import Image

BACKEND = Path(__file__).resolve().parents[1]
PROJECT = BACKEND.parent
PYTHON = BACKEND / ".venv" / "bin/python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)

PUBLIC_PORT, ADMIN_PORT = 8820, 8821
PUB = f"http://127.0.0.1:{PUBLIC_PORT}"
ADM = f"http://127.0.0.1:{ADMIN_PORT}"
DATA_DIR = Path(f"/tmp/al-test-{int(time.time())}")

results: list[tuple[str, bool, str]] = []

# set by test_e_statistics, reused by test_f_export
CONTROL = {"image_id": None, "truth": None}


def check(section: str, name: str, ok: bool, detail: str = "") -> bool:
    results.append((f"{section} · {name}", ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  — {detail}" if detail else ""))
    return ok


def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DATA_DIR / "animal_lab.sqlite3")
    conn.row_factory = sqlite3.Row
    return conn


def jpg_bytes(w: int = 320, h: int = 240, color=(180, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG", quality=85)
    return buf.getvalue()


def png_bytes(w: int = 320, h: int = 240, color=(90, 160, 220)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "PNG")
    return buf.getvalue()


def sid(prefix: str) -> str:
    return f"s-{prefix}-{uuid.uuid4().hex[:8]}"


def vote(student: str, image_id: int, choice: str):
    return requests.post(f"{PUB}/api/vote", json={
        "student_id": student, "image_id": image_id, "choice": choice}, timeout=10)


def next_image(student: str) -> dict:
    r = requests.get(f"{PUB}/api/next", params={"student_id": student}, timeout=10)
    r.raise_for_status()
    return r.json()


def label_loop(student: str, n: int) -> list[int]:
    """Vote n times taking the first offered choice; returns the image ids."""
    labeled_ids = []
    for _ in range(n):
        nxt = next_image(student)
        if nxt.get("done"):
            break
        labeled_ids.append(nxt["image_id"])
        vote(student, nxt["image_id"], nxt["choices"][0])
    return labeled_ids


def upload(category: str, files: list[tuple[str, bytes]]):
    data = [("files", (name, payload, "image/jpeg")) for name, payload in files]
    return requests.post(f"{ADM}/api/admin/upload",
                         data={"category": category}, files=data, timeout=30)


# --------------------------------------------------------------------------
# sections
# --------------------------------------------------------------------------

def test_a_image_loading():
    print("\n=== Test A — Image loading ===")
    cfg = requests.get(f"{PUB}/api/config", timeout=10).json()
    check("A", "config lists categories + image_count",
          len(cfg["categories"]) == 5 and cfg["image_count"] == 50,
          f"categories={cfg['categories']} count={cfg['image_count']}")

    nxt = next_image(sid("a-probe"))
    r = requests.get(f"{PUB}/api/images/{nxt['image_id']}/file", timeout=10)
    ok = (r.status_code == 200 and r.headers["content-type"] == "image/jpeg")
    check("A", "image served by id with correct MIME", ok,
          f"HTTP {r.status_code} type={r.headers.get('content-type')}")
    if ok:
        img = Image.open(io.BytesIO(r.content))
        check("A", "payload is a decodable 640x640 JPEG",
              img.size == (640, 640) and img.format == "JPEG", f"{img.size} {img.format}")

    check("A", "unknown image id -> 404",
          requests.get(f"{PUB}/api/images/999999/file", timeout=10).status_code == 404)
    check("A", "/api/next payload has 3 choices + progress fields",
          len(nxt["choices"]) == 3 and len(set(nxt["choices"])) == 3
          and "labeled" in nxt and "target" in nxt,
          f"choices={nxt['choices']}")


def test_b_submission():
    print("\n=== Test B — Submission saved to SQLite ===")
    s = sid("b-voter")
    requests.post(f"{PUB}/api/session", json={"student_id": s}, timeout=10)
    nxt = next_image(s)
    chosen = nxt["choices"][1]  # deliberately not choices[0]
    resp = vote(s, nxt["image_id"], chosen).json()
    check("B", "vote accepted, labeled advanced",
          resp["ok"] is True and resp["duplicate"] is False and resp["labeled"] == 1,
          json.dumps(resp))

    with db_connect() as conn:
        row = conn.execute(
            "SELECT v.choice, v.is_correct, i.category, i.filename "
            "FROM votes v JOIN images i ON i.id = v.image_id "
            "WHERE v.student_id = ? AND v.image_id = ?", (s, nxt["image_id"])).fetchone()
    ok = (row is not None and row["choice"] == chosen
          and row["is_correct"] == (1 if chosen == row["category"] else 0))
    check("B", "row exists in SQLite with correct choice + is_correct",
          ok, f"choice={row['choice'] if row else None} "
              f"is_correct={row['is_correct'] if row else None} "
              f"truth={row['category'] if row else None}")

    dup = vote(s, nxt["image_id"], nxt["choices"][0]).json()
    with db_connect() as conn:
        n = conn.execute("SELECT COUNT(*) FROM votes WHERE student_id=? AND image_id=?",
                         (s, nxt["image_id"])).fetchone()[0]
    check("B", "duplicate vote ignored, original preserved",
          dup["duplicate"] is True and n == 1, f"rows={n} resp={dup}")


def test_c_multiple_students():
    print("\n=== Test C — Multiple independent students ===")
    students = [sid(f"c-s{i}") for i in range(4)]
    per_student = {}
    for s in students:
        per_student[s] = label_loop(s, 5)

    with db_connect() as conn:
        rows = conn.execute(
            "SELECT student_id, image_id, choice FROM votes WHERE student_id IN "
            f"({','.join('?' * 4)})", students).fetchall()
        per_image = dict(conn.execute(
            "SELECT image_id, COUNT(*) FROM votes WHERE image_id IN "
            "(SELECT image_id FROM votes WHERE student_id IN "
            f"({','.join('?' * 4)})) GROUP BY image_id", students).fetchall())
    check("C", "4 students x 5 votes = 20 independent rows", len(rows) == 20,
          f"rows={len(rows)}")
    ok = all(sum(1 for r in rows if r["student_id"] == s) == 5 for s in students)
    check("C", "each student has exactly 5 votes", ok)
    ok = all(per_image[i] >= 1 for i in per_image) and len(per_image) >= 5
    check("C", "same images shared across students (independent votes counted)",
          ok, f"distinct images voted={len(per_image)}")
    combos = {(r["student_id"], r["image_id"]) for r in rows}
    check("C", "no student voted the same image twice", len(combos) == len(rows))


def test_d_progress():
    print("\n=== Test D — Progress + refresh persistence ===")
    s = sid("d-runner")
    requests.post(f"{PUB}/api/session", json={"student_id": s}, timeout=10)
    seen = label_loop(s, 12)
    check("D", "12 consecutive images, no repeats", len(seen) == 12 and len(set(seen)) == 12)

    again = requests.post(f"{PUB}/api/session", json={"student_id": s}, timeout=10).json()
    check("D", "refresh (new session call) restores labeled=12",
          again["labeled"] == 12 and again["done"] is False, json.dumps(again))

    prog = requests.get(f"{PUB}/api/progress", params={"student_id": s}, timeout=10).json()
    check("D", "progress endpoint agrees", prog == {"labeled": 12, "target": 35, "done": False},
          json.dumps(prog))

    nxt = next_image(s)
    check("D", "next image is new after refresh", nxt["done"] is False
          and nxt["image_id"] not in seen)

    s2 = sid("d-exhaust")
    requests.post(f"{PUB}/api/session", json={"student_id": s2}, timeout=10)
    done_at = None
    for i in range(60):
        nxt = next_image(s2)
        if nxt.get("done"):
            done_at = nxt["labeled"]
            break
        vote(s2, nxt["image_id"], nxt["choices"][0])
    check("D", "completion triggers when target reached (35)", done_at == 35,
          f"done at labeled={done_at}")


def test_e_statistics():
    print("\n=== Test E — Statistics math (controlled votes) ===")
    with db_connect() as conn:
        before = requests.get(f"{ADM}/api/admin/summary", timeout=10).json()
        img = conn.execute(
            "SELECT i.id, i.category FROM images i "
            "LEFT JOIN votes v ON v.image_id = i.id "
            "GROUP BY i.id HAVING COUNT(v.id) = 0 LIMIT 1").fetchone()
    if img is None:
        # earlier tests exhausted the pool — upload a fresh control image
        up = upload("cat", [("e-control.jpg", jpg_bytes())]).json()
        with db_connect() as conn:
            cat_row = conn.execute("SELECT category FROM images WHERE id=?",
                                   (up["uploaded"][0]["image_id"],)).fetchone()
        img = {"id": up["uploaded"][0]["image_id"], "category": cat_row["category"]}
        before = requests.get(f"{ADM}/api/admin/summary", timeout=10).json()
    total0, correct0 = before["total_votes"], 0
    for cat in before["category_accuracy"]:
        if cat["category"] == img["category"]:
            total0, correct0 = cat["votes"], cat["correct"]

    truth = img["category"]
    with db_connect() as conn:
        triple = json.loads(conn.execute(
            "SELECT choices FROM images WHERE id=?", (img["id"],)).fetchone()[0])
    wrong = next(c for c in triple if c != truth)  # must come from THIS image's choice set
    pattern = [truth, wrong, truth, truth, wrong]  # 3 correct / 5
    voters = [sid(f"e-v{i}") for i, choice in enumerate(pattern)]
    for s, choice in zip(voters, pattern):
        requests.post(f"{PUB}/api/session", json={"student_id": s}, timeout=10)
        resp = vote(s, img["id"], choice)
        assert resp.status_code == 200, f"controlled vote rejected: {resp.text}"
    CONTROL["image_id"], CONTROL["truth"] = img["id"], truth

    s = requests.get(f"{ADM}/api/admin/summary", timeout=10).json()
    check("E", "total_votes +5", s["total_votes"] == before["total_votes"] + 5,
          f"{before['total_votes']} -> {s['total_votes']}")
    check("E", "student_count counts distinct voters",
          s["student_count"] == before["student_count"] + 5,
          f"{before['student_count']} -> {s['student_count']}")
    exp_acc = round(100.0 * (correct0 + 3) / (total0 + 5), 1)
    cat = next((c for c in s["category_accuracy"] if c["category"] == truth), None)
    check("E", f"accuracy for '{truth}' = {exp_acc} (3 correct / 5)",
          cat is not None and cat["accuracy"] == exp_acc and cat["votes"] == total0 + 5
          and cat["correct"] == correct0 + 3,
          json.dumps(cat))

    exp_overall = round(100.0 * (sum(c["correct"] for c in s["category_accuracy"]))
                        / s["total_votes"], 1)
    check("E", "overall_accuracy consistent with per-category sums",
          s["overall_accuracy"] == exp_overall,
          f"{s['overall_accuracy']} == {exp_overall}")

    with db_connect() as conn:
        rows = conn.execute(
            "SELECT choice, is_correct FROM votes WHERE image_id=? ORDER BY id",
            (img["id"],)).fetchall()
    check("E", "all 5 controlled votes stored individually",
          [r["choice"] for r in rows] == pattern
          and [r["is_correct"] for r in rows] == [1 if c == truth else 0 for c in pattern],
          f"choices={[r['choice'] for r in rows]}")

    buckets = s["votes_over_time"]
    check("E", "votes_over_time buckets sum to total_votes",
          sum(b["count"] for b in buckets) == s["total_votes"],
          f"buckets={len(buckets)} sum={sum(b['count'] for b in buckets)}")
    check("E", "workshop goal defaults to 25 x target (875) with sane percent",
          s["workshop"]["goal"] == 875 and s["workshop"]["labels"] == s["total_votes"]
          and s["workshop"]["percent"] == round(100.0 * s["total_votes"] / 875, 1),
          json.dumps(s["workshop"]))

    # independently recompute the top-15 disagreement list from SQLite
    with db_connect() as conn:
        totals = {
            int(r["image_id"]): {"filename": r["filename"], "category": r["category"],
                                 "total": r["total"], "correct": r["correct"],
                                 "counts": {}}
            for r in conn.execute(
                "SELECT i.id AS image_id, i.filename, i.category, COUNT(*) AS total, "
                "SUM(v.is_correct) AS correct FROM votes v JOIN images i ON i.id = v.image_id "
                "GROUP BY i.id HAVING COUNT(*) >= 2")}
        for image_id, choice, n in conn.execute(
                "SELECT image_id, choice, COUNT(*) FROM votes GROUP BY image_id, choice"):
            if int(image_id) in totals:
                totals[int(image_id)]["counts"][choice] = int(n)
    expected = []
    for image_id, t in totals.items():
        expected.append({
            "image_id": image_id, "filename": t["filename"],
            "instructor_category": t["category"], "total_votes": t["total"],
            "counts": t["counts"],
            "accuracy": round(100.0 * t["correct"] / t["total"], 1)})
    expected.sort(key=lambda d: (d["accuracy"], -d["total_votes"]))
    expected = expected[:15]
    check("E", "disagreement list exactly matches independent top-15 recomputation",
          s["disagreement"] == expected,
          f"api={len(s['disagreement'])} expected={len(expected)}")
    ctrl_in = next((d for d in s["disagreement"]
                    if d["image_id"] == CONTROL["image_id"]), None)
    if ctrl_in:
        check("E", "controlled image split shown correctly where ranked",
              ctrl_in["counts"].get(truth) == 3 and ctrl_in["counts"].get(wrong) == 2
              and ctrl_in["accuracy"] == 60.0, json.dumps(ctrl_in))
    check("E", "disagreement sorted by ascending accuracy",
          all(a["accuracy"] <= b["accuracy"]
              for a, b in zip(s["disagreement"], s["disagreement"][1:])))


def test_f_export():
    print("\n=== Test F — CSV exports ===")
    with db_connect() as conn:
        votes_db = conn.execute(
            "SELECT v.id, v.student_id, v.image_id, i.filename, i.category, "
            "v.choice, v.is_correct, v.created_at FROM votes v "
            "JOIN images i ON i.id = v.image_id ORDER BY v.id").fetchall()

    csv_text = requests.get(f"{ADM}/api/admin/export/votes.csv", timeout=10).text
    rows = list(csv.reader(io.StringIO(csv_text)))
    check("F", "votes.csv header exact",
          rows[0] == ["vote_id", "student_id", "image_id", "filename",
                      "instructor_category", "student_choice", "is_correct", "created_at"],
          str(rows[0]))
    check("F", "votes.csv row count == votes in SQLite",
          len(rows) - 1 == len(votes_db), f"csv={len(rows) - 1} db={len(votes_db)}")
    v0 = votes_db[0]
    check("F", "first CSV row matches DB values verbatim",
          rows[1] == [str(v0["id"]), v0["student_id"], str(v0["image_id"]),
                      v0["filename"], v0["category"], v0["choice"],
                      str(v0["is_correct"]), v0["created_at"]], str(rows[1]))

    # tie scenario: fresh image, 2 students disagree 1-1
    up = upload("cat", [("tie-test.jpg", jpg_bytes())]).json()
    tie_img = up["uploaded"][0]["image_id"]
    with db_connect() as conn:
        triple = json.loads(conn.execute(
            "SELECT choices FROM images WHERE id=?", (tie_img,)).fetchone()[0])
    other = next(c for c in triple if c != "cat")
    st1, st2 = sid("f-tie1"), sid("f-tie2")
    for st, ch in ((st1, "cat"), (st2, other)):
        requests.post(f"{PUB}/api/session", json={"student_id": st}, timeout=10)
        resp = vote(st, tie_img, ch)
        assert resp.status_code == 200, f"tie vote rejected: {resp.text}"

    ds_text = requests.get(f"{ADM}/api/admin/export/dataset.csv", timeout=10).text
    ds = list(csv.DictReader(io.StringIO(ds_text)))
    cats = requests.get(f"{PUB}/api/config", timeout=10).json()["categories"]
    header = list(ds[0].keys())
    check("F", "dataset.csv has one vote_<cat> column per category",
          all(f"vote_{c}" in header for c in cats), str(header))
    tie_row = next(r for r in ds if int(r["image_id"]) == tie_img)
    check("F", "tie flagged: is_tie=true, majority empty, split 1/1",
          tie_row["is_tie"] == "true" and tie_row["majority_category"] == ""
          and tie_row["vote_cat"] == "1" and tie_row[f"vote_{other}"] == "1"
          and tie_row["student_accuracy"] == "50.0",
          str({k: tie_row[k] for k in
               ("total_votes", "vote_cat", f"vote_{other}", "majority_category",
                "is_tie", "student_accuracy")}))
    win_row = next(r for r in ds if int(r["image_id"]) == CONTROL["image_id"])
    check("F", "clear winner: majority=truth, is_tie=false",
          win_row["majority_category"] == CONTROL["truth"] and win_row["is_tie"] == "false"
          and win_row["total_votes"] == "5", str({k: win_row[k] for k in
              ("total_votes", "majority_category", "is_tie")}))


def test_g_bulk_upload():
    print("\n=== Test G — Bulk upload + validation ===")
    with db_connect() as conn:
        dogs_before = conn.execute(
            "SELECT COUNT(*) FROM images WHERE category='dog'").fetchone()[0]
        total_before = conn.execute("SELECT COUNT(*) FROM images").fetchone()[0]
    batch = [(f"dog-{i:02d}.jpg", jpg_bytes(color=(i * 20 % 255, 100, 150)))
             for i in range(6)] + [(f"pup-{i}.png", png_bytes()) for i in range(2)]
    r = upload("dog", batch).json()
    check("G", "8 mixed jpg/png files uploaded in one request",
          len(r["uploaded"]) == 8 and not r["failed"], json.dumps(r["failed"]))

    inv = requests.get(f"{ADM}/api/admin/images", timeout=10).json()
    check("G", "per-category counts updated (dog +8, total +8)",
          inv["counts"]["dog"] == dogs_before + 8 and inv["total"] == total_before + 8,
          f"counts={inv['counts']} total={inv['total']}")
    check("G", "new uploads listed with original names",
          any(i["original_name"] == "dog-00.jpg" for i in inv["images"]),
          str(inv["images"][0]["original_name"]))

    fresh = sid("g-fresh")
    got_new = False
    for _ in range(15):
        nxt = next_image(fresh)
        if nxt.get("done"):
            break
        if nxt["image_id"] in {u["image_id"] for u in r["uploaded"]}:
            got_new = True
            break
        vote(fresh, nxt["image_id"], nxt["choices"][0])
    check("G", "fresh students are served the newly uploaded images", got_new)

    bad = upload("dog", [("notes.txt", b"hello world"),
                         ("fake.jpg", b"\x00\x01garbage-not-a-jpeg" * 50),
                         ("big.jpg", jpg_bytes() + b"\xff" * (2 * 1024 * 1024))])
    failed = {f["name"]: f["reason"] for f in bad.json()["failed"]}
    check("G", "wrong extension rejected", "notes.txt" in failed, failed.get("notes.txt", ""))
    check("G", "bad magic bytes rejected", "fake.jpg" in failed, failed.get("fake.jpg", ""))
    check("G", "oversized file (>MAX_UPLOAD_MB=1) rejected", "big.jpg" in failed,
          failed.get("big.jpg", ""))

    many = upload("dog", [(f"m{i}.jpg", jpg_bytes()) for i in range(13)])
    check("G", "13th file rejected (MAX_FILES_PER_UPLOAD=12)",
          len(many.json()["uploaded"]) == 12 and len(many.json()["failed"]) == 1)

    dragon = upload("dragon", [("d.jpg", jpg_bytes())]).json()
    check("G", "invalid category rejected for every file",
          not dragon["uploaded"] and "invalid category" in dragon["failed"][0]["reason"],
          dragon["failed"][0]["reason"] if dragon["failed"] else "")


def test_i_security():
    print("\n=== Test I — Basic security ===")
    cases = [
        ("GET", f"{PUB}/dashboard", None, "admin page hidden from public port"),
        ("GET", f"{PUB}/api/admin/summary", None, "admin summary hidden from public port"),
        ("POST", f"{PUB}/api/admin/upload", {"category": "cat"}, "upload hidden from public port"),
        ("GET", f"{PUB}/api/admin/export/votes.csv", None, "exports hidden from public port"),
    ]
    for method, url, body, name in cases:
        r = requests.request(method, url, json=body, timeout=10)
        # 404 (no such route) or 405 (static mount rejects the method) both prove
        # the admin capability is not reachable on the public port
        check("I", name, r.status_code in (404, 405), f"HTTP {r.status_code}")

    r = requests.get(f"{ADM}/api/admin/files/..%2F..%2Fanimal_lab.sqlite3", timeout=10)
    check("I", "path traversal on file preview blocked", r.status_code == 404,
          f"HTTP {r.status_code}")
    r = requests.get(f"{ADM}/api/admin/files/animal_lab.sqlite3", timeout=10)
    check("I", "non-image files not served by preview route", r.status_code == 404)

    r = requests.get(f"{PUB}/api/images/not-an-int/file", timeout=10)
    check("I", "non-integer image id rejected", r.status_code in (404, 422),
          f"HTTP {r.status_code}")

    r = requests.post(f"{PUB}/api/session", json={"student_id": "ab"}, timeout=10)
    check("I", "malformed student_id rejected", r.status_code == 400)

    s = sid("i-tamper")
    requests.post(f"{PUB}/api/session", json={"student_id": s}, timeout=10)
    nxt = next_image(s)
    r = vote(s, nxt["image_id"], "dragon")
    check("I", "vote with choice outside image choices rejected",
          r.status_code == 400, f"HTTP {r.status_code}")

    truth = requests.get(f"{ADM}/api/admin/summary", timeout=10)  # noqa: F841
    with db_connect() as conn:
        row = conn.execute("SELECT category FROM images WHERE id=?",
                           (nxt["image_id"],)).fetchone()
    wrong = next(c for c in nxt["choices"] if c != row["category"])
    resp = requests.post(f"{PUB}/api/vote", json={
        "student_id": s, "image_id": nxt["image_id"], "choice": wrong,
        "is_correct": 1}, timeout=10).json()
    with db_connect() as conn:
        rec = conn.execute("SELECT is_correct FROM votes WHERE student_id=? AND image_id=?",
                           (s, nxt["image_id"])).fetchone()
    check("I", "client-supplied is_correct ignored (stored 0)",
          resp["ok"] is True and rec["is_correct"] == 0, f"is_correct={rec['is_correct']}")

    r = requests.get(f"{PUB}/api/health", headers={"Origin": "https://example.com"},
                     timeout=10)
    check("I", "CORS allow-origin present for cross-origin clients",
          r.headers.get("access-control-allow-origin") == "*")


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------

def main() -> int:
    proc = subprocess.Popen(
        [str(PYTHON), str(BACKEND / "run.py")],
        env={
            **__import__("os").environ,
            "ANIMAL_LAB_DATA": str(DATA_DIR),
            "PUBLIC_PORT": str(PUBLIC_PORT),
            "ADMIN_PORT": str(ADMIN_PORT),
            "MAX_UPLOAD_MB": "1",
            "MAX_FILES_PER_UPLOAD": "12",
        },
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(120):
            try:
                if requests.get(f"{PUB}/api/health", timeout=2).json().get("ok"):
                    break
            except requests.RequestException:
                time.sleep(0.5)
        else:
            print("server never became healthy")
            return 2
        print(f"server up — data dir {DATA_DIR}")

        test_a_image_loading()
        test_b_submission()
        test_c_multiple_students()
        test_d_progress()
        test_e_statistics()
        test_f_export()
        test_g_bulk_upload()
        test_i_security()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    failed = [r for r in results if not r[1]]
    print("\n" + "=" * 64)
    print(f"RESULTS: {len(results) - len(failed)}/{len(results)} passed")
    for name, ok, detail in failed:
        print(f"  FAIL: {name}  —  {detail}")
    print("=" * 64)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
