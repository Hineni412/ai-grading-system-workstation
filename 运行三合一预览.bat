@echo off
setlocal
cd /d "%~dp0"
title AI Grading - Teacher Platform Preview

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime
if "%API_PORT%"=="" set "API_PORT=8035"
set "AI_GRADING_WORKTREE_DATA_DIR=%~dp0user_data"
set "AI_GRADING_DATA_DIR=%AI_GRADING_WORKTREE_DATA_DIR%"
set "AI_GRADING_OPS_STATE_DIR=%~dp0user_data\runtime_state\ops"
set "AI_GRADING_API_PROFILES_PATH=%~dp0user_data\config\api_profiles.json"
set "AI_GRADING_PREVIEW_INSTANCE_ID=teacher-platform-integration"
set "AI_GRADING_PREVIEW_HEAD="
for /f "delims=" %%I in ('git -C "%~dp0" rev-parse HEAD 2^>nul') do set "AI_GRADING_PREVIEW_HEAD=%%I"
if not defined AI_GRADING_PREVIEW_HEAD goto preview_not_ready
set "PYTHONUTF8=1"

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
