@echo off
title CareCall Backend Server
cd /d "%~dp0backend"
echo Starting CareCall Backend on http://localhost:8000 ...
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
pause
