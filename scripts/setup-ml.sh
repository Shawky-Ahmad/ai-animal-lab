#!/usr/bin/env bash
# Install the ML stack for the Unsupervised Lab (Phase 2) into backend/.venv.
#
# - transformers + numpy from backend/requirements-ml.txt (regular PyPI)
# - torch from PyTorch's CPU wheel index (PyPI torch would pull CUDA deps)
#
# Idempotent: re-running skips/reuses whatever is already installed.
# Usage:  bash scripts/setup-ml.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/backend/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"

if [ ! -x "$PIP" ]; then
  echo "error: no venv found at $VENV" >&2
  echo "       create it first:  python -m venv \"$VENV\"" >&2
  exit 1
fi

echo "==> Using venv: $VENV ($("$PYTHON" --version))"

echo
echo "==> [1/2] Installing transformers + numpy (backend/requirements-ml.txt)"
"$PIP" install -r "$ROOT/backend/requirements-ml.txt"

echo
echo "==> [2/2] Installing CPU torch from the PyTorch wheel index"
echo "    pip install torch --index-url https://download.pytorch.org/whl/cpu"
"$PIP" install torch --index-url https://download.pytorch.org/whl/cpu

echo
echo "==> Verifying the installed stack"
"$PYTHON" - <<'PY'
import numpy, transformers, torch
print(f"    numpy       {numpy.__version__}")
print(f"    transformers {transformers.__version__}")
print(f"    torch       {torch.__version__}")
print(f"    cuda available: {torch.cuda.is_available()} (CPU build expected)")
PY

echo
echo "==> Done. ML stack ready — first embed run downloads facebook/dinov2-small (~90 MB)."
