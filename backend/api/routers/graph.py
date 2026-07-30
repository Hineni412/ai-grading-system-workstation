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


router = APIRouter(prefix="/api/graph", tags=["graph"])
GRAPH_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Graph data is temporarily unavailable",
    }
}


def get_relation_review_service() -> RelationReviewService:
    return RelationReviewService(question_bank_db_path())


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


__all__ = ["get_relation_review_service", "router"]
