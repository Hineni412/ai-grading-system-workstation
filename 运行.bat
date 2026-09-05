@echo off
setlocal
cd /d "%~dp0"
title AI Grading System

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
if not exist "%PYTHON_EXE%" goto missing_runtime

set "PROJECT_RUNNER=%~dp0tools\run_project_module.py"
if not exist "%PROJECT_RUNNER%" goto missing_runner

set "FRONTEND_DIR=%~dp0frontend"
if not exist "%FRONTEND_DIR%\package.json" goto frontend_ready
where npm.cmd >nul 2>nul
if errorlevel 1 goto missing_frontend_runtime

echo Building the latest frontend...
pushd "%FRONTEND_DIR%"
call npm.cmd run build
set "FRONTEND_BUILD_EXIT=%ERRORLEVEL%"
popd
if not "%FRONTEND_BUILD_EXIT%"=="0" goto frontend_build_error
echo Frontend build completed.

:frontend_ready
if "%API_PORT%"=="" set "API_PORT=8035"
if "%AI_GRADING_WORKTREE_DATA_DIR%"=="" set "AI_GRADING_WORKTREE_DATA_DIR=%~dp0user_data"
if "%AI_GRADING_DATA_DIR%"=="" set "AI_GRADING_DATA_DIR=%AI_GRADING_WORKTREE_DATA_DIR%"
if "%AI_GRADING_OPS_STATE_DIR%"=="" set "AI_GRADING_OPS_STATE_DIR=%~dp0user_data\runtime_state\ops"
if "%AI_GRADING_API_PROFILES_PATH%"=="" set "AI_GRADING_API_PROFILES_PATH=%~dp0user_data\config\api_profiles.json"
set "BROWSER_OPTION="
if "%AI_GRADING_NO_BROWSER%"=="1" set "BROWSER_OPTION=--no-browser"
set "PYTHONUTF8=1"

"%PYTHON_EXE%" -c "import socket, sys; client = socket.socket(); client.settimeout(0.75); result = client.connect_ex(('127.0.0.1', int(sys.argv[1]))); client.close(); raise SystemExit(0 if result == 0 else 1)" "%API_PORT%" >nul 2>nul
if not errorlevel 1 goto port_in_use

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.startup_storage_preflight
if errorlevel 1 goto taxonomy_storage_error

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.api.launcher --check-frontend >nul
if errorlevel 1 goto frontend_error

"%PYTHON_EXE%" "%PROJECT_RUNNER%" backend.ops.offline --apply-pending
if errorlevel 1 goto offline_error

echo AI Grading System
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
echo The project launcher was not found.
echo Missing file: %PROJECT_RUNNER%
goto failed

:missing_frontend_runtime
echo Node.js and npm were not found.
echo Install the required Node.js version, then run this file again.
goto failed

:frontend_build_error
echo The latest frontend could not be built.
echo IMPORTANT: This launch did NOT start the service or open the old page.
echo Fix the frontend build error shown above, then run this file again.
goto failed

:taxonomy_storage_error
echo The machine-local taxonomy state cannot be saved.
echo IMPORTANT: This launch did NOT start the service.
echo Fix the folder permission shown above, then run this file again.
goto failed

:frontend_error
echo The frontend build is missing or incomplete.
echo Rebuild frontend\dist and try again.
goto failed

:offline_error
echo A protected startup operation could not finish safely.
echo No service was started. Send this window's output to Codex.
goto failed

:server_error
echo The service stopped unexpectedly.
echo Confirm that port %API_PORT% is free and send this output to Codex.
goto failed

:port_in_use
echo Port %API_PORT% is already in use.
echo IMPORTANT: This launch did NOT restart the service.
echo The open page may still be connected to an older process.
echo Close the existing service window, then run this file again.
goto failed

:failed
pause
endlocal
exit /b 1

:done
endlocal
exit /b 0
