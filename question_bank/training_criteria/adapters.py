from __future__ import annotations

import json
import mimetypes
import re
import threading
from pathlib import Path
from time import perf_counter
from typing import Any, Mapping, Sequence

from backend.llm import LLMRequestKind, usage_fields
from question_bank.database.schema import connect
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.ai_tagging_service import (
    AITaggingResult,
    AITaggingService,
    _with_quality,
    is_auto_saveable_result,
)
from question_bank.services.asset_path_service import (
    resolve_question_bank_asset_path,
)
from question_bank.services.question_service import QuestionService
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
    TaxonomyProjectionReviewRequired,
    combined_response_format,
)


_IMAGE_MARKER = re.compile(r"\[\[IMAGE:(?P<path>[^\]]+)\]\]")


class ExistingTagProjectionWriter:
    """Adapter that keeps combined tags on the established tag seam."""

    def __init__(
        self,
        *,
        question_service: QuestionService,
        tagging_service: AITaggingService,
    ) -> None:
        self.question_service = question_service
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
        contract = dict(
            question.taxonomy_contract
            or self.tagging_service.taxonomy_contract(
                question.tagging_context
            )
        )
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
            raise TaxonomyProjectionReviewRequired(
                "tag projection requires taxonomy review"
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
                        f"{str(contract.get('candidate_fingerprint') or 'none')}"
                    ),
                    "expected_revision": int(checked.taxonomy_revision or 0),
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
        if not self.question_service.save_tag_analysis(
            question.question_id,
            checked.analysis,
            model_name=model_name,
            confidence=checked.analysis.confidence,
            taxonomy_governance=self.tagging_service.taxonomy_governance,
        ):
            raise RuntimeError("tag projection could not be saved")
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


class OpenAICombinedAnalysisGateway:
    """Production Adapter with zero automatic model retries."""

    def __init__(self, *, protocol_adapter: Any, model_name: str) -> None:
        self.protocol_adapter = protocol_adapter
        self.model_name = str(model_name or "").strip()
        if not self.model_name:
            raise ValueError("model_name must not be empty")

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
            allow_retry=False,
            kwargs={
                "text": {
                    "format": combined_response_format(projection)
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
                "combined model response JSON parsing failed"
            ) from exc
        if not isinstance(payload, Mapping):
            raise GatewayResponseParseError(
                "combined model response JSON must be an object"
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

    def __init__(self, *, db_path: Path, data_root: Path) -> None:
        self.db_path = Path(db_path)
        self.data_root = Path(data_root)

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
        with connect(self.db_path) as connection:
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
            question_images = self._images(question_paths, role="question")
            answer_images = self._images(answer_paths, role="answer")
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
                    ),
                    rich_question_blocks=tuple(
                        _public_block(item) for item in question_blocks
                    ),
                    rich_answer_blocks=tuple(
                        _public_block(item) for item in answer_blocks
                    ),
                    images=images,
                    taxonomy_contract=dict(
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
                result.append(
                    QuestionAnalysisImage(
                        role=role,  # type: ignore[arg-type]
                        mime_type=mime,
                        content=resolved.read_bytes(),
                    )
                )
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
        images=normalized_images,
        taxonomy_contract=dict(taxonomy_contract or {}),
        reference_solution=(
            dict(reference_solution)
            if isinstance(reference_solution, Mapping)
            else {}
        ),
    )


def _combined_prompt(
    batch: PlannedAnalysisBatch,
    projection: AnalysisProjection,
) -> list[dict[str, Any]]:
    instructions = (
        "Analyze only the listed junior-middle-school math questions. "
        "Return exactly one result for every listed question_id, copy that "
        "integer unchanged into both the result and solution_evidence, and "
        "never mix candidates between questions. Tag candidates may only come "
        "from that question's candidate_contract. Solution evidence must use "
        "Simplified Chinese for every teacher-visible semantic field, including "
        "target, observable_evidence, justification, rationale, auxiliary rules, "
        "equivalent rules, counterexamples, and reference assessment reasons. "
        "Only formulas, mathematical variables, option letters, machine IDs, and "
        "verbatim answer anchors may remain non-Chinese. "
        "question-solution-evidence-v2 and be split into question parts. Within "
        "each part, one independently scorable mathematical milestone must map "
        "to exactly one evidence point. If a derivation contains several meaningful "
        "intermediate results, return one evidence point for each result instead "
        "of placing the whole derivation inside one target or observable_evidence. "
        "Do not split trivial algebraic typography or restate the same result. "
        "A genuinely atomic answer may contain one evidence point. Follow the "
        "complete positive and negative evidence_examples supplied with this task; "
        "the negative example is explicitly forbidden. For every point, "
        "step_index must start at 1 and follow array order, justification must name "
        "the condition, theorem, property, or operation supporting that step, and "
        "answer_anchor must copy the shortest unique result or operation that identifies "
        "the milestone verbatim from the answer source. For exact_objective the answer "
        "source is canonical_answer and full_answer may be empty; for every other "
        "response_mode the answer source is full_answer. Process anchors must occur in "
        "evidence-point order. "
        "depends_on may reference only earlier evidence_point_id values in the same "
        "part. Each part starts a fresh dependency namespace: never put an "
        "evidence_point_id from another part in depends_on. If a later part uses an "
        "earlier part's conclusion, describe that fact in justification or "
        "auxiliary_rules instead. Before returning, complete every item in "
        "pre_output_checklist. Every "
        "part_id and evidence_point_id must be a unique lowercase ASCII machine "
        "identifier matching ^[a-z][a-z0-9_-]{1,127}$; prefer part-1 and "
        "part-1-step-1 style IDs, and keep evidence_point_id unique across the "
        "whole question. Every evidence point may link zero or more entries from "
        "that question's candidate_contract.candidates.knowledge only. Copy the "
        "id and name together, verbatim, from the same knowledge candidate; do "
        "not use another candidate dimension, a proposed tag, a paraphrase, or "
        "an invented term. If no governed knowledge candidate is an exact fit, "
        "return an empty fine_term_links array; keep any genuinely new term only "
        "in tag_analysis.proposed_tags for later human review. Label each link "
        "as direct or supporting_prerequisite and do not repeat the same "
        "(id, role) pair in "
        "one evidence point. Never infer or return "
        "core graph mappings; the application resolves those from governed "
        "local mappings. For every part also return response_mode, canonical "
        "and full answers, accepted forms, proof and visual obligations, a "
        "non-empty deduction policy, and whether alternative methods are "
        "allowed. target and observable_evidence must be non-empty. For "
        "exact_objective, canonical_answer must be non-empty; for every other "
        "response_mode, full_answer must be non-empty. Keep "
        "keys present even when a type-specific list or answer is empty. "
        "Solution evidence must never contain score fields. "
        "Treat reference_solution according to trust_level. teacher_confirmed is a "
        "teacher-confirmed basis. source_extracted is unconfirmed reference material "
        "that may be incomplete or internally conflicting: use the stem, images and "
        "mathematical reasoning instead of copying it mechanically. absent means no "
        "reference was available. Return reference_assessment as consistent, conflict, "
        "or insufficient with a short reference_assessment_reason. A conflict is a "
        "teacher warning, never a reason to omit a usable grading structure. "
        "Do not invent content hidden by a missing image. When repair_context is "
        "present, this is a teacher-authorized targeted repair. Use its exact "
        "validation_error, validation_issues, allowed_changes, immutable_fields, "
        "and previous_result. Resolve every listed issue at its stated path, retain "
        "content that is already correct, never alter an immutable field, and return "
        "one complete replacement result for the requested projection. When "
        "expected_projection is training_criteria, return solution_evidence only; "
        "the application preserves the already accepted tag_analysis. "
        "Do not copy the rejected structure blindly. Process-required parts should "
         "be split into independently verifiable evidence points when the answer "
         "shows more than one meaningful milestone. If only one milestone can be "
         "confirmed, one evidence point is allowed; never invent steps just to "
         "satisfy a count. Do not infer an exact evidence point count from punctuation, "
         "equations, angle symbols, or connective words. "
        "A local question_type with question_type_confirmed=false is only a preview hint, "
        "not a grading fact. Decide response_mode separately for every part from the "
        "question, its complete answer and analysis. One blank in part (1) must never "
        "collapse later process-required parts into a whole-question fill blank. When "
        "expected_part_count is present, return exactly that many parts in the stated order. "
        "response_shape is a deterministic local fact: single_choice and single_blank "
        "must each return one exact_objective part with one final-answer evidence point; "
        "never turn option-by-option elimination or explanatory work into extra points. "
        "multiple_blank keeps separately observable blank answers, and unknown must not "
        "be forced into an objective shape."
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
            "candidate_contract": dict(item.taxonomy_contract),
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
                    "task": "combined-v3 question analysis",
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
                        "Return strict JSON only. Never return scores, "
                        "ranks, grades, or point values."
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
                "Three independently checkable intermediate mathematical results "
                "become three unscored placeholders for later score allocation."
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
                "This incorrectly merges three independently scorable milestones "
                "into one evidence point."
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
        "atomic_process_positive": {
            "why_correct": (
                "Moving one term and obtaining the only required result is one "
                "independently scorable milestone; do not split typography."
            ),
            "part_id": "part-1",
            "label": "第1问",
            "response_mode": "process_required",
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
                "An exact objective item is graded from canonical_answer alone; "
                "full_answer may be empty."
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
        "pre_output_checklist": [
            "Every meaningful intermediate result that could earn partial credit has its own evidence_point.",
            "No evidence_point combines multiple independently scorable milestones.",
            "No trivial algebraic typography or repeated conclusion was split into a separate point.",
            "Each process answer_anchor is copied verbatim from full_answer and follows answer order.",
            "Each exact_objective answer_anchor is copied verbatim from canonical_answer; full_answer may be empty.",
            "step_index is contiguous and each depends_on entry names only an earlier point in the same part.",
            "No score or point-value field appears anywhere in solution_evidence.",
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


__all__ = [
    "ExistingTagProjectionWriter",
    "OpenAICombinedAnalysisGateway",
    "QuestionAnalysisInputLoader",
    "question_analysis_input_from_config_source",
]
