@echo off
rem Shows what is inside the downloaded NSE file and which code version is running. Send me a screenshot.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" call setup.bat quiet
.venv\Scripts\python.exe diagnose.py
pause
