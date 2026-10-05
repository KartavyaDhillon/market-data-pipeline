@echo off
rem Loads one month of SAMPLE market data, builds the report and opens it.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat quiet
set PY=.venv\Scripts\python.exe
echo === Running pipeline on sample data (September 2026) ===
%PY% run_pipeline.py --source sample --start 2026-09-01 --end 2026-09-30
echo.
echo === Asking the AI agent a few questions ===
%PY% ask.py "top 5 gainers today"
%PY% ask.py "market breadth"
%PY% ask.py "any data quality issues?"
echo.
start "" "reports\Market_Report_20260930.xlsx"
start "" "reports\market_chart_20260930.png"
pause
