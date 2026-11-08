@echo off
cd /d "%~dp0.."
python -m venv .venv || goto :err
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -e . || goto :err
if not exist config.yaml copy config.example.yaml config.yaml
if not exist .env copy .env.example .env
if not exist queue.csv copy queue.example.csv queue.csv
echo.
echo Done. Edit config.yaml and .env, then run:  .venv\Scripts\python.exe -m fblive check
goto :eof
:err
echo Installation failed. Is Python 3.10+ installed and in PATH?
exit /b 1
