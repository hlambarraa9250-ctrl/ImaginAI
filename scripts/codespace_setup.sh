#!/usr/bin/env bash
set -e
sudo apt-get update
sudo apt-get install -y ffmpeg
cd backend
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
cd ../frontend
npm ci
npm run build
