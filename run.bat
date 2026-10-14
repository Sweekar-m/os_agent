@echo off
title OS Agent - ChatGPT Desktop & Phone Remote
cd /d "%~dp0"

echo ========================================================
echo  Starting OS Agent with Python FastAPI & ChatGPT Desktop
echo ========================================================

if exist backend\venv\Scripts\python.exe (
    backend\venv\Scripts\python.exe start.py
) else if exist C:\Python314\python.exe (
    C:\Python314\python.exe start.py
) else (
    py start.py
)

pause
