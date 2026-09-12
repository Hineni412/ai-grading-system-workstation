"""班级分析（教师版）内嵌页面：状态存储、生成 job 与阅卷完成后的自动触发。

- 每场次一个状态文件：受控 reports 目录下 `.class_analysis/{session_id}.json`，
  原子写入；AI 叙述本体直接内嵌在状态文件中，另按
  (session_id, score_revision, rendition, report_key="class:{class_name}") 进
  AnalysisNarrativeCache，重新生成命中缓存不再调用模型。
- 页面数据（data）不落盘，GET 时按当前成绩实时装配；状态文件只记叙述与
  score_revision，用于叙述 stale 判定。逐题错因归并同存于该状态文件，保留
  批语映射及评分要求，读取时匹配当前输入并按所选班级去重统计人数。
- 错因整理只由手动 kind=causes 任务触发；普通读取、切班与自动报告不调用。
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
CLASS_ANALYSIS_RENDITION_VERSION = "class_analysis_page_v3_class_scope"
CLASS_ANALYSIS_REPORT_KEY = "class:session"
NARRATIVE_CACHE_DIRNAME = ".analysis_narrative_cache"
CAUSE_ANALYSIS_VERSION = "class_error_causes_v2"
CAUSE_KINDS = frozenset({"error", "process", "response_state", "carry_forward", "review"})

CAUSE_ANALYSIS_PROMPT = """你是数学教师，依据本场考试的题目、参考解答、已有作答证据与批语整理失分情况，不重新评分。
evidence 的每个 id 代表相同的批语、作答及前问证据组合，不是学生身份。批语是解释来源，不是不可质疑的事实。
先看 question_text、reference_analysis 与 canonical_answer，再对照 rubric 和作答；只用现有证据，缺资料应明确待核对，不重新识别图片。
每组分为 kind：
error：作答支持的数学或单位错误。只描述可观察的错误，不猜粗心、态度、不会某知识。
process：未写依据、关键推导未展示、未完成求解等过程缺项，与数学错误分开。
response_state：未作答、全部作废无有效内容、仅无关作答；不能依据低分或默认字段猜空白。
carry_forward：沿用前问错误量，当前关系在该输入下成立。必须给出该证据 previous_answers 中的 source_question_id。若另有独立新错，可另列 error，不能把每个受影响步骤重复归错。
review：字迹辨认、标准与参考解答冲突、等价形式争议、证据不足等需要核对的具体问题。教师确认的是分数，不能据此消除旧批语中的辨认疑点。
reason 为可复用的规范名称，例如“选错目标量的组成部分”；manifestation 为本题具体表现，例如“求绳长时多加水平边”。同一规范错因的不同表现用相同 reason 分别列组，系统合并人数并保留表现。
每个有分歧的方面单独处理；一份可以同时有过程缺项、确定错误与待核对项。不得用不确定猜测填满数学错因。
判断边界：只写直角结论没证明是 process；先假设待证直角再据此论证才是循环论证。停在12x=28是未完成求解，算出x=2才是计算错。
28/12与7/3等价，是否必须化简属于书写要求；参考解答也未展开平方时，不把未展开直接判为数学错误。已改正并保留的最终答案不能仍算成旧的划去答案。
绳长中多加一段与替错一段可共用规范名称，但 manifestation 必须区分。沿用前问错误绳长后运算自洽，不再推断不会勾股定理。
没有作答过程时，不从选项或错误数字推测具体认知错因；保留可观察的选答表现，并归 review 的“过程原因未明”。
肯定表述不成为错因；全部证据只支持正确、且没有任何待核对方面时放 positive_ids。整条不足以整理的放 uncertain_ids，遗漏项也由系统保留待核对。
仅返回 JSON：{"groups":[{"kind":"error","reason":"规范错因","manifestation":"本题证据支持的具体表现","evidence_ids":["E1"],"source_question_id":null}],"positive_ids":[],"uncertain_ids":[]}。
覆盖全部输入 id，只用输入 id；同一 id 可在多个组，但 positive_ids、uncertain_ids 与组成员互斥。不要输出人数、姓名、分数或评分调整；人数由系统去重。
"""


def _cause_text(record: Any) -> str:
    parts = []
    seen = set()
    for label, value in (("扣分理由", record.deduction_reason), ("错因", record.error_summary),
                         ("错误类别", record.error_category), ("教师批语", record.teacher_comment)):
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            parts.append(f"{label}：{text}")
    return "\n".join(parts) or "未记录具体错因"


def assemble_cause_data(db: Any, session_id: int, *, data_root: Path | None = None) -> Any:
    """复用成绩快照、作答文字和已绑定题目，跳过报告图片与题库回填。"""
    from analysis_report_exporter import assemble_session_analysis, _enrich_personal_questions
    repositories = as_grading_repositories(db)
    data = assemble_session_analysis(repositories, session_id, data_root=data_root,
                                     page_only=True, include_answer_evidence=True)
    _enrich_personal_questions(repositories, data, data_root, include_images=False)
    return data


def _cause_rubric(data: Any, question_id: str) -> dict[str, Any]:
    from analysis_report_exporter import _parent_question_id
    allowed = {"question_id", "question_type", "part_id", "max_score", "stem_summary", "question_text",
               "text", "parts", "deduction_policy", "proof_obligations", "steps", "canonical_answer",
               "step_id", "description", "evidence", "evidence_required", "score", "points",
               "deduction_rules", "answer_only", "final_answer_required", "equivalent_answers",
               "part_score", "step_score", "core_goal", "required_elements", "allow_alternative_methods",
               "presentation_rules", "rule_id", "rule", "max_deduction", "require_final_answer",
               "answer_only_max_score", "response_mode", "accepted_forms", "answer"}

    def project(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: project(item) for key, item in value.items() if key in allowed}
        if isinstance(value, list):
            return [project(item) for item in value]
        return value

    return {key: value for q in data.rubric.get("questions", []) if isinstance(q, dict)
            and _parent_question_id(str(q.get("question_id") or "")) == _parent_question_id(question_id)
            for key, value in project(q).items()}


def _cause_evidence(student: Any, record: Any) -> dict[str, Any]:
    from analysis_report_exporter import _parent_question_id, _natural_question_order
    siblings = {item.question_id: item for item in student.records
                if _parent_question_id(item.question_id) == _parent_question_id(record.question_id)}
    order = _natural_question_order(siblings)
    previous = [siblings[qid] for qid in order[:order.index(record.question_id)]]
    return {
        "text": _cause_text(record), "student_answer": record.student_answer,
        "evidence_steps": record.evidence_steps, "missing_steps": record.missing_steps,
        "teacher_confirmed": record.teacher_confirmed,
        "previous_answers": [{"question_id": item.question_id, "student_answer": item.student_answer,
                              "text": _cause_text(item), "evidence_steps": item.evidence_steps}
                             for item in previous],
    }


def _evidence_key(evidence: dict[str, Any]) -> str:
    return json.dumps({key: value for key, value in evidence.items() if key != "id"},
                      ensure_ascii=False, sort_keys=True)


def build_cause_inputs(data: Any) -> list[dict[str, Any]]:
    """全场共用逐题输入；只有批语、作答及前问证据都相同才合并，不发送身份。"""
    evidence: dict[str, dict[str, dict[str, Any]]] = {}
    for student in data.students:
        for record in student.records:
            if record.lost:
                item = _cause_evidence(student, record)
                evidence.setdefault(record.question_id, {})[_evidence_key(item)] = item
    return [{
        "question_id": info.question_id, "max_score": info.max_score,
        "stem_summary": info.stem_summary, "canonical_answer": info.canonical_answer,
        "question_text": info.question_text, "reference_analysis": info.reference_analysis,
        "rubric": _cause_rubric(data, info.question_id),
        "evidence": [{"id": f"E{index}", **item} for index, (_key, item) in
                     enumerate(sorted(evidence[info.question_id].items()), start=1)],
    } for info in data.questions if evidence.get(info.question_id)]


def normalize_cause_result(payload: Any, source: dict[str, Any]) -> dict[str, Any]:
    """保存类型、规范名称、本题表现与证据映射；人数不采纳模型输出。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        raise ValueError("invalid cause classification")
    known = {item["id"] for item in source["evidence"]}

    def ids(value: Any) -> set[str]:
        if not isinstance(value, list) or any(not isinstance(item, str) or item not in known for item in value):
            raise ValueError("invalid evidence reference")
        return set(value)

    groups: dict[tuple[str, str], dict[tuple[str, str | None], set[str]]] = {}
    source_evidence = {item["id"]: item for item in source["evidence"]}
    for group in payload["groups"]:
        if not isinstance(group, dict) or not isinstance(group.get("reason"), str) or not group["reason"].strip():
            raise ValueError("invalid cause label")
        kind, manifestation = group.get("kind"), group.get("manifestation")
        if kind not in CAUSE_KINDS or not isinstance(manifestation, str) or not manifestation.strip():
            raise ValueError("invalid cause kind or manifestation")
        members = ids(group.get("evidence_ids"))
        previous_id = group.get("source_question_id")
        if kind == "carry_forward":
            if not isinstance(previous_id, str) or not members or any(
                previous_id not in {item["question_id"] for item in source_evidence[key].get("previous_answers", [])}
                for key in members
            ):
                raise ValueError("invalid previous question reference")
        elif previous_id is not None:
            raise ValueError("unexpected previous question reference")
        if members:
            groups.setdefault((kind, group["reason"].strip()), {}).setdefault(
                (manifestation.strip(), previous_id), set()).update(members)
    positive = ids(payload.get("positive_ids", []))
    uncertain = ids(payload.get("uncertain_ids", []))
    assigned = {key for variants in groups.values() for members in variants.values() for key in members}
    if assigned & (positive | uncertain) or positive & uncertain:
        raise ValueError("conflicting evidence classification")
    uncertain |= known - assigned - positive - uncertain
    return {
        "groups": [{"kind": kind, "reason": reason,
                    "evidence_ids": sorted({key for members in variants.values() for key in members}),
                    "manifestations": [{"description": description, "source_question_id": previous_id,
                                        "evidence_ids": sorted(members)}
                                       for (description, previous_id), members in variants.items()]}
                   for (kind, reason), variants in groups.items()],
        "positive_ids": sorted(positive), "uncertain_ids": sorted(uncertain),
    }


def save_cause_result(store: Any, session_id: int, source: dict[str, Any], payload: Any, *, origin: str = "model") -> None:
    """模型生成与本次助手归类共用同一校验、保存入口，逐题保存已完成结果。"""
    result = normalize_cause_result(payload, source)
    current = (store.load(session_id) or {}).get("cause_analysis") or {}
    questions = dict(current.get("questions") or {})
    previous = questions.get(source["question_id"]) or {}
    history = list(previous.get("history") or [])
    if previous.get("result") and any((previous.get("version") != CAUSE_ANALYSIS_VERSION,
                                      previous.get("input") != source, previous.get("result") != result)):
        history.append({key: value for key, value in previous.items() if key not in {"history", "failed"}})
    questions[source["question_id"]] = {
        "version": CAUSE_ANALYSIS_VERSION, "input": source, "result": result,
        "generated_at": _now_iso(), "origin": origin, "failed": False,
        "history": history,
    }
    store.save(session_id, cause_analysis={"questions": questions})


def apply_cause_results(page: dict[str, Any] | None, data: Any, all_inputs: list[dict[str, Any]], state: Any) -> dict[str, Any]:
    """匹配当前作答证据后按班级投影；兼容的旧归并明确标记，等待手动升级。"""
    stored = ((state or {}).get("cause_analysis") or {}).get("questions") or {}
    sources = {source["question_id"]: source for source in all_inputs}
    ready, failed, legacy_count, stale, times, origins = 0, 0, 0, False, [], set()
    question_pages = {item["question_id"]: item for item in (page or {}).get("questions", [])}
    for question_id, source in sources.items():
        saved = stored.get(question_id) or {}
        fresh = (saved.get("version") == CAUSE_ANALYSIS_VERSION and saved.get("input") == source
                 and isinstance(saved.get("result"), dict))
        legacy = saved.get("version") == "class_error_causes_v1" and isinstance(saved.get("result"), dict)
        old_source = saved.get("input") or {}
        legacy = legacy and all(old_source.get(key) == source.get(key)
                                for key in ("question_id", "max_score", "stem_summary", "canonical_answer"))
        legacy = legacy and {item.get("text") for item in old_source.get("evidence", [])} == {
            item["text"] for item in source["evidence"]}
        if not fresh:
            stale |= bool(saved.get("result"))
            failed += int(bool(saved.get("failed")))
            if not legacy:
                continue
            legacy_count += 1
        else:
            ready += 1
        times.append(saved.get("generated_at") or "")
        origins.add(saved.get("origin") or "model")
        question = question_pages.get(question_id)
        if question is None:
            continue
        by_evidence: dict[str, set[int]] = {}
        for student in data.students:
            for record in student.records:
                if record.question_id == question_id and record.lost:
                    key = _cause_text(record) if legacy else _evidence_key(_cause_evidence(student, record))
                    by_evidence.setdefault(key, set()).add(student.student_id)
        evidence = {item["id"]: item for item in (old_source if legacy else source)["evidence"]}

        def details(member_ids: list[str]) -> list[dict[str, Any]]:
            result = []
            for key in member_ids:
                item = evidence.get(key)
                if item is None:
                    continue
                lookup = item["text"] if legacy else _evidence_key(item)
                if members := by_evidence.get(lookup):
                    result.append({**{field: value for field, value in item.items() if field != "id"},
                                   "student_ids": sorted(members)})
            return result

        causes = []
        for group in saved["result"]["groups"]:
            items = details(group["evidence_ids"])
            members = {sid for item in items for sid in item["student_ids"]}
            if members:
                cause = {"reason": group["reason"], "count": len(members), "evidence": items}
                if not legacy:
                    cause["kind"] = group["kind"]
                    cause["manifestations"] = [
                        {"description": variant["description"], "source_question_id": variant["source_question_id"],
                         "evidence": visible}
                        for variant in group["manifestations"] if (visible := details(variant["evidence_ids"]))
                    ]
                causes.append(cause)
        question["causes"] = sorted(causes, key=lambda item: -item["count"])
        question["cause_review"] = {
            "positive": details(saved["result"]["positive_ids"]),
            "uncertain": details(saved["result"]["uncertain_ids"]),
        }
        question["causes_grouped"] = True
        question["causes_legacy"] = bool(legacy)
    total = len(sources)
    return {"status": "ready" if ready == total else "partial" if ready else "not_generated",
            "pending_questions": total - ready, "total_questions": total, "failed_questions": failed,
            "legacy_questions": legacy_count,
            "stale": stale, "generated_at": max(times, default="") or None,
            "origin": "assistant" if origins == {"assistant"} else "model" if origins else None}


def run_cause_analysis(context: Any, *, db: Any, data_root: Path | None, store: Any,
                       llm_client_factory: Callable[[], Any] | None) -> dict[str, object]:
    session_id = int(context.payload["session_id"])
    data = assemble_cause_data(db, session_id, data_root=data_root)
    sources = build_cause_inputs(data)
    client = None
    failed = 0
    for index, source in enumerate(sources):
        context.raise_if_cancelled()
        old = ((store.load(session_id) or {}).get("cause_analysis") or {}).get("questions") or {}
        saved = old.get(source["question_id"]) or {}
        if saved.get("version") == CAUSE_ANALYSIS_VERSION and saved.get("input") == source and saved.get("result"):
            continue
        context.report((index + 0.2) / max(1, len(sources)), "class_analysis", "grouping_error_causes")
        if client is None:
            client = llm_client_factory() if llm_client_factory else None
        try:
            if client is None:
                raise ValueError("content generation model is not configured")
            prompt = CAUSE_ANALYSIS_PROMPT + "\n" + json.dumps(source, ensure_ascii=False)
            payload = client.json_from_text(prompt, extra_kwargs={"temperature": 0.2, "max_tokens": 12000})
            context.raise_if_cancelled()
            save_cause_result(store, session_id, source, payload)
        except Exception:
            context.raise_if_cancelled()
            failed += 1
            # 失败不重发；其他已完成题目继续可用，旧输入的结果仍由读取端判定是否过期。
            old[source["question_id"]] = {**saved, "failed": True}
            store.save(session_id, cause_analysis={"questions": old})
    return {"session_id": session_id, "kind": "causes", "failed_questions": failed,
            "status": "failed" if failed else "ready"}


def class_question_preview(
    db: GradingRepositoryAccess, session_id: int, question_id: str, *, source_service: Any,
) -> dict[str, Any]:
    """按需读取考试绑定的原题，复用配置页图文投影及图片读取入口。"""
    from analysis_report_exporter import _infer_data_root, _load_rubric, _parent_question_id
    from backend.config_workspace.sources import _project_config_rich_blocks

    repositories = as_grading_repositories(db)
    session = repositories.sessions.get_grading_session(session_id) or {}
    root = _infer_data_root(repositories.db_path)
    parent = _parent_question_id(question_id)
    bound_source = str(session.get("source_paper_sha256") or "").strip()
    if bound_source:
        try:
            source = source_service.load_active_record(session_id=session_id)
            if source.sha256 == bound_source:
                original = next((item for item in source.public_snapshot()["questions"]
                                 if item["question_id"] == parent), None)
                if original is not None:
                    content = original["rich_content"]
                    return {
                        "question_id": question_id,
                        "parent_question_id": parent,
                        "text": original["question_preview"],
                        "rich_content": {**content, "answer_blocks": [], "answer_block_count": 0,
                                         "available": bool(content["question_blocks"])},
                        "notice": "",
                    }
        except (OSError, ValueError, RuntimeError):
            pass

    rubric = _load_rubric(session, root)
    question = next((item for item in rubric.get("questions", []) if isinstance(item, dict)
                     and _parent_question_id(str(item.get("question_id") or "")) == parent), {})
    text = next((str(question[key]).strip() for key in ("question_html", "question_text", "text", "stem")
                 if question.get(key)), "")
    full_text = bool(text)
    text = text or str(question.get("stem_summary") or "")
    options = question.get("options")
    if isinstance(options, dict):
        text += "\n" + "\n".join(f"{label}. {value}" for label, value in options.items())
    elif isinstance(options, list):
        text += "\n" + "\n".join(str(value) for value in options)
    blocks = _project_config_rich_blocks(text)
    return {
        "question_id": question_id,
        "parent_question_id": parent,
        "text": text,
        "rich_content": {"available": bool(blocks), "question_block_count": len(blocks),
                         "answer_block_count": 0, "question_blocks": blocks, "answer_blocks": []},
        "notice": "原文件预览不可用，显示考试配置中的题目文字。" if full_text else
                  "未找到可预览的完整原题，以下仅为已保存的题干摘要。",
    }

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
            "class_reports": {},
            "rendition_version": "",
            "cause_analysis": None,
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
    kind: str = "narrative",
) -> JobRecord:
    """提交班级分析生成 job；已有 queued/running 同类 job 时直接复用返回。"""
    payload = {
        "session_id": int(session_id),
        "score_revision": str(revision),
        "force": bool(force),
        "kind": kind,
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
    class_name: str | None = None,
) -> dict[str, Any] | None:
    """缓存命中直接返回；否则恰好调用 1 次模型，任何异常只降级不重发。"""
    from analysis_report_exporter import AnalysisNarrativeCache

    key = AnalysisNarrativeCache.cache_key(
        session_id=int(session_id),
        score_revision=revision,
        rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        report_key=f"class:{class_name}" if class_name is not None else CLASS_ANALYSIS_REPORT_KEY,
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
        split_session_analysis_by_class,
    )

    raw_session_id = context.payload.get("session_id")
    if raw_session_id is None:
        raise ValueError("session_id is required")
    session_id = int(raw_session_id)
    # 延迟导入：backend.report_exports 的导入链会经 jobs/__init__ 回到本模块。
    from backend.report_exports import score_revision

    store = ClassAnalysisStateStore(reports_dir)
    db = open_grading_repositories(Path(db_path))
    if context.payload.get("kind") == "causes":
        return run_cause_analysis(context, db=db, data_root=data_root, store=store,
                                  llm_client_factory=llm_client_factory)
    revision = str(context.payload.get("score_revision") or "").strip() or score_revision(
        db, session_id, include_question_bank=False
    )
    force = bool(context.payload.get("force"))

    # 幂等：同 (session_id, score_revision) 已有 ready 状态且非手动重新生成时跳过。
    existing = store.load(session_id)
    if (
        not force
        and existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
        and existing.get("rendition_version") == CLASS_ANALYSIS_RENDITION_VERSION
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
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
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
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
        )
        return {
            "session_id": session_id,
            "status": "not_configured",
            "generated_at": generated_at,
        }

    context.report(0.4, "class_analysis", "generating_narrative")
    class_reports = {}
    for class_name, group in split_session_analysis_by_class(data).items():
        if not group.students:
            continue
        context.raise_if_cancelled()
        narrative = _class_narrative(
            client=client,
            cache=AnalysisNarrativeCache(Path(reports_dir) / NARRATIVE_CACHE_DIRNAME),
            session_id=session_id, revision=revision, class_name=class_name,
            prompt=build_report_prompt(CLASS_SYSTEM_PROMPT, build_class_payload(group)),
        )
        class_reports[class_name] = {
            "status": "ready" if narrative is not None else "failed",
            "narrative": narrative,
        }
    context.raise_if_cancelled()
    status = "ready" if all(item["status"] == "ready" for item in class_reports.values()) else "failed"
    store.save(
        session_id, status=status,
        narrative=next(iter(class_reports.values()))["narrative"] if len(class_reports) == 1 else None,
        narrative_error=None if status == "ready" else "AI 分析生成失败，可重新生成",
        score_revision=revision, generated_at=generated_at, small_sample=data.small_sample,
        class_reports=class_reports, rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
    )
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
    revision = score_revision(repositories, session_id, include_question_bank=False)
    if (
        existing is not None
        and existing.get("status") == "ready"
        and existing.get("score_revision") == revision
        and existing.get("rendition_version") == CLASS_ANALYSIS_RENDITION_VERSION
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
            class_reports={},
            rendition_version=CLASS_ANALYSIS_RENDITION_VERSION,
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
