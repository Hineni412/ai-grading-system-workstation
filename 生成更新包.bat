@echo off
setlocal
cd /d "%~dp0"
title AI Grading System - Update Package Builder

echo ============================================
echo  Incremental update package builder
echo ============================================
echo.
echo This package contains code only (no user data).
echo The running system does NOT need to be closed.
echo.
pause

where py >nul 2>nul
if errorlevel 1 goto missing_python

py update_tools\make_update.py
if errorlevel 1 goto failed

echo.
echo Update package finished. Open the dist\ folder to find it.
pause
exit /b 0

:missing_python
echo The Python launcher (py) was not found on this machine.
pause
exit /b 1

:failed
echo Update packaging failed. Read the output above.
pause
exit /b 1
