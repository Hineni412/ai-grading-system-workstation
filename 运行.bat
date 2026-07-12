@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set "PYTHON_EXE=%~dp0runtime\python\python.exe"
if not exist "%PYTHON_EXE%" (
  set "PYTHON_EXE=%~dp0..\..\runtime\python\python.exe"
)
if not exist "%PYTHON_EXE%" (
  echo 未找到便携 Python 运行时。
  echo 请确认主项目或当前工作树的 runtime\python 目录完整。
  pause
  exit /b 1
)

set "AI_GRADING_DATA_DIR=%~dp0user_data"
set "PYTHONUTF8=1"
set "STREAMLIT_BROWSER_GATHER_USAGE_STATS=false"
set "STREAMLIT_SERVER_HEADLESS=true"
if "%PORT%"=="" set "PORT=8501"
if "%API_PORT%"=="" set "API_PORT=8000"
if "%START_API%"=="" set "START_API=1"

echo AI阅卷系统 工作机版 v1.5.0
echo 数据目录: %AI_GRADING_DATA_DIR%
echo Streamlit 地址: http://127.0.0.1:%PORT%
if /I not "%START_API%"=="0" (
  echo API 地址: http://127.0.0.1:%API_PORT%/healthz
  start "AI阅卷系统 API" "%PYTHON_EXE%" -m uvicorn backend.api.app:app --host 127.0.0.1 --port %API_PORT%
) else (
  echo API 启动: 已跳过 START_API=0
)
start "" "http://127.0.0.1:%PORT%"

"%PYTHON_EXE%" -m streamlit run web_app.py --server.address 127.0.0.1 --server.port %PORT%
if errorlevel 1 (
  echo.
  echo 程序异常退出，请把窗口中的报错发给 Codex 排查。
  pause
)
endlocal
