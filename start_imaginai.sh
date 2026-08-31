#!/usr/bin/env bash
set -euo pipefail

backend_pid=""
cleanup() {
  if [[ -n "${backend_pid}" ]]; then
    kill "${backend_pid}" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

(
  cd backend
  exec uvicorn app.main:app --host 127.0.0.1 --port 8000
) &
backend_pid=$!

cd frontend
exec npm run dev -- --hostname 0.0.0.0 --port "${PORT:-3000}"