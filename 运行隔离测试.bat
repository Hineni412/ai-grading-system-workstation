@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime
set "PYTHONUTF8=1"
"%PYTHON_EXE%" tools\run_test_suite.py serial
set "TEST_EXIT_CODE=%ERRORLEVEL%"
pause
endlocal & exit /b %TEST_EXIT_CODE%

:missing_runtime
echo Portable Python runtime was not found.
echo Keep this worktree inside the main project and try again.
pause
endlocal
exit /b 1
