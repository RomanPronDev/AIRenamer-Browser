@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-BrowserNative.ps1" -OpenChromeExtensions
if errorlevel 1 (
    echo AIRenamer setup failed. Check the message above.
    pause
    exit /b 1
)
echo.
echo Setup complete. Press any key to close this window.
pause >nul
