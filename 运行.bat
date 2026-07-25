@echo off
setlocal
cd /d "%~dp0"
title AI Grading P3.5 Acceptance

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime

set "PROJECT_RUNNER=%~dp0tools\run_project_module.py"
if not exist "%PROJECT_RUNNER%" goto missing_runner

if "%API_PORT%"=="" set "API_PORT=8035"
if "%AI_GRADING_WORKTREE_DATA_DIR%"=="" set "AI_GRADING_WORKTREE_DATA_DIR=%~dp0user_data"
if "%AI_GRADING_DATA_DIR%"=="" set "AI_GRADING_DATA_DIR=%AI_GRADING_WORKTREE_DATA_DIR%"
if "%AI_GRADING_OPS_STATE_DIR%"=="" set "AI_GRADING_OPS_STATE_DIR=%~dp0user_data\runtime_state\ops"
if "%AI_GRADING_API_PROFILES_PATH%"=="" set "AI_GRADING_API_PROFILES_PATH=%~dp0user_data\config\api_profiles.json"
set "BROWSER_OPTION="
if "%AI_GRADING_NO_BROWSER%"=="1" set "BROWSER_OPTION=--no-browser"
set "PYTHONUTF8=1"

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.api.launcher --check-frontend >nul
if errorlevel 1 goto frontend_error

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.ops.offline --apply-pending
if errorlevel 1 goto offline_error

echo AI Grading P3.5 acceptance build
echo Data directory: %AI_GRADING_WORKTREE_DATA_DIR%
echo URL: http://127.0.0.1:%API_PORT%/
echo Close this window to stop the service.

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.api.launcher --host 127.0.0.1 --port %API_PORT% %BROWSER_OPTION%
if errorlevel 1 goto server_error
goto done

:missing_runtime
echo Portable Python runtime was not found.
echo Keep this worktree inside the main project and try again.
goto failed

:missing_runner
echo P3.5 project launcher was not found.
echo Missing file: %PROJECT_RUNNER%
goto failed

:frontend_error
echo The P3.5 frontend build is missing or incomplete.
echo Rebuild frontend\dist and try again.
goto failed

:offline_error
echo A protected startup operation could not finish safely.
echo No service was started. Send this window's output to Codex.
goto failed

:server_error
echo The P3.5 service stopped unexpectedly.
echo Confirm that port %API_PORT% is free and send this output to Codex.
goto failed

:failed
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0
