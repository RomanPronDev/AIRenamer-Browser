@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Update-Browser.ps1"
if errorlevel 1 (
    echo AIRenamer update failed. Check the message above.
    pause
    exit /b 1
)
