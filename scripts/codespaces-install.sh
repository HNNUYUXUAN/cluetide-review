#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"
trap 'printf "Codespaces installation failed at line %s. Review the command output above.\n" "$LINENO" >&2' ERR

python3.12 -c 'import sys; assert sys.version_info[:2] == (3, 12), "Python 3.12 is required"'
node -e 'if (Number(process.versions.node.split(".")[0]) !== 24) { throw new Error("Node.js 24 is required"); }'

python3.12 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -r .devcontainer/requirements.txt
.venv/bin/python -m pip check

cd -- "$project_root/frontend"
npm ci --include=dev --no-audit --no-fund
npm run build

printf 'Codespaces dependencies and the English release application are ready.\n'
