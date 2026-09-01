"""班级分析（教师版）内嵌页面：状态存储、生成 job 与阅卷完成后的自动触发。

- 每场次一个状态文件：受控 reports 目录下 `.class_analysis/{session_id}.json`，
  原子写入；AI 叙述本体直接内嵌在状态文件中，另按
  (session_id, score_revision, rendition, report_key="class:session") 进
  AnalysisNarrativeCache，重新生成命中缓存不再调用模型。
- 页面数据（data）不落盘，GET 时按当前成绩实时装配；状态文件只记叙述与
  score_revision，用于 stale 判定。
- 自动生成挂钩点：阅卷 run 判定为 completed 且该场次无未批完答卷时，
  由 default_handlers 里的 grading_run handler 调用
  maybe_auto_generate_class_analysis。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from analysis_report_prompts import CLASS_MAX_TOKENS, CLASS_SYSTEM_PROMPT
from backend.jobs.manager import (
    ActiveJobExistsError,
    JobContext,
    JobManager,
)
from backend.jobs.store import JobRecord
from backend.repositories.access import GradingRepositoryAccess, as_grading_repositories
from backend.repositories.compat import open_grading_repositories

CLASS_ANALYSIS_JOB_TYPE = "class_analysis_generate"
CLASS_ANALYSIS_STATE_DIRNAME = ".class_analysis"
CLASS_ANALYSIS_RENDITION_VERSION = "class_analysis_page_v1"
CLASS_ANALYSIS_REPORT_KEY = "class:session"
NARRATIVE_CACHE_DIRNAME = ".analysis_narrative_cache"

# 状态文件 status 取值：ready / failed / not_configured；None 表示从未生成。
_STATE_STATUSES = frozenset({"ready", "failed", "not_configured"})

_save_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ClassAnalysisStateStore:
    """每场次一个状态 JSON 文件，原子写入，读取失败按不存在处理。"""

    def __init__(self, reports_dir: Path) -> None:
        self.state_dir = Path(reports_dir) / CLASS_ANALYSIS_STATE_DIRNAME

    def _path(self, session_id: int) -> Path:
        return self.state_dir / f"{int(session_id)}.json"

    @staticmethod
    def _default() -> dict[str, Any]:
        return {
            "version": 1,
            "auto_generate": True,
            "status": None,
            "narrative": None,
            "narrative_error": None,
            "score_revision": "",
            "generated_at": None,
            "small_sample": False,
        }

    def load(self, session_id: int) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._path(session_id).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        state = self._default()
        state.update({key: payload[key] for key in state if key in payload})
        if state["status"] not in _STATE_STATUSES:
            state["status"] = None
        if not isinstance(state["narrative"], dict):
            state["narrative"] = None
        state["auto_generate"] = bool(state["auto_generate"])
        return state

    def save(self, session_id: int, **fields: Any) -> dict[str, Any]:
        """合并写入（保留未提及字段，如 auto_generate 开关），原子替换。"""
        with _save_lock:
            state = self.load(session_id) or self._default()
            state.update(fields)
            self.state_dir.mkdir(parents=True, exist_ok=True)
            target = self._path(session_id)
            temp = target.with_name(f".{target.stem}.tmp")
            try:
                temp.write_text(
                    json.dumps(state, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(temp, target)
            except OSError:
                temp.unlink(missing_ok=True)
                raise
            return state

    def set_auto_generate(self, session_id: int, enabled: bool) -> dict[str, Any]:
        return self.save(session_id, auto_generate=bool(enabled))


def submit_class_analysis_generate(
    *,
    manager: JobManager,
    session_id: int,
    revision: str,
    force: bool = False,
) -> JobRecord:
    """提交班级分析生成 job；已有 queued/running 同类 job 时直接复用返回。"""
    payload = {
        "session_id": int(session_id),
        "score_revision": str(revision),
        "force": bool(force),
    }
    try:
        return manager.submit_unique_active(CLASS_ANALYSIS_JOB_TYPE, payload)
    except ActiveJobExistsError:
        jobs, _total = manager.list(
            session_id=int(session_id),
            job_types=(CLASS_ANALYSIS_JOB_TYPE,),
            statuses=("queued", "running"),
            limit=1,
        )
        if jobs:
            return jobs[0]
        raise


def _class_narrative(
    *,
    client: Any,
    cache: Any,
    session_id: int,
    revision: str,
    prompt: str,
) -> dict[str, Any] | None:
    """缓存命中直接返回；否则恰好调用 1 次模型，任何异常只降级不重发。"""
    from analysis_report_exporter import AnalysisNarrativeCache

    key = AnalysisNarrativeCache.cache_key(
        session_id=int(session_id),
        score_revision=revision,
        rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        report_key=CLASS_ANALYSIS_REPORT_KEY,
    )
    cached = cache.load(key)
    if cached is not None:
        return cached
    try:
        narrative = client.json_from_text(
            prompt,
            extra_kwargs={"temperature": 0.3, "max_tokens": CLASS_MAX_TOKENS},
        )
    except Exception:
        return None
    if not isinstance(narrative, dict):
        return None
    cache.store(key, narrative)
    return narrative


def run_class_analysis_generate(
    context: JobContext,
    *,
    db_path: Path,
    reports_dir: Path,
    data_root: Path | None,
    llm_client_factory: Callable[[], Any] | None,
) -> dict[str, object]:
    """class_analysis_generate job：装配数据 → 生成 AI 叙述 → 写状态文件。"""
    from analysis_report_exporter import (
        AnalysisNarrativeCache,
        assemble_session_analysis,
        build_class_payload,
        build_report_prompt,
    )

    raw_session_id = context.payload.get("session_id")
    if raw_session_id is None:
        raise ValueError("session_id is required")
    session_id = int(raw_session_id)
    # 延迟导入：backend.report_exports 的导入链会经 jobs/__init__ 回到本模块。
    from backend.report_exports import score_revision

    store = ClassAnalysisStateStore(reports_dir)
    db = open_grading_repositories(Path(db_path))
    revision = str(context.payload.get("score_revision") or "").strip() or score_revision(
        db, session_id
    )
    force = bool(context.payload.get("force"))

    # 幂等：同 (session_id, score_revision) 已有 ready 状态且非手动重新生成时跳过。
    existing = store.load(session_id)
    if (
        not force
        and existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
    ):
        return {
            "session_id": session_id,
            "status": "ready",
            "generated_at": existing.get("generated_at"),
            "skipped": True,
        }

    context.raise_if_cancelled()
    context.report(0.1, "class_analysis", "assembling")
    data = assemble_session_analysis(db, session_id, data_root=data_root)
    generated_at = _now_iso()
    if not data.students:
        store.save(
            session_id,
            status="failed",
            narrative=None,
            narrative_error="该场次暂无可分析的成绩数据",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
        )
        return {
            "session_id": session_id,
            "status": "failed",
            "generated_at": generated_at,
        }

    client = llm_client_factory() if llm_client_factory is not None else None
    if client is None:
        # 未配置内容生成模型：不静默换模型，记 not_configured 供页面降级显示。
        store.save(
            session_id,
            status="not_configured",
            narrative=None,
            narrative_error="内容生成模型未配置",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
        )
        return {
            "session_id": session_id,
            "status": "not_configured",
            "generated_at": generated_at,
        }

    context.report(0.4, "class_analysis", "generating_narrative")
    narrative = _class_narrative(
        client=client,
        cache=AnalysisNarrativeCache(Path(reports_dir) / NARRATIVE_CACHE_DIRNAME),
        session_id=session_id,
        revision=revision,
        prompt=build_report_prompt(CLASS_SYSTEM_PROMPT, build_class_payload(data)),
    )
    context.raise_if_cancelled()
    if narrative is not None:
        store.save(
            session_id,
            status="ready",
            narrative=narrative,
            narrative_error=None,
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
        )
        status = "ready"
    else:
        # 模型超时/解析失败：只降级为无叙述版，不暗中重发。
        store.save(
            session_id,
            status="failed",
            narrative=None,
            narrative_error="AI 分析生成失败，可重新生成",
            score_revision=revision,
            generated_at=generated_at,
            small_sample=data.small_sample,
        )
        status = "failed"
    context.report(0.98, "class_analysis", status)
    return {
        "session_id": session_id,
        "status": status,
        "generated_at": generated_at,
    }


def maybe_auto_generate_class_analysis(
    *,
    manager: JobManager,
    db: GradingRepositoryAccess | Any,
    session_id: int,
    reports_dir: Path,
) -> JobRecord | None:
    """阅卷完成后的自动触发：开关开 + 全部答卷批完 + 已配置模型才提交 job。

    同 revision 已 ready 或已有进行中 job 时幂等跳过；未配置模型时记
    not_configured 状态（页面降级显示），不提交 job、不产生费用。
    """
    from analysis_report_exporter import resolve_content_generation_settings
    from backend.report_exports import score_revision

    repositories = as_grading_repositories(db)
    store = ClassAnalysisStateStore(reports_dir)
    existing = store.load(session_id)
    if existing is not None and not bool(existing.get("auto_generate", True)):
        return None
    # 仍有未批完答卷时不算「阅卷结束」。
    if repositories.list_incomplete_results(int(session_id)):
        return None
    revision = score_revision(repositories, session_id)
    if (
        existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
    ):
        return None
    if resolve_content_generation_settings() is None:
        store.save(
            session_id,
            status="not_configured",
            narrative=None,
            narrative_error="内容生成模型未配置",
            score_revision=revision,
            generated_at=_now_iso(),
        )
        return None
    return submit_class_analysis_generate(
        manager=manager,
        session_id=int(session_id),
        revision=revision,
    )


def build_class_analysis_auto_trigger(
    *,
    manager: JobManager,
    db_path: Path,
    reports_dir: Path,
) -> Callable[[int], None]:
    """供 grading_run handler 在批改完成后调用的闭包；任何失败不外抛。"""

    def trigger(session_id: int) -> None:
        maybe_auto_generate_class_analysis(
            manager=manager,
            db=open_grading_repositories(Path(db_path)),
            session_id=int(session_id),
            reports_dir=Path(reports_dir),
        )

    return trigger
