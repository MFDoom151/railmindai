@echo off
cd /d "%~dp0backend"
echo Digital Station -^> http://127.0.0.1:8000   (API: /docs, metrics: /metrics)
python -m uvicorn main:app --host 127.0.0.1 --port 8000
