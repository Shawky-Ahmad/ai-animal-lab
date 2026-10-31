#!/usr/bin/env bash
# Reset the workshop database + image catalog to a fresh state.
# The next `./start.sh` re-creates the schema and re-seeds 50 placeholders.
set -euo pipefail
DATA_DIR="$(cd "$(dirname "$0")/.." && pwd)/backend/data"
if [ -d "$DATA_DIR" ]; then
  rm -rf "$DATA_DIR"
  echo "Removed $DATA_DIR — fresh start on next launch."
else
  echo "Nothing to reset ($DATA_DIR does not exist)."
fi
