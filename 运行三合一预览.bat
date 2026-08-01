@echo off
setlocal
cd /d "%~dp0"
title AI Grading - Teacher Platform Preview

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime
if "%API_PORT%"=="" set "API_PORT=8035"

"%PYTHON_EXE%" "%~dp0tools\teacher_platform_preview.py" ensure
if errorlevel 1 goto preview_not_ready

"%PYTHON_EXE%" "%~dp0tools\teacher_platform_preview.py" open-running
set "RUNNING_STATUS=%ERRORLEVEL%"
if "%RUNNING_STATUS%"=="0" goto done
if not "%RUNNING_STATUS%"=="3" goto preview_not_ready

call "%~dp0运行.bat"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:missing_runtime
echo Portable Python runtime was not found.
goto failed

:preview_not_ready
echo.
echo The three-workspace preview is not current.
echo Ask Codex to merge the latest checkpoints, then try again.
goto failed

:failed
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0
