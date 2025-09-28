#!/usr/bin/env bash
set -euo pipefail

# Always run from repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${SCRIPT_DIR}"

if command -v uv >/dev/null 2>&1; then
  echo "[run_demo] Detected 'uv'; ensuring dependencies are synced (including extras)..." >&2
  uv sync --all-extras
  echo "[run_demo] Launching demo via 'uv run python demo'" >&2
  exec uv run python demo "$@"
fi

if [ -x .venv/bin/python ]; then
  echo "[run_demo] Using existing virtual environment at .venv" >&2
  source .venv/bin/activate
  exec python demo "$@"
fi

echo "[run_demo] Unable to find 'uv' or an existing .venv environment." >&2
echo "[run_demo] Install dependencies with one of the following:" >&2
echo "  uv sync --all-extras" >&2
echo "  python -m venv .venv && source .venv/bin/activate && pip install -e .[ui]" >&2
exit 1
