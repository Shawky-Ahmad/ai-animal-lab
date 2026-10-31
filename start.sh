#!/usr/bin/env bash
# AI Animal Lab — one-command launcher for the instructor laptop.
#
#   ./start.sh            start public API (0.0.0.0:8000) + admin console (127.0.0.1:8001)
#   ./start.sh --tunnel   also open a cloudflared quick tunnel and print the public URL
#   ./start.sh --no-seed  don't auto-seed placeholder images into an empty database
#
# Environment: ADMIN_PIN (protect + expose the instructor tools),
#              WORKSHOP_GOAL (dashboard progress target), TARGET_PER_STUDENT.
# Ctrl+C stops everything (servers + tunnel).
set -euo pipefail
cd "$(dirname "$0")/backend"

if [ ! -x .venv/bin/python ]; then
  echo "[setup] creating Python virtual environment..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet --upgrade pip
fi
.venv/bin/pip install --quiet -r requirements.txt

# cloudflared is not stored in git (keeps the clone light); fetch it on demand
if [ "${1:-}" = "--tunnel" ] || [ "${2:-}" = "--tunnel" ]; then
  if [ ! -x ../bin/cloudflared ]; then
    echo "[setup] downloading cloudflared..."
    mkdir -p ../bin
    if [ "$(uname -s)" = "Linux" ] && [ "$(uname -m)" = "x86_64" ]; then
      curl -sL -o ../bin/cloudflared \
        https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
      chmod +x ../bin/cloudflared
    else
      echo "[setup] automatic cloudflared download supports Linux x86_64 only —"
      echo "        install cloudflared manually or run without --tunnel."
      exit 1
    fi
  fi
fi

exec .venv/bin/python run.py "$@"
