@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=%~dp0runtime\python\python.exe"
set "AI_GRADING_DATA_DIR=%~dp0user_data"
"%PYTHON_EXE%" -m pytest test_answer_normalizer.py tests\test_objective_batch_recognition_service.py tests\test_objective_escalation.py tests\test_prompt_injection_guard.py tests\test_portable_path_resolution.py -q
pause
endlocal
