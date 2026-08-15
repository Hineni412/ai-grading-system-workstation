from __future__ import annotations

import atexit
import json
import subprocess
import threading
import time
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
        self._session_lock = threading.Lock()
        self._session: _PreviewHelperSession | None = None
        atexit.register(self.close_preview_session)

    def execute(
        self,
        *,
        operation_id: str,
        plan: dict[str, Any],
    ) -> dict[str, Any]:
        self.close_preview_session()
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
        return self._invoke_helper(
            operation_id=operation_id,
            plan=plan,
            working_dir=working_dir,
        )

    def render_previews(
        self,
        *,
        operation_id: str,
        source_copy: str,
        preview_directory: str,
        slide_indexes: list[int],
        source_sha256: str,
        timeout_milliseconds: int,
    ) -> dict[str, Any]:
        source_path = Path(source_copy).resolve(strict=True)
        preview_dir = Path(preview_directory).resolve(strict=False)
        working_dir = preview_dir.parent
        try:
            source_path.relative_to(working_dir)
            preview_dir.relative_to(working_dir)
        except ValueError as exc:
            raise TeachingPrepValidationError(
                "WPS preview paths are outside the isolated directory"
            ) from exc
        indexes = [int(item) for item in slide_indexes]
        if (
            not indexes
            or len(indexes) > 32
            or len(indexes) != len(set(indexes))
            or any(index <= 0 for index in indexes)
        ):
            raise TeachingPrepValidationError(
                "WPS preview slide selection is invalid"
            )
        timeout_seconds = max(
            0.001,
            min(self._timeout_seconds, float(timeout_milliseconds) / 1000),
        )
        try:
            return self._render_previews_with_session(
                source_path=source_path,
                preview_dir=preview_dir,
                working_dir=working_dir,
                indexes=indexes,
                source_sha256=str(source_sha256),
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            self.close_preview_session()
            raise

    def close_preview_session(self) -> None:
        session = None
        with self._session_lock:
            session = self._session
            self._session = None
        if session is not None:
            session.close()

    def _render_previews_with_session(
        self,
        *,
        source_path: Path,
        preview_dir: Path,
        working_dir: Path,
        indexes: list[int],
        source_sha256: str,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        with self._session_lock:
            session = self._session
            if (
                session is None
                or not session.is_alive()
                or session.working_dir != working_dir
                or session.source_sha256 != source_sha256
            ):
                if session is not None:
                    session.close()
                session = _PreviewHelperSession(
                    helper_script=self._helper_script,
                    powershell_executable=self._powershell_executable,
                    working_dir=working_dir,
                    source_path=source_path,
                    source_sha256=source_sha256,
                    creation_flags=_creation_flags(),
                )
                self._session = session
            active = session
        opened = active.call(
            {
                "op": "open",
                "source_copy": str(source_path),
                "sha256": source_sha256,
            },
            timeout_seconds=min(timeout_seconds, 60),
        )
        if opened.get("status") != "completed":
            raise RuntimeError("WPS preview session failed to open")
        result = active.call(
            {
                "op": "export",
                "source_copy": str(source_path),
                "sha256": source_sha256,
                "preview_directory": str(preview_dir),
                "slide_indexes": indexes,
            },
            timeout_seconds=timeout_seconds,
        )
        rendered = result.get("rendered_slide_indexes")
        if isinstance(rendered, int):
            rendered = [rendered]
        if (
            result.get("status") != "completed"
            or rendered != indexes
            or result.get("source_unchanged") is not True
        ):
            raise RuntimeError("WPS preview verification contract failed")
        return result

    def _invoke_helper(
        self,
        *,
        operation_id: str,
        plan: dict[str, Any],
        working_dir: Path,
    ) -> dict[str, Any]:
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
            creationflags=_creation_flags(),
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


class _PreviewHelperSession:
    def __init__(
        self,
        *,
        helper_script: Path,
        powershell_executable: str,
        working_dir: Path,
        source_path: Path,
        source_sha256: str,
        creation_flags: int,
    ) -> None:
        self.working_dir = working_dir
        self.source_path = source_path
        self.source_sha256 = source_sha256
        self._command_path = working_dir / "session-command.json"
        self._command_ready = working_dir / "session-command.ready"
        self._result_path = working_dir / "session-result.json"
        self._result_ready = working_dir / "session-result.ready"
        self._clear_signals()
        working_dir.mkdir(parents=True, exist_ok=True)
        self._process = subprocess.Popen(
            [
                powershell_executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-File",
                str(helper_script),
                "-PreviewSession",
                "-SessionDirectory",
                str(working_dir),
            ],
            cwd=working_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=creation_flags,
        )
        if self._process.poll() is not None:
            raise RuntimeError("WPS preview session failed to start")

    def is_alive(self) -> bool:
        return self._process.poll() is None

    def call(
        self,
        command: dict[str, Any],
        *,
        timeout_seconds: float,
    ) -> dict[str, Any]:
        if not self.is_alive():
            raise RuntimeError("WPS preview session is not running")
        self._clear_signals()
        payload = json.dumps(
            command,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        temporary = self._command_path.with_name(
            f".{self._command_path.name}.tmp"
        )
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(self._command_path)
        self._command_ready.write_text("1", encoding="utf-8")
        deadline = time.monotonic() + max(0.001, float(timeout_seconds))
        while time.monotonic() < deadline:
            if self._result_ready.is_file():
                break
            if not self.is_alive():
                raise RuntimeError("WPS preview session exited")
            time.sleep(0.05)
        else:
            raise TimeoutError("WPS helper timed out")
        if self._result_path.stat().st_size > 1_000_000:
            raise RuntimeError("WPS helper result is too large")
        try:
            result = json.loads(
                self._result_path.read_text(encoding="utf-8-sig")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("WPS helper result is invalid") from exc
        self._clear_signals()
        if not isinstance(result, dict):
            raise RuntimeError("WPS helper result is invalid")
        return result

    def close(self) -> None:
        if self.is_alive():
            try:
                self.call({"op": "close"}, timeout_seconds=5)
            except Exception:
                pass
        _terminate_helper_tree(self._process)
        self._clear_signals()

    def _clear_signals(self) -> None:
        for path in (
            self._command_path,
            self._command_ready,
            self._result_path,
            self._result_ready,
        ):
            path.unlink(missing_ok=True)


def _creation_flags() -> int:
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return 0


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
