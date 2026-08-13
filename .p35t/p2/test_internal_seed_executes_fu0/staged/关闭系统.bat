@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title AI Grading P3.5 Shutdown

set "STOP_SCRIPT=%~dp0tools\stop_p3_5_service.ps1"
set "PROJECT_ROOT=%~dp0."
set "STOP_RESULT=2"

if not exist "%STOP_SCRIPT%" (
    echo Shutdown helper is missing. Nothing was stopped.
    echo %STOP_SCRIPT%
    goto hold_window
)

"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%STOP_SCRIPT%" -ProjectRoot "%PROJECT_ROOT%" -Port 8035
set "STOP_RESULT=%ERRORLEVEL%"

:hold_window
echo.
echo Press any key to close this window.
pause >nul
endlocal & exit /b %STOP_RESULT%
