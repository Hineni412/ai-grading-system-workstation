"""AI 补答案草稿：只为还没有答案的题生成「答案 + 解析」草稿。

写入一律带 `needs_review=1` 与来源标记，由教师复核后才算正式答案；
`apply_draft` 用条件 UPDATE 保证已有答案的题目绝不被覆盖。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from question_bank.database.schema import connect
from question_bank.services.ai_tagging_service import classify_tagging_error

LOGGER = logging.getLogger(__name__)

AI_DRAFT_MARKER = "【AI 生成，待教师核对】"
MODEL_NOT_CONFIGURED_CATEGORY = "model_not_configured"


def format_draft_answer_text(draft: dict[str, Any]) -> str:
    """把模型草稿组装成存入 questions.answer_text 的文本。"""
    answer = str(draft.get("answer") or "").strip()
    analysis = str(draft.get("analysis") or "").strip()
    lines = [AI_DRAFT_MARKER, f"答案：{answer}"]
    if analysis:
        lines.append(f"解析：{analysis}")
    return "\n".join(lines)


class AnswerDraftService:
    def __init__(
        self,
        db_path: Path,
        *,
        data_root: Path | None = None,
        llm_client: Any | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root) if data_root is not None else None
        if llm_client is None:
            # 「内容生成」任务绑定的模型档案，与报告导出共用；
            # settings 为 None 表示教师还没在设置页配置模型。
            from backend.model_profiles.content_generation import (
                resolve_content_generation_settings,
            )

            settings = resolve_content_generation_settings()
            if settings is not None:
                from backend.llm.llm_client import LLMClient

                llm_client = LLMClient(settings)
        self._client = llm_client

    def load_draft_candidates(
        self,
        question_ids: list[int],
    ) -> dict[int, dict[str, Any]]:
        """返回仍需补答案的题：未删除且 answer_text 为空。"""
        ids = sorted({int(item) for item in question_ids if int(item) > 0})
        if not ids:
            return {}
        placeholders = ", ".join("?" for _ in ids)
        with connect(self.db_path) as conn:
            rows = conn.execute(
                f"""
                SELECT id, question_number, question_type, question_text,
                       difficulty
                FROM questions
                WHERE id IN ({placeholders})
                  AND COALESCE(is_deleted, 0) = 0
                  AND (answer_text IS NULL OR trim(answer_text) = '')
                """,
                ids,
            ).fetchall()
        return {
            int(row["id"]): {
                "question_id": int(row["id"]),
                "question_number": row["question_number"],
                "question_type": row["question_type"],
                "question_text": row["question_text"],
                "difficulty": row["difficulty"],
            }
            for row in rows
        }

    def draft_answer(self, question: dict[str, Any]) -> dict[str, str]:
        """对单题调用模型，返回 {"answer": ..., "analysis": ...}。"""
        if self._client is None:
            raise RuntimeError("answer draft model is not configured")
        question_text = str(question.get("question_text") or "").strip()
        if not question_text:
            raise ValueError("question text is missing")
        question_type = (
            str(question.get("question_type") or "").strip() or "未标注题型"
        )
        prompt = (
            "你是一位严谨的中学数学老师。请为下面这道题给出正确答案和简明解析。\n\n"
            f"题型：{question_type}\n"
            f"题目：\n{question_text}\n\n"
            "只输出一个 JSON 对象，不要输出任何其他内容：\n"
            '{"answer": "最终答案", "analysis": "解题过程与解析"}\n'
            "answer 写这道题的最终答案（选择题给选项字母，填空题给结果，"
            "解答题给关键结论）；analysis 写清解题步骤。"
            "如果题目信息不足、无法给出可靠答案，answer 填空字符串。"
        )
        payload = self._client.json_from_text(prompt)
        answer = str((payload or {}).get("answer") or "").strip()
        analysis = str((payload or {}).get("analysis") or "").strip()
        if not answer:
            raise ValueError(
                "model response did not include a usable answer"
            )
        return {"answer": answer, "analysis": analysis}

    def apply_draft(self, question_id: int, answer_text: str) -> bool:
        """条件写入草稿；已有答案或已删除的题返回 False 且绝不覆盖。"""
        text = str(answer_text or "").strip()
        if not text:
            raise ValueError("answer_text must be nonblank")
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                UPDATE questions
                SET answer_text = ?, needs_review = 1,
                    updated_at = datetime('now','localtime')
                WHERE id = ?
                  AND COALESCE(is_deleted, 0) = 0
                  AND (answer_text IS NULL OR trim(answer_text) = '')
                """,
                (text, int(question_id)),
            )
            applied = cursor.rowcount > 0
        return applied

    def draft_for_questions(
        self,
        question_ids: list[int],
        *,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict[str, object]:
        """逐题生成并写入草稿，互不影响地收集成功/失败/跳过。"""
        candidates = self.load_draft_candidates(question_ids)
        successful: list[int] = []
        skipped: list[int] = []
        failed: list[dict[str, object]] = []
        for raw_id in question_ids:
            question_id = int(raw_id)
            if cancel_check is not None:
                cancel_check()
            question = candidates.get(question_id)
            if question is None:
                # 已有答案或已删除/不存在：不调用模型，直接跳过。
                skipped.append(question_id)
                continue
            if self._client is None:
                failed.append(
                    {
                        "question_id": question_id,
                        "category": MODEL_NOT_CONFIGURED_CATEGORY,
                    }
                )
                continue
            try:
                draft = self.draft_answer(question)
                applied = self.apply_draft(
                    question_id,
                    format_draft_answer_text(draft),
                )
            except Exception as exc:
                failed.append(
                    {
                        "question_id": question_id,
                        "category": classify_tagging_error(exc),
                    }
                )
                continue
            if applied:
                successful.append(question_id)
            else:
                # 读取候选后另一作业已写入答案：条件更新挡下，按跳过计。
                skipped.append(question_id)
        return {
            "successful": successful,
            "failed": failed,
            "skipped": skipped,
        }


__all__ = [
    "AI_DRAFT_MARKER",
    "MODEL_NOT_CONFIGURED_CATEGORY",
    "AnswerDraftService",
    "format_draft_answer_text",
]
