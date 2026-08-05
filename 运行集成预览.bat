@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title AI Grading - Integration Preview

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime
if "%API_PORT%"=="" set "API_PORT=8035"
set "AI_GRADING_WORKTREE_DATA_DIR=%~dp0user_data"
set "AI_GRADING_DATA_DIR=%AI_GRADING_WORKTREE_DATA_DIR%"
set "AI_GRADING_OPS_STATE_DIR=%~dp0user_data\runtime_state\ops"
set "AI_GRADING_API_PROFILES_PATH=%~dp0user_data\config\api_profiles.json"
set "AI_GRADING_PREVIEW_INSTANCE_ID=integration-preview"
set "AI_GRADING_PREVIEW_HEAD="
set "PYTHONUTF8=1"

"%PYTHON_EXE%" "%~dp0tools\integration_preview.py" ensure
if errorlevel 1 goto preview_prepare_error

for /f "delims=" %%I in ('call "%PYTHON_EXE%" "%~dp0tools\integration_preview.py" print-head') do set "AI_GRADING_PREVIEW_HEAD=%%I"
if not defined AI_GRADING_PREVIEW_HEAD goto preview_prepare_error

"%PYTHON_EXE%" "%~dp0tools\integration_preview.py" open-running
set "RUNNING_STATUS=%ERRORLEVEL%"
if "%RUNNING_STATUS%"=="0" goto done
if not "%RUNNING_STATUS%"=="3" goto running_probe_error

call "%~dp0运行.bat"
set "EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %EXIT_CODE%

:missing_runtime
echo Portable Python runtime was not found.
goto failed

:preview_prepare_error
echo.
echo Preview preparation or frontend update failed.
echo See the detailed error above.
goto failed

:running_probe_error
echo.
echo Could not determine whether the service on port %API_PORT% can be reused.
echo See the detailed error above and send the full window capture to Codex.
goto failed

:failed
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0
