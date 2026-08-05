@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title AI Grading System Shutdown

set "STOP_SCRIPT=%~dp0tools\stop_service.ps1"
set "PROJECT_ROOT=%~dp0."
set "STOP_RESULT=2"

if not exist "%STOP_SCRIPT%" (
    echo Shutdown helper is missing. Nothing was stopped.
    echo %STOP_SCRIPT%
    goto hold_window
)

set "PWSH_EXE=C:\Program Files\PowerShell\7\pwsh.exe"
if not exist "%PWSH_EXE%" set "PWSH_EXE="
if not defined PWSH_EXE for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined PWSH_EXE set "PWSH_EXE=%%P"
if not defined PWSH_EXE (
    echo PowerShell 7 is required to close the system safely.
    echo Nothing was stopped. Install PowerShell 7, then try again.
    goto hold_window
)

"%PWSH_EXE%" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%STOP_SCRIPT%" -ProjectRoot "%PROJECT_ROOT%" -Port 8035
set "STOP_RESULT=%ERRORLEVEL%"

:hold_window
echo.
echo Press any key to close this window.
pause >nul
endlocal & exit /b %STOP_RESULT%
