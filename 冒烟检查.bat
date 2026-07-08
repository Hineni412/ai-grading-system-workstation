@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
set "AI_GRADING_DATA_DIR=%~dp0user_data"
"%PYTHON_EXE%" tools\smoke_check.py %*
pause
endlocal
