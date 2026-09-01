#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
backend_pid=""
backend_port="${BACKEND_PORT:-8000}"
cleanup() {
  if [[ -n "${backend_pid}" ]]; then
    kill "${backend_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

(
  cd "${script_dir}/backend"
  exec uvicorn app.main:app --host 127.0.0.1 --port "${backend_port}"
) &
backend_pid=$!

cd "${script_dir}/frontend"
if [[ "${1:-dev}" == "production" ]]; then
  exec npm run start -- --hostname 0.0.0.0 --port "${PORT:-3000}"
else
  exec npm run dev -- --hostname 0.0.0.0 --port "${PORT:-3000}"
fi
