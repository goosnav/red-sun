#!/usr/bin/env bash
# Red Sun: start the GUI from a source checkout.
# Uses uv when available (exact locked Pillow, managed Python 3.12); otherwise python3 + a local venv.
set -euo pipefail
cd "$(dirname "$0")"
export PYTHONPATH="$PWD/app/src"
if command -v uv >/dev/null 2>&1; then
  exec uv run --project app --no-dev python -m red_sun.start "$@"
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "Red Sun needs either uv (https://docs.astral.sh/uv/) or Python 3.10+ on PATH." >&2
  exit 1
fi
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install --quiet --disable-pip-version-check "pillow==12.0.0"
exec .venv/bin/python -m red_sun.start "$@"
