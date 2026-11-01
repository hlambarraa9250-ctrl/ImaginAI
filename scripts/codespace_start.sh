#!/usr/bin/env bash
cd "$(dirname "$0")/.."
mkdir -p .logs
pkill -f "uvicorn app.main:app" 2>/dev/null || true
pkill -f "next start" 2>/dev/null || true
(cd backend && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 > ../.logs/backend.log 2>&1 &)
(cd frontend && nohup npm run start -- -p 3000 -H 0.0.0.0 > ../.logs/frontend.log 2>&1 &)
echo started
