#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "$0")/.." && pwd)"
root_dir="$(cd "$project_dir/.." && pwd)"
python_bin="${CODEX_TEST_PYTHON:-$root_dir/.venv-codex/bin/python}"

if [[ ! -x "$python_bin" ]]; then
  echo "Codex test environment is unavailable: $python_bin" >&2
  exit 2
fi

cd "$project_dir"
PYTHONPATH=. "$python_bin" -m pytest "$@"
