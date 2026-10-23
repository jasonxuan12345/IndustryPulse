@echo off
title IndustryPulse Launcher
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-dashboard.ps1" %*
if errorlevel 1 (
    echo.
    echo IndustryPulse could not start. See the message above.
    pause
)
