"""已保存报告的读取状态与单份个人报告存储。"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

RESULT_STATES = ("current", "stale", "missing")
_RESULT_KINDS = frozenset({"causes", "personal_report"})
PERSONAL_REPORTS_DIRNAME = ".personal_reports"
_PERSONAL_REPORT_REPLACE_LOCK = threading.Lock()


def resolve_result_state(
    stored: Mapping[str, Any] | None,
    kind: str,
    *,
    current_changed_at: str | None = None,
) -> dict[str, Any]:
    """按保存内容判定可读状态；更新提醒不影响读取。"""
    if kind not in _RESULT_KINDS:
        raise ValueError(f"unknown report result kind: {kind!r}")
    has_result = isinstance(stored, Mapping) and (
        stored.get("result") is not None or stored.get("narrative") is not None
    )
    if not has_result:
        return {"status": "missing", "generated_at": None, "needs_update": False,
                **({"read_error": stored["read_error"]} if stored and stored.get("read_error") else {})}
    generated_at = stored.get("generated_at")
    needs_update = stored.get("needs_update") is True
    if generated_at and current_changed_at:
        try:
            generated = datetime.fromisoformat(str(generated_at).replace("Z", "+00:00"))
            changed = datetime.fromisoformat(str(current_changed_at).replace("Z", "+00:00"))
            needs_update = needs_update or changed.timestamp() > generated.timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
    return {
        "status": "stale" if needs_update else "current",
        "generated_at": generated_at,
        "needs_update": needs_update,
    }



class PersonalReportStore:
    """每个学生一个结果文件，原子写入；不再使用按指纹命名的缓存文件。"""

    def __init__(self, reports_dir: Path) -> None:
        self.base_dir = Path(reports_dir) / PERSONAL_REPORTS_DIRNAME

    def _session_dir(self, session_id: int) -> Path:
        return self.base_dir / f"session_{int(session_id)}"

    def _path(self, session_id: int, student_id: int) -> Path:
        return self._session_dir(session_id) / f"{int(student_id)}.json"

    def load(self, session_id: int, student_id: int) -> dict[str, Any] | None:
        read_error = None
        try:
            payload = json.loads(self._path(session_id, student_id).read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("narrative") is not None:
                return payload
            read_error = "保存的报告内容无法读取，原文件已保留。"
        except FileNotFoundError:
            pass
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            read_error = "保存的报告文件无法读取，原文件已保留。"
        legacy = self._legacy_entry(session_id, student_id)
        if legacy is not None:
            if read_error:
                legacy["read_error"] = read_error
            return legacy
        return {"narrative": None, "read_error": read_error} if read_error else None

    def _legacy_index(self, session_id: int) -> dict:
        entries = {}
        paths = (
            (self.base_dir.parent / ".analysis_narrative_cache" / "personal_index" / f"session_{int(session_id)}.json", "students"),
            (self.base_dir.parent / ".class_analysis" / f"{int(session_id)}.json", "personal_index"),
        )
        for path, key in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            saved = payload.get(key) if isinstance(payload, dict) else None
            if isinstance(saved, dict):
                entries.update(saved)
        return entries

    def _legacy_entry(self, session_id: int, student_id: int) -> dict[str, Any] | None:
        saved = self._legacy_index(session_id).get(str(int(student_id)))
        if not isinstance(saved, dict):
            return None
        narrative = saved.get("narrative")
        if narrative is None:
            key = saved.get("cache_key")
            if isinstance(key, str) and key and Path(key).name == key:
                for name in (".cache", ".analysis_narrative_cache"):
                    narrative = _legacy_cache_load(self.base_dir.parent / name, key)
                    if narrative is not None:
                        break
        if narrative is None:
            return {"narrative": None, "read_error": "索引中的报告正文无法读取，原文件已保留。"}
        return {"narrative": narrative, "generated_at": saved.get("generated_at"),
                "needs_update": saved.get("needs_update") is True}

    def save(
        self,
        session_id: int,
        student_id: int,
        *,
        narrative: Any,
        needs_update: bool = False,
        source: Mapping[str, Any] | None = None,
        generated_at: str | None = None,
    ) -> dict[str, Any]:
        if narrative is None:
            raise ValueError("a successful report narrative is required")
        entry = {"narrative": narrative, "needs_update": bool(needs_update),
                 "generated_at": generated_at or datetime.now().isoformat(timespec="seconds")}
        if source is not None:
            entry["source"] = dict(source)
        session_dir = self._session_dir(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)
        target = self._path(session_id, student_id)
        with tempfile.NamedTemporaryFile(dir=session_dir, prefix=f".{target.stem}.", suffix=".tmp", delete=False) as handle:
            temp = Path(handle.name)
        try:
            temp.write_text(json.dumps(entry, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
            with _PERSONAL_REPORT_REPLACE_LOCK:
                os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        return entry

    def list(self, session_id: int) -> dict[int, dict[str, Any]]:
        entries: dict[int, dict[str, Any]] = {}
        session_dir = self._session_dir(session_id)
        try:
            files = sorted(session_dir.glob("*.json"))
        except OSError:
            return entries
        legacy_ids = self._legacy_index(session_id)
        files = list(files) + [self._path(session_id, int(key)) for key in legacy_ids
                              if str(key).isdigit() and not self._path(session_id, int(key)).exists()]
        for file in files:
            try:
                student_id = int(file.stem)
            except ValueError:
                continue
            entry = self.load(session_id, student_id)
            if entry is not None:
                entries[student_id] = entry
        return entries

    def delete_session(self, session_id: int) -> None:
        shutil.rmtree(self._session_dir(session_id), ignore_errors=True)



def _legacy_cache_load(cache_dir: Path, key: str) -> Any:
    path = Path(cache_dir) / f"{key}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "narrative" not in payload:
        return None
    return payload.get("narrative")
