from __future__ import annotations

import sqlite3
from math import ceil
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.schemas.graph import (
    GraphEvidenceRequest,
    GraphEvidenceResponse,
    GraphProfilesResponse,
    GraphQueryRequest,
    GraphRowsResponse,
    GraphV2EvidenceRequest,
    GraphV2EvidenceResponse,
    GraphV2QueryRequest,
    GraphV2Response,
    MasteryComparisonRequest,
    MasteryComparisonResponse,
    MasteryEvaluationGateResponse,
    MasteryParameterCreateRequest,
    MasteryParameterHistoryResponse,
    MasteryParameterVersionResponse,
    MasteryRolloutStateResponse,
    MasteryRolloutUpdateRequest,
    MasterySpotCheckRequest,
    RelationBatchReviewRequest,
    RelationBatchReviewResponse,
    RelationImpactRequest,
    RelationImpactResponse,
    RelationReviewQueueResponse,
    RelationReviewRequest,
    RelationReviewResponse,
    RelationTimelineResponse,
)
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.skill_graph_projection import (
    build_question_tag_graph_evidence,
    build_question_tag_graph_nodes,
    build_question_tag_graph_rows,
)
from question_bank.database.paths import question_bank_db_path
from question_bank.relations.contracts import KnowledgeRelation
from question_bank.relations.repository import (
    KnowledgeRelationConfirmationConflict,
    KnowledgeRelationDuplicate,
    KnowledgeRelationNotFound,
    KnowledgeRelationRevisionConflict,
    KnowledgeRelationTransitionError,
)
from question_bank.relations.review_service import (
    RelationReviewCommand,
    RelationReviewService,
)
from question_bank.relations.query_service import (
    GraphV2Query,
    KnowledgeGraphV2QueryService,
)
from question_bank.mastery.comparison import (
    build_profile_comparison_cases,
    compare_mastery_v1_v2,
)
from question_bank.mastery.rollout import (
    MasteryEvaluationItemNotFound,
    MasteryEvaluationNotFound,
    MasteryRolloutError,
    MasteryRolloutGateBlocked,
    MasteryRolloutRepository,
    MasteryRolloutRevisionConflict,
    MasterySpotCheckConflict,
)
from question_bank.mastery.v2 import MasteryV2Parameters


router = APIRouter(prefix="/api/graph", tags=["graph"])
GRAPH_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Graph data is temporarily unavailable",
    }
}


def get_relation_review_service() -> RelationReviewService:
    return RelationReviewService(question_bank_db_path())


def get_graph_v2_query_service() -> KnowledgeGraphV2QueryService:
    return KnowledgeGraphV2QueryService(question_bank_db_path())


def get_mastery_rollout_repository() -> MasteryRolloutRepository:
    return MasteryRolloutRepository(question_bank_db_path())


@router.post(
    "/profiles",
    response_model=GraphProfilesResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_graph_profiles(
    body: GraphQueryRequest,
    service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
) -> GraphProfilesResponse:
    profile = _build_profile(body, service)
    return GraphProfilesResponse.model_validate(_profile_response(profile))


@router.post(
    "/rows",
    response_model=GraphRowsResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_graph_rows(
    body: GraphQueryRequest,
    service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
) -> GraphRowsResponse:
    profile = _build_profile(body, service)
    rows = build_question_tag_graph_rows(profile)
    return GraphRowsResponse.model_validate(
        {
            **_profile_context(profile),
            "rows": rows,
            "nodes": build_question_tag_graph_nodes(rows),
            "edges": [],
        }
    )


@router.post(
    "/evidence",
    response_model=GraphEvidenceResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_graph_evidence(
    body: GraphEvidenceRequest,
    service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
) -> GraphEvidenceResponse:
    profile = _build_profile(body, service)
    evidence = build_question_tag_graph_evidence(profile, body.knowledge_key)
    total = len(evidence)
    start = (body.page - 1) * body.page_size
    label = body.knowledge_key.removeprefix("knowledge_point:")
    return GraphEvidenceResponse.model_validate(
        {
            **_profile_context(profile),
            "knowledge_key": body.knowledge_key,
            "knowledge_label": label,
            "items": evidence[start : start + body.page_size],
            "total": total,
            "page": body.page,
            "page_size": body.page_size,
            "total_pages": max(1, ceil(total / body.page_size)),
        }
    )


@router.post(
    "/v2/query",
    response_model=GraphV2Response,
    responses=GRAPH_DATABASE_RESPONSES,
)
def query_graph_v2(
    body: GraphV2QueryRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    graph_service: KnowledgeGraphV2QueryService = Depends(
        get_graph_v2_query_service
    ),
) -> GraphV2Response:
    profile = _build_profile(body, diagnosis_service)
    try:
        payload = graph_service.query(
            profile,
            GraphV2Query(
                knowledge_keys=tuple(body.knowledge_keys),
                prerequisite_depth=body.prerequisite_depth,
            ),
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "graph_v2_query_invalid",
            "Graph v2 query is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return GraphV2Response.model_validate(payload)


@router.post(
    "/v2/evidence",
    response_model=GraphV2EvidenceResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_graph_v2_evidence(
    body: GraphV2EvidenceRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    graph_service: KnowledgeGraphV2QueryService = Depends(
        get_graph_v2_query_service
    ),
) -> GraphV2EvidenceResponse:
    profile = _build_profile(body, diagnosis_service)
    try:
        payload = graph_service.evidence(
            profile,
            stable_key=body.stable_key,
            page=body.page,
            page_size=body.page_size,
        )
    except KeyError as exc:
        raise ApiError(
            404,
            "knowledge_identity_not_found",
            "Knowledge identity does not exist",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "graph_v2_query_invalid",
            "Graph v2 query is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return GraphV2EvidenceResponse.model_validate(payload)


@router.post(
    "/v2/mastery/compare",
    response_model=MasteryComparisonResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def compare_mastery_versions(
    body: MasteryComparisonRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryComparisonResponse:
    profile = _build_profile(body, diagnosis_service)
    try:
        if body.parameter_version is None:
            parameters = MasteryV2Parameters()
            repository.register_parameters(parameters)
        else:
            parameters = repository.get_parameters(body.parameter_version)
        cases = build_profile_comparison_cases(profile)
        if not cases:
            raise ApiError(
                422,
                "mastery_comparison_empty",
                "No governed mastery evidence is available in this scope",
            )
        report = compare_mastery_v1_v2(
            cases,
            as_of=body.as_of,
            parameters=parameters,
        )
        gate = repository.record_evaluation(report)
    except KeyError as exc:
        raise ApiError(
            404,
            "mastery_parameter_version_not_found",
            "Mastery parameter version does not exist",
        ) from exc
    except ApiError:
        raise
    except ValueError as exc:
        raise ApiError(
            422,
            "mastery_comparison_invalid",
            "Mastery comparison input is invalid",
        ) from exc
    except (OSError, sqlite3.Error, MasteryRolloutError) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryComparisonResponse.model_validate(
        {
            **report.to_dict(),
            "gate": gate.to_dict(),
        }
    )


@router.post(
    "/v2/mastery/spot-check",
    response_model=MasteryEvaluationGateResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def review_mastery_difference(
    body: MasterySpotCheckRequest,
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryEvaluationGateResponse:
    try:
        gate = repository.review_item(
            evaluation_id=body.evaluation_id,
            item_hash=body.item_hash,
            decision=body.decision,
            teacher_ref=body.teacher_ref,
            reason=body.reason,
            expected_revision=body.expected_revision,
        )
    except (MasteryEvaluationNotFound, MasteryEvaluationItemNotFound) as exc:
        raise ApiError(
            404,
            "mastery_evaluation_not_found",
            "Mastery evaluation item does not exist",
        ) from exc
    except MasteryRolloutRevisionConflict as exc:
        raise ApiError(
            409,
            "mastery_evaluation_revision_conflict",
            "Mastery evaluation changed; reload before reviewing",
        ) from exc
    except MasterySpotCheckConflict as exc:
        raise ApiError(
            409,
            "mastery_spot_check_conflict",
            "Mastery spot check is immutable",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "mastery_spot_check_invalid",
            "Mastery spot check is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryEvaluationGateResponse.model_validate(gate.to_dict())


@router.get(
    "/v2/mastery/rollout",
    response_model=MasteryRolloutStateResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_mastery_rollout(
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryRolloutStateResponse:
    try:
        state = repository.get_state()
    except (OSError, sqlite3.Error, MasteryRolloutError) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryRolloutStateResponse.model_validate(state.to_dict())


@router.put(
    "/v2/mastery/rollout",
    response_model=MasteryRolloutStateResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def update_mastery_rollout(
    body: MasteryRolloutUpdateRequest,
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryRolloutStateResponse:
    try:
        state = repository.update_state(
            enabled=body.enabled,
            expected_revision=body.expected_revision,
            actor_ref=body.teacher_ref,
            reason=body.reason,
            evaluation_id=body.evaluation_id,
        )
    except MasteryEvaluationNotFound as exc:
        raise ApiError(
            404,
            "mastery_evaluation_not_found",
            "Mastery evaluation does not exist",
        ) from exc
    except MasteryRolloutRevisionConflict as exc:
        raise ApiError(
            409,
            "mastery_rollout_revision_conflict",
            "Mastery rollout state changed; reload before updating",
        ) from exc
    except MasteryRolloutGateBlocked as exc:
        raise ApiError(
            409,
            "mastery_rollout_gate_blocked",
            "Required mastery differences are not all accepted",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "mastery_rollout_update_invalid",
            "Mastery rollout update is invalid",
        ) from exc
    except (OSError, sqlite3.Error, MasteryRolloutError) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryRolloutStateResponse.model_validate(state.to_dict())


@router.post(
    "/v2/mastery/parameters",
    response_model=MasteryParameterVersionResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def create_mastery_parameter_version(
    body: MasteryParameterCreateRequest,
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryParameterVersionResponse:
    try:
        parameters = MasteryV2Parameters(**body.model_dump())
        version = repository.register_parameters(parameters)
        item = next(
            entry
            for entry in repository.list_parameters()
            if entry["parameter_version"] == version
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "mastery_parameters_invalid",
            "Mastery parameters are invalid",
        ) from exc
    except (OSError, sqlite3.Error, MasteryRolloutError) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryParameterVersionResponse.model_validate(item)


@router.get(
    "/v2/mastery/parameters",
    response_model=MasteryParameterHistoryResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def list_mastery_parameter_versions(
    repository: MasteryRolloutRepository = Depends(
        get_mastery_rollout_repository
    ),
) -> MasteryParameterHistoryResponse:
    try:
        items = repository.list_parameters()
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "mastery_rollout_unavailable",
            "Mastery rollout data is temporarily unavailable",
        ) from exc
    return MasteryParameterHistoryResponse.model_validate(
        {"items": list(items)}
    )


@router.get(
    "/relations/review-queue",
    response_model=RelationReviewQueueResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_relation_review_queue(
    status: str = "suggested",
    page: int = 1,
    page_size: int = 50,
    service: RelationReviewService = Depends(get_relation_review_service),
) -> RelationReviewQueueResponse:
    try:
        payload = service.list_queue(
            status=status,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "relation_review_query_invalid",
            "Relation review query is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return RelationReviewQueueResponse.model_validate(payload)


@router.post(
    "/relations/{relation_id}/impact",
    response_model=RelationImpactResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def preview_relation_impact(
    relation_id: str,
    body: RelationImpactRequest,
    service: RelationReviewService = Depends(get_relation_review_service),
) -> RelationImpactResponse:
    amended = (
        None
        if body.amended_relation is None
        else KnowledgeRelation(**body.amended_relation.model_dump())
    )
    try:
        payload = service.preview(
            relation_id,
            action=body.action,
            amended_relation=amended,
        )
    except KnowledgeRelationNotFound as exc:
        raise ApiError(
            404,
            "relation_not_found",
            "Knowledge relation does not exist",
        ) from exc
    except ValueError as exc:
        raise ApiError(
            422,
            "relation_review_invalid",
            "Relation review command is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return RelationImpactResponse.model_validate(payload)


@router.post(
    "/relations/{relation_id}/review",
    response_model=RelationReviewResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def review_relation(
    relation_id: str,
    body: RelationReviewRequest,
    service: RelationReviewService = Depends(get_relation_review_service),
) -> RelationReviewResponse:
    amended = (
        None
        if body.amended_relation is None
        else KnowledgeRelation(**body.amended_relation.model_dump())
    )
    try:
        payload = service.review_one(
            relation_id,
            expected_revision=body.expected_revision,
            action=body.action,
            actor_ref=body.teacher_ref,
            reason=body.reason,
            amended_relation=amended,
        )
    except KnowledgeRelationNotFound as exc:
        raise ApiError(
            404,
            "relation_not_found",
            "Knowledge relation does not exist",
        ) from exc
    except KnowledgeRelationRevisionConflict as exc:
        raise ApiError(
            409,
            "relation_revision_conflict",
            "Knowledge relation changed; refresh before reviewing",
            details={"current_revision": exc.current_revision},
        ) from exc
    except KnowledgeRelationConfirmationConflict as exc:
        raise ApiError(
            409,
            "relation_confirmation_conflict",
            "Knowledge relation conflicts with the active graph",
            details={
                "conflict_codes": [
                    conflict.value for conflict in exc.conflicts
                ]
            },
        ) from exc
    except KnowledgeRelationDuplicate as exc:
        raise ApiError(
            409,
            "relation_duplicate",
            "Knowledge relation already exists",
            details={
                "relation_id": exc.relation_id,
                "status": exc.status.value,
            },
        ) from exc
    except (KnowledgeRelationTransitionError, ValueError) as exc:
        raise ApiError(
            422,
            "relation_review_invalid",
            "Relation review command is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return RelationReviewResponse.model_validate(payload)


@router.post(
    "/relations/review-batch",
    response_model=RelationBatchReviewResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def review_relations_batch(
    body: RelationBatchReviewRequest,
    service: RelationReviewService = Depends(get_relation_review_service),
) -> RelationBatchReviewResponse:
    try:
        payload = service.review_batch(
            [
                RelationReviewCommand(**command.model_dump())
                for command in body.commands
            ],
            actor_ref=body.teacher_ref,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "relation_review_invalid",
            "Relation review command is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return RelationBatchReviewResponse.model_validate(payload)


@router.get(
    "/relations/{relation_id}/timeline",
    response_model=RelationTimelineResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_relation_timeline(
    relation_id: str,
    service: RelationReviewService = Depends(get_relation_review_service),
) -> RelationTimelineResponse:
    try:
        service.repository.get_relation(relation_id)
        timeline = service.timeline(relation_id)
    except KnowledgeRelationNotFound as exc:
        raise ApiError(
            404,
            "relation_not_found",
            "Knowledge relation does not exist",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return RelationTimelineResponse(
        relation_id=relation_id,
        timeline=list(timeline),
    )


def _build_profile(
    body: GraphQueryRequest,
    service: DiagnosisProfileService,
) -> dict[str, Any]:
    try:
        profile = service.build_tag_profiles(
            scope=body.scope.model_dump(exclude_none=True),
            exam_scope=body.exam_scope.model_dump(exclude_none=True),
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "graph_scope_invalid",
            "Graph scope is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    if not isinstance(profile, dict) or profile.get("diagnosis_identity") != "question_tag":
        raise ApiError(
            422,
            "graph_scope_invalid",
            "Graph scope is invalid",
        )
    time_provider = getattr(service, "mastery_session_times", None)
    if callable(time_provider):
        try:
            profile["_mastery_session_times"] = time_provider(
                exam_scope=body.exam_scope.model_dump(exclude_none=True),
            )
        except (OSError, sqlite3.Error, ValueError):
            profile["_mastery_session_times"] = {}
    return profile


def _profile_context(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "scope": profile.get("scope", {}),
        "exam_scope": profile.get("exam_scope", {}),
        "coverage": profile.get("coverage", {}),
        "warnings": profile.get("warnings", []),
        "diagnosis_identity": "question_tag",
    }


def _profile_response(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        **_profile_context(profile),
        "students": profile.get("students", []),
    }


__all__ = [
    "get_graph_v2_query_service",
    "get_mastery_rollout_repository",
    "get_relation_review_service",
    "router",
]
