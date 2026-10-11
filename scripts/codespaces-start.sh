#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
trap 'printf "Codespaces application startup failed at line %s.\n" "$LINENO" >&2' ERR

if [[ ! -x "$project_root/.venv/bin/python" ]]; then
  printf 'Install the Codespaces dependencies with bash scripts/codespaces-install.sh first.\n' >&2
  exit 1
fi

export CLUETIDE_DATA_DIR="$project_root/local-data"
export CLUETIDE_PREVIEW_ONLY=1
export PYDANTIC_AI_NO_BANNER=1
export PYTHONPATH="$project_root/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$project_root/.venv/bin/python" "$project_root/scripts/codespaces-runtime.py"
