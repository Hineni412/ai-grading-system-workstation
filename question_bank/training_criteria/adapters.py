from __future__ import annotations

import json
import mimetypes
import re
import sqlite3
import threading
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from backend.llm import LLMRequestKind, usage_fields
from backend.llm.policy import policy_from_profile
from question_bank.database.schema import connect
from question_bank.models.tag_schema import DIFFICULTY_SCALE_GUIDANCE, TagAnalysis, TaggingContext
from question_bank.models.question import CORE_ANALYSIS_TAG_TYPES
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    _with_quality,
    is_auto_saveable_result,
)
from question_bank.services.asset_path_service import (
    resolve_question_bank_asset_path,
)
from question_bank.services.question_write_service import QuestionBankWriteService
from question_bank.services.rich_content_service import (
    load_question_rich_content,
)
from backend.llm.json_repair import parse_json_object_locally
from question_bank.training_criteria.analysis import (
    AnalysisProjection,
    GatewayBatchResponse,
    GatewayResponseParseError,
    GatewayUsage,
    PlannedAnalysisBatch,
    QuestionAnalysisImage,
    QuestionAnalysisInput,
    QuestionTypeSuggestion,
    TaxonomyProjectionReviewRequired,
    combined_response_format,
    controlled_term_ids_from_questions,
)


_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>[^\]]+)\]\]")
_SOURCE_SECTION_HEADING = re.compile(
    r"^\s*[一二三四五六七八九十百]+[、.．]\s*"
    r"(?:单项选择题|选择题|填空题|解答题|计算题|证明题|作图题|综合题|判断题|简答题)"
    r"(?:\s*[（(].*)?$"
)


class ExistingTagProjectionWriter:
    """Adapter that keeps combined tags on the established tag seam."""

    def __init__(
        self,
        *,
        write_service: QuestionBankWriteService,
        tagging_service: AITaggingService,
    ) -> None:
        self.write_service = write_service
        self.tagging_service = tagging_service
        self._audit_lock = threading.Lock()
        self._audits: dict[tuple[str, int], dict[str, Any]] = {}

    def write(
        self,
        question: QuestionAnalysisInput,
        payload: Mapping[str, Any],
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        analysis = TagAnalysis.from_dict(dict(payload))
        contract = question.taxonomy_snapshot
        if not contract:
            raise ValueError("question taxonomy snapshot is unavailable")
        checked = _with_quality(
            AITaggingResult(
                ok=True,
                mock_mode=False,
                analysis=analysis,
                model_name=model_name,
            ),
            question.tagging_context,
            governance=self.tagging_service.taxonomy_governance,
            taxonomy_contract=contract,
            question_ref=str(question.question_id),
        )
        if not is_auto_saveable_result(checked):
            reasons = "；".join(
                str(note).strip()
                for note in checked.quality_notes
                if str(note).strip()
            )
            raise TaxonomyProjectionReviewRequired(
                "tag projection requires taxonomy review"
                + (f": {reasons}" if reasons else "")
            )
        assert checked.analysis is not None
        persisted_proposals: list[dict[str, Any]] = []
        if checked.proposals:
            governance_payload = checked.analysis.to_dict()
            governance_payload["proposed_tags"] = [
                *governance_payload.get("proposed_tags", []),
                *checked.proposals,
            ]
            governed = self.tagging_service.taxonomy_governance.constrain(
                governance_payload,
                context={
                    "persist_proposals": True,
                    "question_ref": str(question.question_id),
                    "model": str(model_name or ""),
                    "request_token": (
                        f"combined-tag:{operation_id}:question:"
                        f"{question.question_id}:taxonomy:{checked.taxonomy_revision}:"
                        "candidates:"
                        f"{contract.candidate_fingerprint or 'none'}"
                    ),
                    "expected_revision": contract.taxonomy_revision,
                    "allowed_term_ids": contract.get("allowed_term_ids", {}),
                    "knowledge_catalog_revision": contract.get(
                        "knowledge_catalog_revision"
                    ),
                },
            )
            persisted_proposals = [
                dict(item)
                for item in governed.get("proposals", [])
                if isinstance(item, Mapping)
            ]
        with self._audit_lock:
            self._audits[(str(operation_id), question.question_id)] = {
                "retrieval_misses": [
                    dict(item)
                    for item in checked.retrieval_misses
                    if isinstance(item, Mapping)
                ],
                "proposals": persisted_proposals,
            }
        if not self.write_service.save_tag_analysis(
            question.question_id,
            checked.analysis,
            model_name=model_name,
            confidence=checked.analysis.confidence,
            taxonomy_governance=self.tagging_service.taxonomy_governance,
        ):
            raise RuntimeError("tag projection could not be saved")
        placeholders = ", ".join("?" for _ in CORE_ANALYSIS_TAG_TYPES)
        with connect(self.write_service.db_path) as connection:
            stored_question = connection.execute(
                "SELECT difficulty FROM questions WHERE id = ? AND is_deleted = 0",
                (int(question.question_id),),
            ).fetchone()
            stored_types = {
                str(row["tag_type"])
                for row in connection.execute(
                    f"""
                    SELECT DISTINCT tag_type
                    FROM question_tags
                    WHERE question_id = ?
                      AND tag_type IN ({placeholders})
                      AND TRIM(tag_value) <> ''
                    """,
                    (int(question.question_id), *CORE_ANALYSIS_TAG_TYPES),
                ).fetchall()
            }
        try:
            stored_difficulty = float(
                stored_question["difficulty"] if stored_question else 0
            )
        except (TypeError, ValueError):
            stored_difficulty = 0
        if (
            stored_question is None
            or not set(CORE_ANALYSIS_TAG_TYPES).issubset(stored_types)
            or not 1 <= stored_difficulty <= 10
        ):
            if persisted_proposals:
                raise TaxonomyProjectionReviewRequired(
                    "tag projection requires taxonomy review"
                )
            raise ValueError("persisted core tags remain incomplete")
        return {
            "schema_version": "tag-only-v1",
            "analysis": checked.analysis.to_dict(),
            "quality_status": checked.quality_status,
            "taxonomy_revision": checked.taxonomy_revision,
            "proposal_count": len(persisted_proposals),
            "operation_id": operation_id,
        }

    def audit_summary(
        self,
        operation_id: str,
        question_ids: Sequence[int],
    ) -> dict[str, Any]:
        retrieval_misses: list[dict[str, Any]] = []
        proposals: list[dict[str, Any]] = []
        retrieval_question_ids: list[int] = []
        proposal_question_ids: list[int] = []
        with self._audit_lock:
            rows = [
                (
                    int(question_id),
                    self._audits.get((str(operation_id), int(question_id))),
                )
                for question_id in question_ids
            ]
        for question_id, audit in rows:
            if not isinstance(audit, Mapping):
                continue
            misses = [
                dict(item)
                for item in audit.get("retrieval_misses", [])
                if isinstance(item, Mapping)
            ]
            observed = [
                dict(item)
                for item in audit.get("proposals", [])
                if isinstance(item, Mapping)
            ]
            retrieval_misses.extend(misses)
            proposals.extend(observed)
            if misses:
                retrieval_question_ids.append(question_id)
            if observed:
                proposal_question_ids.append(question_id)
        return {
            "retrieval_misses": retrieval_misses,
            "proposals": proposals,
            "retrieval_miss_question_ids": retrieval_question_ids,
            "proposal_question_ids": proposal_question_ids,
        }


class BankQuestionTypeSuggestionWriter:
    """Adapter that lands model question-type suggestions on the write seam."""

    def __init__(self, *, write_service: QuestionBankWriteService) -> None:
        self.write_service = write_service

    def apply(
        self,
        question: QuestionAnalysisInput,
        suggestion: QuestionTypeSuggestion,
        *,
        model_name: str,
        operation_id: str,
    ) -> Mapping[str, Any]:
        return self.write_service.apply_question_type_suggestion(
            question.question_id,
            suggested_type=suggestion.question_type,
            suggested_subtype=suggestion.essay_subtype,
            question_type_confirmed=question.question_type_confirmed,
            reason=suggestion.reason,
            model_name=model_name,
            operation_id=operation_id,
        )


def combined_analysis_retry_budget(tagging_service: Any) -> int:
    """Channel automatic-retry budget shared by transport and feedback retries.

    Resolved from the tagging service's policy profile so the runner-level
    feedback retry count always equals the gateway transport retry count for
    the same channel.
    """
    profile = getattr(tagging_service, "_tagging_policy_profile", None)
    policy = policy_from_profile(
        LLMRequestKind.TAGGING,
        profile if isinstance(profile, Mapping) else {},
    )
    return int(policy.max_retries)


class OpenAICombinedAnalysisGateway:
    """Production adapter for the combined tagging analysis.

    Transport-level retries (429/timeout/connection/5xx) follow the channel
    policy via the gateway; the channel attribute ``max_auto_retries`` bounds
    them.  Local parse/validation feedback retries live in the analysis
    runner, bounded by :attr:`max_auto_retries` as well.
    """

    def __init__(
        self,
        *,
        protocol_adapter: Any,
        model_name: str,
        max_auto_retries: int = 0,
    ) -> None:
        self.protocol_adapter = protocol_adapter
        self.model_name = str(model_name or "").strip()
        if not self.model_name:
            raise ValueError("model_name must not be empty")
        if isinstance(max_auto_retries, bool) or not isinstance(
            max_auto_retries, int
        ):
            raise ValueError("max_auto_retries must be an integer")
        if not 0 <= max_auto_retries <= 5:
            raise ValueError("max_auto_retries must be between 0 and 5")
        self.max_auto_retries = max_auto_retries

    @property
    def max_parallel_requests(self) -> int:
        gateway = getattr(self.protocol_adapter, "gateway", None)
        snapshot = getattr(gateway, "execution_snapshot", None)
        value = None
        governors = getattr(gateway, "governors", None)
        status = getattr(governors, "status", None)
        if callable(status):
            try:
                current = status(
                    getattr(gateway, "governor_scope", ""),
                    snapshot,
                )
            except Exception:
                current = None
            if isinstance(current, Mapping):
                value = current.get("effective_max_in_flight")
        if value is None:
            value = getattr(snapshot, "max_in_flight", 1)
        try:
            return max(1, min(100, int(value)))
        except (TypeError, ValueError):
            return 1

    def analyze(
        self,
        batch: PlannedAnalysisBatch,
        *,
        projection: AnalysisProjection,
        operation_id: str,
        request_id: str,
    ) -> GatewayBatchResponse:
        started = perf_counter()
        response = self.protocol_adapter.responses(
            request_kind=LLMRequestKind.TAGGING,
            model=self.model_name,
            request_id=request_id,
            operation_id=operation_id,
            allow_retry=self.max_auto_retries > 0,
            kwargs={
                "text": {
                    "format": combined_response_format(
                        projection,
                        allowed_term_ids=controlled_term_ids_from_questions(
                            batch.questions
                        ),
                    )
                },
                "input": _combined_prompt(batch, projection),
            },
        )
        output_text = str(
            getattr(response, "output_text", "") or ""
        ).strip()
        try:
            parsed = parse_json_object_locally(output_text)
            payload = parsed.payload
        except (TypeError, ValueError) as exc:
            raise GatewayResponseParseError(
                "combined model response JSON parsing failed",
                raw_text=output_text,
            ) from exc
        if not isinstance(payload, Mapping):
            raise GatewayResponseParseError(
                "combined model response JSON must be an object",
                raw_text=output_text,
            )
        normalized_usage = usage_fields(response)
        return GatewayBatchResponse(
            payload=dict(payload),
            model_name=self.model_name,
            usage=GatewayUsage(
                prompt_tokens=int(
                    normalized_usage.get("prompt_tokens") or 0
                ),
                completion_tokens=int(
                    normalized_usage.get("completion_tokens") or 0
                ),
                total_tokens=int(
                    normalized_usage.get("total_tokens") or 0
                ),
            ),
            latency_ms=int(
                round(max(perf_counter() - started, 0.0) * 1000)
            ),
        )


class QuestionAnalysisInputLoader:
    """Load controlled rich text and actual image bodies for the module."""

    def __init__(self, *, db_path: Path, data_root: Path,
                 external_connection: sqlite3.Connection | None = None) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)
        self.external_connection = external_connection

    def load(
        self,
        question_ids: Sequence[int],
        *,
        taxonomy_contracts: Mapping[int, Mapping[str, Any]] | None = None,
        curriculum_volume_id: str | None = None,
    ) -> tuple[QuestionAnalysisInput, ...]:
        ids = tuple(dict.fromkeys(int(value) for value in question_ids))
        if not ids or any(value <= 0 for value in ids):
            raise ValueError("question_ids must contain positive integers")
        placeholders = ",".join("?" for _ in ids)
        with connect(self.db_path, external_connection=self.external_connection) as connection:
            rows = connection.execute(
                f"""
                SELECT q.id, q.question_text, q.answer_text,
                       q.question_number, q.question_type,
                       q.image_paths, q.has_images, q.is_deleted,
                       p.grade, p.semester, p.textbook_version,
                       p.exam_type, p.district
                FROM questions q
                LEFT JOIN papers p ON p.id = q.paper_id
                WHERE q.id IN ({placeholders})
                """,
                ids,
            ).fetchall()
            # 解答题子类标签随题一并加载，供题组划分（证明/画图单题成批）；
            # 只进 TaggingContext，不进入任何模型提示词。
            special_type_rows = connection.execute(
                f"""
                SELECT question_id, tag_value
                FROM question_tags
                WHERE tag_type = 'special_type'
                  AND question_id IN ({placeholders})
                ORDER BY question_id, id
                """,
                ids,
            ).fetchall()
        special_types_by_id: dict[int, list[str]] = {}
        for tag_row in special_type_rows:
            bucket = special_types_by_id.setdefault(int(tag_row["question_id"]), [])
            tag_value = str(tag_row["tag_value"] or "").strip()
            if tag_value and tag_value not in bucket:
                bucket.append(tag_value)
        by_id = {int(row["id"]): row for row in rows}
        result: list[QuestionAnalysisInput] = []
        for question_id in ids:
            row = by_id.get(question_id)
            if row is None or bool(row["is_deleted"]):
                raise KeyError(question_id)
            rich = load_question_rich_content(
                question_id,
                root=self.data_root / "question_bank" / "rich_content",
            ) or {}
            question_blocks = _safe_blocks(rich.get("question_blocks"))
            answer_blocks = _safe_blocks(rich.get("answer_blocks"))
            question_paths = _dedupe_paths(
                [
                    *_stored_paths(row["image_paths"]),
                    *_marker_paths(row["question_text"]),
                    *_rich_paths(question_blocks),
                ]
            )
            answer_paths = _dedupe_paths(
                [
                    *_marker_paths(row["answer_text"]),
                    *_rich_paths(answer_blocks),
                ]
            )
            image_hashes_by_path: dict[str, str] = {}
            question_images = self._images(
                question_paths,
                role="question",
                image_hashes_by_path=image_hashes_by_path,
            )
            answer_images = self._images(
                answer_paths,
                role="answer",
                image_hashes_by_path=image_hashes_by_path,
            )
            # Some DOCX/PDF extractors attach a diagram to the answer-side
            # rich blocks even though it is the figure the question refers to.
            # Keep the original answer image and expose a question-role copy
            # when no question-side body could be resolved, so the structural
            # image gate does not reject an otherwise usable question.
            if bool(row["has_images"]) and not question_images and answer_images:
                question_images = [
                    QuestionAnalysisImage(
                        role="question",
                        mime_type=image.mime_type,
                        content=image.content,
                    )
                    for image in answer_images
                ]
            images = tuple([*question_images, *answer_images])
            has_images = bool(row["has_images"]) or bool(
                question_paths or answer_paths
            )
            result.append(
                QuestionAnalysisInput(
                    question_id=question_id,
                    tagging_context=TaggingContext(
                        question_text=str(row["question_text"] or ""),
                        answer_text=str(row["answer_text"] or ""),
                        question_number=str(
                            row["question_number"] or ""
                        ),
                        question_type=str(row["question_type"] or ""),
                        grade=str(row["grade"] or ""),
                        semester=str(row["semester"] or ""),
                        textbook_version=str(
                            row["textbook_version"] or ""
                        ),
                        curriculum_volume_id=str(
                            curriculum_volume_id or ""
                        ).strip(),
                        exam_type=str(row["exam_type"] or ""),
                        district=str(row["district"] or ""),
                        has_images=has_images,
                        existing_tags_by_dimension=(
                            {"special_type": special_types_by_id[question_id]}
                            if question_id in special_types_by_id
                            else {}
                        ),
                    ),
                    rich_question_blocks=tuple(
                        _public_block(item) for item in question_blocks
                    ),
                    rich_answer_blocks=tuple(
                        _public_block(item) for item in answer_blocks
                    ),
                    word_question_blocks=tuple(
                        _word_block(item, image_hashes_by_path)
                        for item in _question_content_blocks(question_blocks)
                    ),
                    word_answer_blocks=tuple(
                        _word_block(item, image_hashes_by_path)
                        for item in answer_blocks
                    ),
                    images=images,
                    taxonomy_contract=(
                        (taxonomy_contracts or {}).get(question_id, {})
                    ),
                )
            )
        return tuple(result)

    def _images(
        self,
        paths: Sequence[str],
        *,
        role: str,
        image_hashes_by_path: dict[str, str] | None = None,
    ) -> list[QuestionAnalysisImage]:
        result: list[QuestionAnalysisImage] = []
        for saved_path in paths:
            try:
                resolved = resolve_question_bank_asset_path(
                    saved_path,
                    data_root=self.data_root,
                    search_subdirs=(
                        "question_bank/extracted_images",
                        "question_bank/previews",
                    ),
                )
            except (OSError, ValueError):
                continue
            if not resolved.is_file():
                continue
            mime = (
                mimetypes.guess_type(resolved.name)[0]
                or "application/octet-stream"
            )
            try:
                image = QuestionAnalysisImage(
                    role=role,  # type: ignore[arg-type]
                    mime_type=mime,
                    content=resolved.read_bytes(),
                )
                result.append(image)
                if image_hashes_by_path is not None:
                    image_hashes_by_path[str(saved_path)] = image.sha256
            except (OSError, ValueError):
                continue
        return result


def question_analysis_input_from_config_source(
    source: Mapping[str, Any],
    *,
    question_id: int,
    curriculum_volume_id: str,
    taxonomy_contract: Mapping[str, Any] | None = None,
    images: Sequence[QuestionAnalysisImage] = (),
) -> QuestionAnalysisInput:
    """Adapt an in-memory config question/answer block without writing it first."""

    question_text = _first_source_text(
        source,
        "question_text",
        "text",
        "content",
        "stem",
    )
    if not question_text:
        raise ValueError("config source question text must not be empty")
    answer_text = _first_source_text(
        source,
        "answer_text",
        "reference_answer",
        "answer",
        "canonical_answer",
    )
    question_blocks = _safe_blocks(
        source.get("rich_question_blocks")
        or source.get("question_blocks")
    )
    answer_blocks = _safe_blocks(
        source.get("rich_answer_blocks")
        or source.get("answer_blocks")
    )
    reference_solution = source.get("reference_solution")
    normalized_images = tuple(images)
    volume_id = str(curriculum_volume_id or "").strip()
    if not volume_id:
        raise ValueError("curriculum_volume_id must be selected before analysis")
    return QuestionAnalysisInput(
        question_id=int(question_id),
        question_type_confirmed=(
            source.get("question_type_confirmed") is True
        ),
        tagging_context=TaggingContext(
            question_text=question_text,
            answer_text=answer_text,
            question_number=_first_source_text(
                source,
                "question_number",
                "question_id",
                "id",
            ),
            question_type=_first_source_text(
                source,
                "question_type",
                "type",
            ),
            grade=_first_source_text(source, "grade"),
            semester=_first_source_text(source, "semester"),
            textbook_version=_first_source_text(
                source,
                "textbook_version",
            ),
            curriculum_volume_id=volume_id,
            exam_type=_first_source_text(source, "exam_type"),
            district=_first_source_text(source, "district"),
            has_images=bool(normalized_images or source.get("has_images")),
        ),
        # Config-source manifests may contain controlled local asset names.
        # Images are attached as bodies below, so only path-free text is sent
        # to an external model.
        rich_question_blocks=tuple(_public_block(item) for item in question_blocks),
        rich_answer_blocks=tuple(_public_block(item) for item in answer_blocks),
        word_question_blocks=tuple(
            _word_block(item, {})
            for item in _question_content_blocks(question_blocks)
        ),
        word_answer_blocks=tuple(_word_block(item, {}) for item in answer_blocks),
        images=normalized_images,
        taxonomy_contract=taxonomy_contract or {},
        reference_solution=(
            dict(reference_solution)
            if isinstance(reference_solution, Mapping)
            else {}
        ),
    )


def _prompt_candidate_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "schema_version",
        "taxonomy_revision",
        "knowledge_graph_release_id",
        "allowed_dimensions",
        "rules",
    ):
        if key in contract:
            compact[key] = contract[key]
    candidates = contract.get("candidates")
    if isinstance(candidates, Mapping):
        compact["candidates"] = {
            str(dimension): [
                {
                    "id": str(item.get("id") or ""),
                    "name": str(item.get("name") or ""),
                }
                for item in items
                if isinstance(item, Mapping) and str(item.get("id") or "").strip()
            ]
            for dimension, items in candidates.items()
            if isinstance(items, list)
        }
    volume = contract.get("curriculum_volume")
    if isinstance(volume, Mapping):
        sections: list[dict[str, str]] = []
        raw_sections = volume.get("sections")
        if isinstance(raw_sections, list):
            for item in raw_sections:
                if not isinstance(item, Mapping):
                    continue
                item_id = str(item.get("id") or "").strip()
                if not item_id:
                    continue
                sections.append(
                    {
                        "id": item_id,
                        "name": str(
                            item.get("name") or item.get("label") or ""
                        ),
                        "chapter_name": str(item.get("chapter_name") or ""),
                    }
                )
        compact["curriculum_volume"] = {
            "id": volume.get("id"),
            "label": volume.get("label"),
            "sections": sections,
        }
    return compact


def _combined_prompt(
    batch: PlannedAnalysisBatch,
    projection: AnalysisProjection,
) -> list[dict[str, Any]]:
    instructions = (
        "只分析列出的初中数学题。每个列出的 question_id 必须恰好返回一条结果，"
        "并把该整数原样写入 result 与 solution_evidence，不得串题或混用候选。"
        "一道题只返回一份 result；多问大题的小问只能拆在该结果内部的 "
        "solution_evidence.parts 里，禁止按小问拆成多条 result。"
        "除公式、变量、选项字母、机器标识和原答案片段外，所有教师可见自由文本"
        "必须使用简体中文；返回英文说明即为失败。"
        "候选知识表是所选册别及以前册别的完整教材目录树，必须先在整棵树中按稳定 ID 选择，"
        "不得因题干措辞不同而新造近义知识词。"
        "tag_analysis 的 knowledge_points、prerequisite_points、method_tags、"
        "thought_tags、ability_tags、math_model_tags、special_type_tags "
        "只能逐字照抄该题 candidate_contract 中对应维度候选条的 id，不得填写名称、"
        "改写或自造；curriculum_sections 只填 curriculum_volume 中的小节 id。"
        "解题证据使用 question-solution-evidence-v2，并拆成 question parts。"
        "每个独立可评分的数学台阶对应恰好一个 evidence point；推导中有多个有意义"
        "中间结果时，每个结果各占一个 evidence point，不得把整段推导塞进同一个 "
        "target 或 observable_evidence。不要拆无意义的代数书写，也不要把同一结论"
        "再说一遍当成新台阶。真正原子的答案可以只有一个 evidence point，但必须使用"
        "匹配的非过程 response_mode。每个 process_required 的 part 至少包含两个"
        "互不相同、非空的 evidence point。严格遵循本任务提供的正例和反例；反例明确禁止。"
        "定理或判定的适用前提、必要条件（直角/垂直/平行、全等或相似的对应关系、"
        "取值范围、分母不为零等）必须单列为独立 evidence point，不得与“列式”“代入”"
        "合并为一点；该点 target 写明要求成立的数学含义，observable_evidence 写学生"
        "可写出的任一书面形式并注明“任一即可”（例：∠OEB=90°、OE⊥EB、"
        "△OEB 为直角三角形、图中直角标记并在推理中引用）。每个 evidence point 是"
        "整点有无的判定单位，粒度以“教师会否为此单独判未达成”为准：有独立教学价值的"
        "中间结论、条件、结论各占一点。"
        "每个点的 step_index 从 1 起并与数组顺序一致；justification 必须写出支撑该步的"
        "条件、定理、性质或运算；answer_anchor 从答案来源原样抄写能唯一标识该台阶的"
        "最短结果或运算。exact_objective 的答案来源是 canonical_answer，full_answer 可空；"
        "其他 response_mode 的答案来源是 full_answer。过程锚点必须按证据点顺序出现。"
        "depends_on 只能引用同一 part 中更早的 evidence_point_id。每个 part 独立命名空间，"
        "不得把其他 part 的 id 写入 depends_on；后问用到前问结论时，写在 justification "
        "或 auxiliary_rules。返回前必须完成 pre_output_checklist 全部项。"
        "part_id 与 evidence_point_id 必须是全题唯一的小写 ASCII 机器标识，匹配 "
        "^[a-z][a-z0-9_-]{1,127}$；优先 part-1、part-1-step-1。"
        "每个 evidence point 的 fine_term_links 只能引用该题 "
        "candidate_contract.candidates.knowledge 中 usage 不是 retrieval_only "
        "或 do_not_use_as_knowledge 的候选条（技能与本册小节可链接；情境叶子仅供检索），"
        "且必须把同一候选条的 id 与 name "
        "成对原样照抄；不得用其他维度、拟议标签、改写或自造词。没有完全匹配的受控知识时"
        "返回空数组，真正新词只放在 tag_analysis.proposed_tags 供人工审核。"
        "每个链接标注 direct 或 supporting_prerequisite，同一 (id, role) 不得在一个 "
        "evidence point 内重复。不要推断或返回核心图谱映射。"
        "每个 part 还要返回 response_mode、canonical_answer、full_answer、accepted_forms、"
        "证明与作图义务、非空 deduction_policy，以及是否允许其他解法。"
        "评分证据描述必须区分数学义务与参考答案的展开形式：answer_anchor 仅定位参考解答，"
        "不是要求学生逐字复现的答案模板。target 和 observable_evidence 应写需要成立的"
        "条件、关系和结论，不把可以核实的简单算术展开或重复代入另设为必写义务。"
        "在 equivalent_rules 中说明同一方法下可接受的符号关系、数值关系、等价变形或"
        "合并书写；allow_alternative_methods=false 不禁止同一方法的等价表达。"
        "例如给出对应边长且写明成立的平方关系并据逆定理得出结论，可以完成相关证明"
        "义务；只抄边长后下结论、平方关系不成立或循环论证不能作为该等价正例。"
        "扣分规则针对缺失的数学依据；只有题干或教师明确要求特定计算过程或方法时，"
        "才要求对应书写形式，不从参考解答的详略自行增加限制。"
        "target 与 observable_evidence 不得为空。exact_objective 时 canonical_answer "
        "不得为空；其他 response_mode 时 full_answer 不得为空。类型专用列表即使为空"
        "也要保留键。解题证据不得含分值字段。"
        "按 trust_level 对待 reference_solution：teacher_confirmed 是教师确认依据；"
        "source_extracted 是未确认参考，可能不完整或自相矛盾，应以题干、配图和数学推理"
        "为准，不得机械抄写；absent 表示没有参考。reference_assessment 只能是 "
        "consistent、conflict 或 insufficient，并给一句简短 reference_assessment_reason。"
        "conflict 只是教师警告，不得作为省略可用评分结构的理由。"
        "不得编造被缺失配图挡住的内容。出现 repair_context 时，这是教师授权的定向修复："
        "使用其中的 validation_error、validation_issues、allowed_changes、"
        "immutable_fields 和 previous_result；按路径逐项修复已列出问题，保留已经正确的"
        "内容，不得改 immutable 字段，并返回该投影的完整替换结果。"
        "expected_projection 为 training_criteria 时只返回 solution_evidence，应用会保留"
        "已接受的 tag_analysis。不要盲目复制被拒绝的结构。"
        "若只能确认一个台阶，改用匹配的非过程 response_mode，不得为凑数量发明步骤。"
        "不要从标点、等式、角符号或连接词推断证据点个数。"
        "question_type_confirmed=false 的本地题型只是预览提示，不是评分事实。"
        "每题必须返回 question_type_suggestion（question_type 与 reason）："
        "question_type 只能取 选择题、多选题、填空题、解答题；认可本地题型时"
        "原样返回对应值，认为本地题型有误时返回真实题型并在 reason 写一句依据。"
        "question_type_confirmed=true 的题型是教师确认事实，question_type_suggestion "
        "必须与之一致。题型为解答题时可附 essay_subtype（只能取 画图、计算、证明"
        "或 null），只在能从题干确定子类时给出，拿不准返回 null。"
        "在 solution_evidence 同级返回 part_assessments 数组，逐小问提供相同 part_id、"
        "difficulty（1—10）和 rationale（一句基于该问推理要求的理由）。多小问难度必须"
        "分别估计，不复制整题难度，不按步骤数量或题号推断，也不含考试分值。"
        f"{DIFFICULTY_SCALE_GUIDANCE}"
        "每个 part 的 response_mode 必须根据题目、完整答案和解析单独判定。"
        "第(1)问的一个填空位不得把后续过程问压成整题填空。出现 expected_part_count 时，"
        "必须按给定顺序返回恰好那么多 part。"
        "response_shape 是本地预览提示：single_choice 与 single_blank 默认各返回一个 "
        "exact_objective part，且只有一个最终答案 evidence point；不得把逐项排除或解释"
        "过程拆成额外点。multiple_blank 保留可分别观察的各空答案；unknown 不得被强行"
        "改成客观题形态。仅当 question_type_suggestion 返回了与本地不同的真实题型时，"
        "才按该真实题型组织 solution_evidence（例如把误判为填空的解答题改按过程题拆分）。"
    )
    questions = []
    for item in batch.questions:
        context = item.tagging_context.to_dict()
        context.pop("existing_tags", None)
        context.pop("existing_tags_by_dimension", None)
        question_payload = {
            "question_id": item.question_id,
            "question": context,
            "rich_question_blocks": list(
                item.rich_question_blocks
            ),
            "rich_answer_blocks": list(
                item.rich_answer_blocks
            ),
            "candidate_contract": _prompt_candidate_contract(
                item.taxonomy_contract
            ),
            "reference_solution": dict(item.reference_solution),
            "question_type_confirmed": item.question_type_confirmed,
            "response_shape": item.objective_response_shape,
            "expected_part_count": (
                len(item.explicit_part_labels)
                if item.explicit_part_labels
                else None
            ),
            "explicit_part_labels": list(item.explicit_part_labels),
            "expected_projection": projection,
        }
        if item.repair_context:
            question_payload["repair_context"] = dict(item.repair_context)
        questions.append(question_payload)
    content: list[dict[str, Any]] = [
        {
            "type": "input_text",
            "text": json.dumps(
                {
                    "task": "初中数学题联合分析（标签、解题证据与训练判定点）",
                    "rules": instructions,
                    "evidence_examples": _combined_evidence_examples(),
                    "questions": questions,
                },
                ensure_ascii=False,
            ),
        }
    ]
    for item in batch.questions:
        for image in item.images:
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": (
                            f"question_id={item.question_id};"
                            f"image_role={image.role};sha256={image.sha256}"
                        ),
                    },
                    {
                        "type": "input_image",
                        "image_url": image.data_url(),
                    },
                ]
            )
    return [
        {
            "role": "system",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "只返回严格 JSON。不得返回分数、排名、等级或判定点分值。"
                        "除公式、变量、选项字母、机器标识和原答案片段外，"
                        "所有教师可见自由文本必须使用简体中文。"
                    ),
                }
            ],
        },
        {"role": "user", "content": content},
    ]


def _combined_evidence_examples() -> dict[str, Any]:
    """Give the model one authoritative set of evidence-splitting examples."""

    return {
        "q11_process_positive": {
            "why_correct": (
                "三个可独立核对的中间数学结果，对应三个无分值占位，供后续赋分使用。"
            ),
            "part_id": "part-1",
            "label": "第1问",
            "response_mode": "process_required",
            "canonical_answer": "y=x/2",
            "accepted_forms": ["y=x/2", "x=2y"],
            "full_answer": (
                "由直角三角形内角和得到∠B=90°-x；"
                "由AD=AC及等腰三角形性质得到∠ACD=90°-x/2；"
                "代入角度关系化简得到y=x/2。"
            ),
            "proof_obligations": [],
            "visual_requirements": [],
            "deduction_policy": ["缺少某一台阶时，只影响该台阶及依赖它的后续台阶"],
            "allow_alternative_methods": True,
            "evidence_points": [
                {
                    "evidence_point_id": "part-1-step-1",
                    "step_index": 1,
                    "target": "得到∠B=90°-x",
                    "justification": "直角三角形内角和",
                    "answer_anchor": "∠B=90°-x",
                    "observable_evidence": "作答中写出∠B=90°-x",
                    "depends_on": [],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
                {
                    "evidence_point_id": "part-1-step-2",
                    "step_index": 2,
                    "target": "得到∠ACD=90°-x/2",
                    "justification": "AD=AC及等腰三角形性质",
                    "answer_anchor": "∠ACD=90°-x/2",
                    "observable_evidence": "作答中写出∠ACD=90°-x/2",
                    "depends_on": ["part-1-step-1"],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                },
                {
                    "evidence_point_id": "part-1-step-3",
                    "step_index": 3,
                    "target": "推出y=x/2",
                    "justification": "代入角度关系并化简",
                    "answer_anchor": "y=x/2",
                    "observable_evidence": "作答中写出y=x/2",
                    "depends_on": ["part-1-step-2"],
                    "fine_term_links": [],
                    "equivalent_rules": ["x=2y"],
                    "counterexamples": [],
                },
            ],
        },
        "q11_process_negative": {
            "do_not_return": (
                "这个反例错误地把三个独立可评分台阶合并进同一个 evidence point，禁止这样返回。"
            ),
            "part_id": "part-1",
            "label": "第1问",
            "response_mode": "process_required",
            "canonical_answer": "y=x/2",
            "accepted_forms": ["y=x/2", "x=2y"],
            "full_answer": (
                "由直角三角形内角和得到∠B=90°-x；"
                "由AD=AC及等腰三角形性质得到∠ACD=90°-x/2；"
                "代入角度关系化简得到y=x/2。"
            ),
            "proof_obligations": [],
            "visual_requirements": [],
            "deduction_policy": ["缺少某一台阶时，只影响该台阶及依赖它的后续台阶"],
            "allow_alternative_methods": True,
            "evidence_points": [
                {
                    "evidence_point_id": "part-1-step-1",
                    "step_index": 1,
                    "target": "完成所有角度推导并得到y=x/2",
                    "justification": "综合使用题目条件和几何性质",
                    "answer_anchor": "y=x/2",
                    "observable_evidence": "写出从∠B到y=x/2的完整过程",
                    "depends_on": [],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        },
        "atomic_non_process_positive": {
            "why_correct": (
                "移项得到唯一所需结果是一个独立可评分台阶，因此该问使用非过程 "
                "response_mode，而不是再发明第二步。"
            ),
            "part_id": "part-1",
            "label": "第1问",
            "response_mode": "short_answer_points",
            "canonical_answer": "x=1",
            "accepted_forms": ["x=1"],
            "full_answer": "由x+1=2移项得到x=1。",
            "proof_obligations": [],
            "visual_requirements": [],
            "deduction_policy": ["没有得到x=1则该证据点不得分"],
            "allow_alternative_methods": False,
            "evidence_points": [
                {
                    "evidence_point_id": "part-1-step-1",
                    "step_index": 1,
                    "target": "得到x=1",
                    "justification": "依据等式性质移项",
                    "answer_anchor": "x=1",
                    "observable_evidence": "作答中写出x=1",
                    "depends_on": [],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        },
        "objective_positive": {
            "why_correct": (
                "精确客观题只按 canonical_answer 判定；full_answer 可空。"
            ),
            "part_id": "part-1",
            "label": "",
            "response_mode": "exact_objective",
            "canonical_answer": "B",
            "accepted_forms": ["B"],
            "full_answer": "",
            "proof_obligations": [],
            "visual_requirements": [],
            "deduction_policy": ["答案不是B则该证据点不得分"],
            "allow_alternative_methods": False,
            "evidence_points": [
                {
                    "evidence_point_id": "part-1-step-1",
                    "step_index": 1,
                    "target": "选择B",
                    "justification": "与标准答案一致",
                    "answer_anchor": "B",
                    "observable_evidence": "作答为B",
                    "depends_on": [],
                    "fine_term_links": [],
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        },
        "duplicate_result_negative": {
            "do_not_return": (
                "同一 question_id 返回多条 result 是错误返回：多问大题的各个小问"
                "必须全部放进唯一一份 result 的 solution_evidence.parts 里"
                "（part-1、part-2……），不得每个小问各返回一份完整 result。"
            ),
            "wrong_shape": [
                {"question_id": 11, "solution_evidence": {"parts": ["第1问…"]}},
                {"question_id": 11, "solution_evidence": {"parts": ["第2问…"]}},
            ],
            "correct_shape": [
                {
                    "question_id": 11,
                    "solution_evidence": {"parts": ["第1问…", "第2问…"]},
                }
            ],
        },
        "pre_output_checklist": [
            "每个 question_id 只返回一份 result，多问大题的小问都在该 result 的 parts 里。",
            "每个 result 都带 question_type_suggestion，question_type 取自封闭枚举。",
            "每个能单独给分的有意义中间结果都有自己的 evidence_point。",
            "没有任何 evidence_point 把多个独立可评分台阶合在一起。",
            "没有把无意义的代数书写或重复结论拆成新点。",
            "过程题的每个 answer_anchor 都从 full_answer 按出现顺序原样抄写。",
            "exact_objective 的 answer_anchor 从 canonical_answer 原样抄写；full_answer 可空。",
            "step_index 连续，depends_on 只引用同一 part 中更早的点。",
            "solution_evidence 任何位置都没有分数或分值字段。",
        ],
    }


def _stored_paths(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if str(item).strip()]


def _first_source_text(source: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = source.get(key)
        if isinstance(value, Mapping):
            nested = _first_source_text(
                value,
                "text",
                "content",
                "answer",
                "canonical_answer",
            )
            if nested:
                return nested
        elif isinstance(value, (str, int, float)) and not isinstance(value, bool):
            text = str(value).strip()
            if text:
                return text
    return ""


def _marker_paths(value: object) -> list[str]:
    return [
        match.group("path").strip()
        for match in _IMAGE_MARKER.finditer(str(value or ""))
        if match.group("path").strip()
    ]


def _safe_blocks(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        dict(item)
        for item in value
        if isinstance(item, Mapping)
        and isinstance(item.get("text"), str)
    ]


def _question_content_blocks(value: object) -> list[dict[str, Any]]:
    return [
        block
        for block in _safe_blocks(value)
        if not _SOURCE_SECTION_HEADING.fullmatch(
            _IMAGE_MARKER.sub("", str(block.get("text") or "")).strip()
        )
    ]


def _rich_paths(blocks: Sequence[Mapping[str, Any]]) -> list[str]:
    paths: list[str] = []
    for block in blocks:
        paths.extend(_marker_paths(block.get("text")))
        relationships = block.get("image_relationships")
        if isinstance(relationships, Mapping):
            paths.extend(
                str(item).strip()
                for item in relationships.values()
                if str(item).strip()
            )
    return paths


def _dedupe_paths(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _public_block(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "text": _IMAGE_MARKER.sub("", str(value.get("text") or "")),
    }


def _word_block(
    value: Mapping[str, Any],
    image_hashes_by_path: Mapping[str, str],
) -> dict[str, Any]:
    block: dict[str, Any] = {"text": str(value.get("text") or "")}
    xml = str(value.get("xml") or "").strip()
    if xml:
        block["xml"] = xml
    relationships = value.get("image_relationships")
    if isinstance(relationships, Mapping):
        governed: dict[str, str] = {}
        for relationship_id, source in relationships.items():
            source_text = str(source or "").strip()
            image_hash = image_hashes_by_path.get(source_text)
            if image_hash:
                governed[str(relationship_id)] = f"sha256:{image_hash}"
            elif re.fullmatch(r"sha256:[0-9a-f]{64}", source_text):
                governed[str(relationship_id)] = source_text
        if governed:
            block["image_relationships"] = governed
    return block


__all__ = [
    "ExistingTagProjectionWriter",
    "BankQuestionTypeSuggestionWriter",
    "OpenAICombinedAnalysisGateway",
    "QuestionAnalysisInputLoader",
    "question_analysis_input_from_config_source",
]
