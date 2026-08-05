from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.schemas.graph import (
    CurrentGraphEvidenceRequest,
    CurrentGraphEvidenceResponse,
    CurrentGraphQueryRequest,
    CurrentGraphResponse,
    GraphQueryRequest,
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
from question_bank.current_knowledge import CurrentKnowledgeUnavailable
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
    CurrentGraphQuery,
    CurrentKnowledgeGraphQueryService,
)


router = APIRouter(prefix="/api/graph", tags=["graph"])
GRAPH_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Current graph data is temporarily unavailable",
    }
}
_GRAPH_SERVICE_CACHE_LOCK = threading.RLock()
_GRAPH_SERVICE_CACHE: tuple[
    tuple[str, ...],
    CurrentKnowledgeGraphQueryService,
] | None = None


def get_relation_review_service() -> RelationReviewService:
    return RelationReviewService(question_bank_db_path())


def _raise_current_knowledge_unavailable(
    exc: CurrentKnowledgeUnavailable,
) -> None:
    raise ApiError(
        503,
        "current_knowledge_unavailable",
        "Current knowledge standard is unavailable",
        details={"reason": exc.reason},
    ) from exc


GraphServiceProvider = CurrentKnowledgeGraphQueryService | Callable[
    [], CurrentKnowledgeGraphQueryService
]


def get_current_graph_query_service() -> GraphServiceProvider:
    return lambda: _cached_current_graph_service(question_bank_db_path())


def _cached_current_graph_service(db_path: Path) -> CurrentKnowledgeGraphQueryService:
    global _GRAPH_SERVICE_CACHE
    source = Path(db_path).resolve(strict=False)
    generation: list[str] = [str(source)]
    for candidate in (source, Path(f"{source}-wal")):
        try:
            stat = candidate.stat()
            generation.append(f"{candidate.name}:{stat.st_size}:{stat.st_mtime_ns}")
        except FileNotFoundError:
            generation.append(f"{candidate.name}:missing")
    key = tuple(generation)
    with _GRAPH_SERVICE_CACHE_LOCK:
        if _GRAPH_SERVICE_CACHE is not None and _GRAPH_SERVICE_CACHE[0] == key:
            return _GRAPH_SERVICE_CACHE[1]
        service = CurrentKnowledgeGraphQueryService(source)
        _GRAPH_SERVICE_CACHE = (key, service)
        return service


def _materialize_graph_service(
    provider: GraphServiceProvider,
) -> CurrentKnowledgeGraphQueryService:
    if isinstance(provider, CurrentKnowledgeGraphQueryService):
        return provider
    try:
        return provider()
    except CurrentKnowledgeUnavailable as exc:
        raise ApiError(
            503,
            "current_knowledge_unavailable",
            "Current knowledge standard is unavailable",
            details={"reason": exc.reason},
        ) from exc


@router.post(
    "/query",
    response_model=CurrentGraphResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def query_current_graph(
    body: CurrentGraphQueryRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    graph_service_provider: GraphServiceProvider = Depends(
        get_current_graph_query_service
    ),
) -> CurrentGraphResponse:
    profile = _build_profile(body, diagnosis_service)
    graph_service = _materialize_graph_service(graph_service_provider)
    try:
        payload = graph_service.query(
            profile,
            CurrentGraphQuery(
                knowledge_keys=tuple(body.knowledge_keys),
                prerequisite_depth=body.prerequisite_depth,
            ),
            mastery_by_key=getattr(
                diagnosis_service,
                "latest_aggregated_mastery",
                None,
            ),
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "graph_query_invalid",
            "Graph query is invalid",
        ) from exc
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return CurrentGraphResponse.model_validate(payload)


@router.post(
    "/evidence",
    response_model=CurrentGraphEvidenceResponse,
    responses=GRAPH_DATABASE_RESPONSES,
)
def get_current_graph_evidence(
    body: CurrentGraphEvidenceRequest,
    diagnosis_service: DiagnosisProfileService = Depends(
        get_request_diagnosis_profile_service
    ),
    graph_service_provider: GraphServiceProvider = Depends(
        get_current_graph_query_service
    ),
) -> CurrentGraphEvidenceResponse:
    profile = _build_profile(body, diagnosis_service)
    graph_service = _materialize_graph_service(graph_service_provider)
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
            "graph_query_invalid",
            "Graph query is invalid",
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise ApiError(
            503,
            "graph_database_unavailable",
            "Graph data is temporarily unavailable",
        ) from exc
    return CurrentGraphEvidenceResponse.model_validate(payload)


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
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
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
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
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
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
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
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
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
        timeline = service.timeline(relation_id)
    except KnowledgeRelationNotFound as exc:
        raise ApiError(
            404,
            "relation_not_found",
            "Knowledge relation does not exist",
        ) from exc
    except CurrentKnowledgeUnavailable as exc:
        _raise_current_knowledge_unavailable(exc)
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
    if (
        not isinstance(profile, dict)
        or profile.get("diagnosis_identity") != "question_tag"
    ):
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


__all__ = [
    "get_current_graph_query_service",
    "get_relation_review_service",
    "router",
]
