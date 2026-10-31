#!/usr/bin/env python3
"""AI Animal Lab — single-process launcher.

Starts the ADMIN FastAPI app (127.0.0.1:$ADMIN_PORT) in a daemon thread and
the PUBLIC FastAPI app (0.0.0.0:$PUBLIC_PORT) in the main thread. Optionally
exposes the public app through a cloudflared quick tunnel (--tunnel).

Usage:
    python run.py [--no-seed] [--tunnel]
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import uvicorn  # noqa: E402

from app import admin_app, db, public_app  # noqa: E402
from app.config import settings  # noqa: E402
from app.seed_placeholders import seed_if_empty  # noqa: E402

TUNNEL_URL_RE = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")


def find_cloudflared() -> Path | None:
    candidates = [
        BACKEND_DIR / "bin" / "cloudflared",
        BACKEND_DIR.parent / "bin" / "cloudflared",  # <project>/bin/cloudflared
    ]
    for cand in candidates:
        if cand.is_file() and cand.stat().st_mode & 0o111:
            return cand
    found = shutil.which("cloudflared")
    return Path(found) if found else None


def start_tunnel(public_port: int, data_dir: Path, label: str = "public",
                 url_file: str = "tunnel_url.txt") -> subprocess.Popen | None:
    binary = find_cloudflared()
    if binary is None:
        print("[tunnel] cloudflared not found (backend/bin, project/bin, PATH) "
              "— continuing without tunnel")
        return None
    cmd = [str(binary), "tunnel", "--url", f"http://127.0.0.1:{public_port}",
           "--no-autoupdate"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    announced = threading.Event()

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(f"[cloudflared:{label}] " + line)
            sys.stdout.flush()
            if not announced.is_set():
                match = TUNNEL_URL_RE.search(line)
                if match:
                    url = match.group(0)
                    announced.set()
                    try:
                        (data_dir / url_file).write_text(url + "\n",
                                                         encoding="utf-8")
                    except OSError as exc:
                        print(f"[tunnel] could not write {url_file}: {exc}")
                    print()
                    print("#" * 66)
                    print("#" + " " * 12 + f"{label.upper()} TUNNEL URL:")
                    print("#" + " " * 12 + url)
                    print("#" * 66)
                    print()

    threading.Thread(target=pump, daemon=True, name=f"cloudflared-{label}").start()
    return proc


def stop_tunnel(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def main() -> None:
    parser = argparse.ArgumentParser(description="AI Animal Lab server")
    parser.add_argument("--no-seed", action="store_true",
                        help="skip placeholder image seeding on startup")
    parser.add_argument("--tunnel", action="store_true",
                        help="expose the public app via a cloudflared quick tunnel")
    args = parser.parse_args()

    db.init_db()
    seeded = 0
    if not args.no_seed:
        seeded = seed_if_empty()
    image_count = db.count_images()

    tunnel_proc = None
    admin_tunnel_proc = None
    if args.tunnel:
        tunnel_proc = start_tunnel(settings.public_port, settings.data_dir,
                                   label="students")
        if settings.admin_pin:
            admin_tunnel_proc = start_tunnel(
                settings.admin_port, settings.data_dir,
                label="instructor (PIN-protected)", url_file="admin_tunnel_url.txt")

    # ---- admin app: daemon thread; localhost unless ADMIN_PIN opened it up --
    admin_server = uvicorn.Server(uvicorn.Config(
        admin_app.app, host=settings.admin_host, port=settings.admin_port,
        log_level="warning",
    ))
    admin_thread = threading.Thread(target=admin_server.run,
                                    name="admin-uvicorn", daemon=True)
    admin_thread.start()
    for _ in range(100):  # wait until the admin app is accepting connections
        if admin_server.started:
            break
        time.sleep(0.1)

    print()
    print("=" * 66)
    print("  AI Animal Lab")
    print("=" * 66)
    print(f"  Public app : http://localhost:{settings.public_port}"
          f"   (bound 0.0.0.0:{settings.public_port})")
    if settings.admin_pin:
        print(f"  Admin app  : http://localhost:{settings.admin_port}/"
              f"  (PIN required — reachable from other devices too,"
              f" bound {settings.admin_host}:{settings.admin_port})")
    else:
        print(f"  Admin app  : http://127.0.0.1:{settings.admin_port}/  (upload console)")
    print(f"               http://127.0.0.1:{settings.admin_port}/dashboard")
    print(f"  Data dir   : {settings.data_dir}")
    print(f"  Images     : {image_count}"
          + (f"  (+{seeded} placeholders seeded now)" if seeded else ""))
    print(f"  Categories : {', '.join(settings.categories)}")
    print("=" * 66)
    if args.tunnel:
        print("  Tunnels    : starting (URLs will be printed when ready)")
        if not settings.admin_pin:
            print("  (tip: ADMIN_PIN=1234 ./start.sh --tunnel also exposes the")
            print("   instructor dashboard on a second, PIN-protected URL)")
    print()
    sys.stdout.flush()

    # ---- public app: main thread on 0.0.0.0:$PUBLIC_PORT -------------------
    public_server = uvicorn.Server(uvicorn.Config(
        public_app.app, host="0.0.0.0", port=settings.public_port,
        log_level="info",
    ))
    try:
        public_server.run()  # installs SIGINT/SIGTERM handlers, blocks here
    except KeyboardInterrupt:
        pass
    finally:
        admin_server.should_exit = True
        admin_thread.join(timeout=5)
        stop_tunnel(tunnel_proc)
        stop_tunnel(admin_tunnel_proc)
        print("[shutdown] public + admin servers and tunnels stopped.")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
