@echo off
rem Downloads TODAY's real NSE bhavcopy (published around 6-7 PM IST), loads it and builds the report.
rem Used by schedule_daily.bat. Pass "auto" to skip the pause when scheduled.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat quiet
.venv\Scripts\python.exe run_pipeline.py --source nse --notify
if /i not "%~1"=="auto" pause
