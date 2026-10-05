@echo off
rem Creates a Windows scheduled task that runs the real NSE pipeline every weekday at 7:15 PM.
cd /d "%~dp0"
schtasks /Create /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 19:15 /TN "MarketDataPipeline" /TR "\"%~dp0run_today_nse.bat\" auto" /F
if errorlevel 1 (
    echo Could not create the task. Right-click this file and choose "Run as administrator".
) else (
    echo Scheduled! It will run every weekday at 7:15 PM.
    echo To remove it: schtasks /Delete /TN "MarketDataPipeline" /F
)
pause
