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
if "%API_PORT%"=="" set "API_PORT=8000"

"%PYTHON_EXE%" -m backend.api.launcher --check-frontend
if errorlevel 1 (
  echo 默认 Vue 入口无法启动，尚未执行任何启动前数据操作。
  echo 请确认 frontend\dist 目录完整后重试。
  pause
  exit /b 1
)

"%PYTHON_EXE%" -m backend.ops.offline --apply-pending
if errorlevel 1 (
  echo 启动前数据操作未能安全完成，系统已停止启动。
  echo 请把本窗口中的操作编号和结果代码发给 Codex 排查。
  pause
  exit /b 1
)

echo AI阅卷系统 工作机版 v1.5.0
echo 数据目录: %AI_GRADING_DATA_DIR%

echo 当前入口: Vue + FastAPI
echo 启动地址: http://127.0.0.1:%API_PORT%/
echo 关闭本窗口即可停止服务。
"%PYTHON_EXE%" -m backend.api.launcher --host 127.0.0.1 --port %API_PORT%
if errorlevel 1 (
  echo.
  echo 程序异常退出，请确认端口未被占用，并把窗口中的报错发给 Codex 排查。
  pause
)
endlocal
