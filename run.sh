#!/usr/bin/env sh
cd "$(dirname "$0")/backend" && exec python -m uvicorn main:app --host 127.0.0.1 --port 8000
