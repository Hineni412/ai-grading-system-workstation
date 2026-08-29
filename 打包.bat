@echo off
setlocal
cd /d "%~dp0"
title AI Grading System - Full Package Builder

echo ============================================
echo  Full package builder (folder only, no zip)
echo ============================================
echo.
echo IMPORTANT: Close the running system first (关闭系统.bat),
echo otherwise the copied database snapshot may be inconsistent.
echo.
pause

where py >nul 2>nul
if errorlevel 1 goto missing_python

py package_v1.5.0.py
if errorlevel 1 goto failed

echo.
echo Package finished. Open the dist\ folder to find it.
pause
exit /b 0

:missing_python
echo The Python launcher (py) was not found on this machine.
pause
exit /b 1

:failed
echo Packaging failed. Read the output above.
pause
exit /b 1
