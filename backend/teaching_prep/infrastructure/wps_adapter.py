from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from backend.teaching_prep.domain.errors import TeachingPrepValidationError


class SubprocessWpsAdapter:
    def __init__(
        self,
        *,
        helper_script: str | Path,
        powershell_executable: str = "pwsh",
        timeout_seconds: int = 170,
    ) -> None:
        self._helper_script = Path(helper_script).resolve(strict=True)
        self._powershell_executable = powershell_executable
        if not 15 <= int(timeout_seconds) <= 900:
            raise ValueError("WPS helper timeout is invalid")
        self._timeout_seconds = int(timeout_seconds)

    def execute(
        self,
        *,
        operation_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        output = plan.get("output")
        source = plan.get("source")
        if not isinstance(output, dict) or not isinstance(source, dict):
            raise TeachingPrepValidationError(
                "WPS helper request paths are invalid"
            )
        candidate = Path(str(output.get("candidate_path") or "")).resolve(
            strict=False
        )
        source_copy = Path(
            str(source.get("isolated_copy_path") or "")
        ).resolve(strict=True)
        working_dir = candidate.parent
        try:
            source_copy.relative_to(working_dir)
        except ValueError as exc:
            raise TeachingPrepValidationError(
                "WPS helper source copy is outside its isolated directory"
            ) from exc
        request_path = working_dir / "helper-request.json"
        result_path = working_dir / "helper-result.json"
        request_path.write_text(
            json.dumps(
                {
                    "operation_id": operation_id,
                    "plan": plan,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        creation_flags = (
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0
        )
        process = subprocess.Popen(
            [
                self._powershell_executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(self._helper_script),
                "-RequestPath",
                str(request_path),
                "-ResultPath",
                str(result_path),
            ],
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creation_flags,
        )
        budget = plan.get("performance_budget")
        if isinstance(budget, dict):
            requested_milliseconds = float(
                budget.get(
                    "timeout_milliseconds",
                    float(
                        budget.get(
                            "timeout_seconds",
                            self._timeout_seconds,
                        )
                    )
                    * 1000,
                )
            )
            effective_timeout = max(
                0.001,
                min(self._timeout_seconds, requested_milliseconds / 1000),
            )
        else:
            effective_timeout = float(self._timeout_seconds)
        try:
            return_code = process.wait(timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            _terminate_helper_tree(process)
            raise TimeoutError("WPS helper timed out") from exc
        if return_code != 0 or not result_path.is_file():
            raise RuntimeError("WPS helper failed")
        if result_path.stat().st_size > 1_000_000:
            raise RuntimeError("WPS helper result is too large")
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("WPS helper result is invalid") from exc
        if not isinstance(result, dict) or result.get("status") != "completed":
            raise RuntimeError("WPS helper did not complete")
        return result


def _terminate_helper_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        subprocess.run(
            [
                "taskkill.exe",
                "/PID",
                str(process.pid),
                "/T",
                "/F",
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


__all__ = ["SubprocessWpsAdapter"]
