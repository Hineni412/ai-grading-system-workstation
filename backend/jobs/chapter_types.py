"""One authorized chapter grouping request per chapter, followed by atomic publication."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.files.data_transfer_service import ensure_controlled_path
from question_bank.atomic_files import write_json_atomic
from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
from question_bank.knowledge_graph_release.repository import load_active_release
from question_bank.services.chapter_type_service import (
    ChapterTypeInvalid, authorized_chapter_inputs, build_chapter_release,
    chapter_model_payload, publish_chapter_release, validate_chapter_result,
)


def run_chapter_type_job(*, context, question_bank_db_path: Path, data_root: Path,
                        ai_service_factory, publication_guard) -> dict[str, Any]:
    authorization = context.payload.get("authorization")
    if not isinstance(authorization, dict):
        raise ChapterTypeInvalid("题型整理缺少费用授权")
    path = Path(data_root) / "question_bank" / "chapter_type_runs" / f"job-{int(context.job_id)}.json"
    ensure_controlled_path(path, Path(data_root) / "question_bank")
    state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "authorization_fingerprint": authorization.get("input_fingerprint"), "requests": [], "status": "pending"}
    if state.get("authorization_fingerprint") != authorization.get("input_fingerprint"):
        raise ChapterTypeInvalid("任务编号对应的题型整理授权发生变化")
    if state.get("status") == "published":
        return dict(state["summary"])
    active = load_active_release(question_bank_db_path)
    if state.get("candidate") and active and active.release_id == state["candidate"]["release_id"]:
        state["status"] = "published"
        write_json_atomic(path, state)
        return dict(state["summary"])
    if any(row.get("status") in {"running", "outcome_unknown", "failed"} for row in state["requests"]):
        for row in state["requests"]:
            if row.get("status") == "running":
                row["status"] = "outcome_unknown"
        state["status"] = "outcome_unknown"
        write_json_atomic(path, state)
        return {"outcome": "outcome_unknown", "volume_id": authorization.get("volume_id"),
            "published_count": 0, "model_calls": len(state["requests"]), "retryable": False,
            "reason": "已有请求结果未知或失败，本次不会自动追加费用；原活动标准保留。"}
    context.raise_if_cancelled()
    with publication_guard():
        base, volume, ready = authorized_chapter_inputs(db_path=question_bank_db_path,
            data_root=data_root, authorization=authorization)
    if not ready:
        return {"outcome": "skipped", "volume_id": authorization.get("volume_id"), "published_count": 0,
                "model_calls": 0, "chapter_summaries": [], "reason": "已授权章节尚未达到实际可用题门槛。"}
    gateway = None
    chapter_results = []
    try:
        for index, (chapter, rows) in enumerate(ready):
            context.raise_if_cancelled()
            payload = chapter_model_payload(base, volume, chapter, rows)
            prior = next((item for item in state["requests"] if item["chapter_id"] == chapter["chapter_id"]), None)
            if prior:
                response = validate_chapter_result(prior["response"], payload)
            else:
                if len(state["requests"]) >= authorization["request_limit"]:
                    raise ChapterTypeInvalid("本次已达到确认请求上限，未追加请求")
                if gateway is None:
                    gateway = ai_service_factory()
                organize = getattr(gateway, "organize_chapter_types", None)
                if not callable(organize):
                    raise ChapterTypeInvalid("题库模型不支持章节题型整理，未发起请求")
                record = {"chapter_id": chapter["chapter_id"], "status": "running"}
                state["requests"].append(record)
                state["status"] = "running"
                write_json_atomic(path, state)
                context.report(index / len(ready), "整理本章题型", f"正在整理第 {index + 1} 个已授权章节。")
                try:
                    raw = organize(payload)
                except Exception:
                    record["status"] = "outcome_unknown"
                    write_json_atomic(path, state)
                    raise ChapterTypeInvalid("题型整理请求结果未知，不自动重发；原活动标准保留。")
                record.update(status="received", response=raw)
                write_json_atomic(path, state)
                try:
                    response = validate_chapter_result(raw, payload)
                except Exception:
                    record["status"] = "failed"
                    write_json_atomic(path, state)
                    raise
                record["status"] = "validated"
                write_json_atomic(path, state)
            chapter_results.append((chapter, rows, response))
        from question_bank.services.chapter_type_service import _reading
        with _reading(question_bank_db_path) as connection:
            revision = max(13, int(connection.execute("SELECT COALESCE(MAX(taxonomy_revision),0) FROM knowledge_graph_releases").fetchone()[0]) + 1)
        candidate, catalog, labels = build_chapter_release(base, chapter_results, taxonomy_revision=revision)
        summary = {"outcome": "published", "volume_id": authorization.get("volume_id"),
            "release_id": candidate.release_id, "published_count": len(labels),
            "model_calls": len(state["requests"]), "retryable": False,
            "chapter_summaries": [{"chapter_id": chapter["chapter_id"], "label": chapter["label"],
                "new_type_count": len(response["types"]), "question_count": len(response["assignments"]),
                "unclassified_count": 0,
                "types": [{"name": row["name"], "question_count": sum(item["primary_type_id"] == row["type_id"]
                    for item in response["assignments"])} for row in response["types"]]}
                for chapter, rows, response in chapter_results]}
        state.update(candidate=candidate.to_dict(), summary=summary, status="ready_to_publish")
        write_json_atomic(path, state)
        context.report(0.9, "等待安全发布", "等待已排队及进行中的题目分析完成。")
        with publication_guard():
            context.raise_if_cancelled()
            publish_chapter_release(db_path=question_bank_db_path, data_root=data_root,
                base_release_id=base.release_id, candidate=candidate, catalog=catalog, labels=labels)
        state["status"] = "published"
        write_json_atomic(path, state)
        write_json_atomic(path.with_name(f"job-{int(context.job_id)}-summary.json"), summary)
        context.report(1.0, "题型已发布", "已自动发布本章题型及题目分类。")
        return summary
    except ChapterTypeInvalid as exc:
        state["status"] = "failed"
        write_json_atomic(path, state)
        return {"outcome": "failed", "volume_id": authorization.get("volume_id"), "published_count": 0,
            "model_calls": len(state["requests"]), "chapter_summaries": [], "retryable": False, "reason": str(exc)}