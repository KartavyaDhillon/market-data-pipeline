@echo off
rem Chat with the AI agent about the market data. Run run_demo.bat first.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat quiet
if not exist "data\market.db" .venv\Scripts\python.exe run_pipeline.py --source sample --start 2026-09-01 --end 2026-09-30
.venv\Scripts\python.exe ask.py
