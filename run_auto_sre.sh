#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${ROOT_DIR}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  echo "Python virtualenv not found at ${PYTHON_BIN}" >&2
  exit 1
fi

cd "${ROOT_DIR}/auto-sre"
exec "${PYTHON_BIN}" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
