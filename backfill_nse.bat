@echo off
rem Downloads real NSE bhavcopies for a date range (holidays are skipped automatically).
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat quiet
set /p START=Start date (YYYY-MM-DD): 
set /p END=End date (YYYY-MM-DD): 
.venv\Scripts\python.exe run_pipeline.py --source nse --start %START% --end %END%
pause
