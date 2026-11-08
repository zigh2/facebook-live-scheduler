@echo off
cd /d "%~dp0.."
if exist .venv\Scripts\python.exe (
  .venv\Scripts\python.exe -m fblive run
) else (
  python -m fblive run
)
