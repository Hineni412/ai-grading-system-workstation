from __future__ import annotations

import sqlite3
import pickle
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from session_originals import originals_state
from backend.api.app import ApiError, ErrorResponse
from backend.api.dependencies import get_request_diagnosis_profile_service
from backend.api.schemas.graph import (
    CurrentGraphEvidenceRequest,
    CurrentGraphEvidenceResponse,
    CurrentGraphQueryRequest,
    CurrentGraphResponse,
    GraphQueryRequest,
)
from integration.data_generation import commit_generation
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.result_cache import ResultCache
from integration.training_prewarm import record_request
from question_bank.current_knowledge import CurrentKnowledgeUnavailable
from question_bank.database.paths import question_bank_db_path
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
# Pickle-bytes payload cache keyed on the exact diagnosis cache key plus the
# normalized query; the question-bank commit generation inside the diagnosis
# key invalidates entries when evidence or the active release changes.
_GRAPH_QUERY_CACHE = ResultCache(limit=24)


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
    try:
        stat = source.stat()
        identity = f"{stat.st_dev}:{stat.st_ino}"
    except OSError:
        identity = "missing"
    key = (str(source), identity, f"commits:{commit_generation(source)}")
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
    try:
        query = CurrentGraphQuery(
            knowledge_keys=tuple(body.knowledge_keys),
            prerequisite_depth=body.prerequisite_depth,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "graph_query_invalid",
            "Graph query is invalid",
        ) from exc
    graph_service = _materialize_graph_service(graph_service_provider)
    scope_dump = body.scope.model_dump(exclude_none=True)
    exam_scope_dump = body.exam_scope.model_dump(exclude_none=True)
    record_request(
        "graph",
        scope=scope_dump,
        exam_scope=exam_scope_dump,
        params={
            "knowledge_keys": list(query.knowledge_keys),
            "prerequisite_depth": query.prerequisite_depth,
        },
    )
    try:
        payload = compute_graph_query_payload(
            diagnosis_service,
            graph_service,
            scope=scope_dump,
            exam_scope=exam_scope_dump,
            query=query,
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
        _attach_assessment_evidence(payload, diagnosis_service)
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


def _attach_assessment_evidence(
    payload: dict[str, Any],
    diagnosis_service: DiagnosisProfileService,
) -> None:
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        return
    fetch = getattr(
        getattr(getattr(diagnosis_service, "db", None), "results", None),
        "get_active_assessment_evidence",
        None,
    )
    if not callable(fetch):
        return
    session_ids = sorted(
        {
            int(item.get("session_id") or 0)
            for item in items
            if isinstance(item, dict)
        }
    )
    student_ids = sorted(
        {
            int(item.get("student_id") or 0)
            for item in items
            if isinstance(item, dict)
        }
    )
    if not session_ids or not student_ids:
        return
    rows = fetch(student_ids=student_ids, session_ids=session_ids)
    index: dict[tuple[int, int, str], dict[str, Any]] = {}
    for row in rows:
        key = (
            int(row.get("session_id") or 0),
            int(row.get("student_id") or 0),
            str(row.get("question_id") or ""),
        )
        index.setdefault(key, row)
    for item in items:
        if not isinstance(item, dict):
            continue
        key = (
            int(item.get("session_id") or 0),
            int(item.get("student_id") or 0),
            str(item.get("question_id") or ""),
        )
        row = index.get(key)
        if row is None:
            continue
        item["detail_id"] = row.get("detail_id")
        item["deduction_reason"] = str(row.get("deduction_reason") or "")
        item["evidence_url"] = (
            f"/api/sessions/{key[0]}/results/{row.get('result_id')}"
            f"/details/{row.get('detail_id')}/crop"
        ) if originals_state(Path(diagnosis_service.db.db_path).parent.parent, key[0]) not in {"clearing", "cleared"} else None


def _build_profile_dicts(
    service: DiagnosisProfileService,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    *, include_source_details: bool = True,
) -> dict[str, Any]:
    try:
        build = (service.build_summary_profiles
                 if not include_source_details and isinstance(service, DiagnosisProfileService)
                 else service.build_tag_profiles)
        profile = build(
            scope=dict(scope),
            exam_scope=dict(exam_scope),
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
                exam_scope=dict(exam_scope),
            )
        except (OSError, sqlite3.Error, ValueError):
            profile["_mastery_session_times"] = {}
    return profile


def compute_graph_query_payload(
    diagnosis_service: DiagnosisProfileService,
    graph_service: CurrentKnowledgeGraphQueryService,
    *,
    scope: Mapping[str, Any],
    exam_scope: Mapping[str, Any],
    query: CurrentGraphQuery,
) -> dict[str, Any]:
    """Single compute path shared by /api/graph/query and the prewarm worker."""

    def _compute() -> dict[str, Any]:
        profile = _build_profile_dicts(
            diagnosis_service, scope=scope, exam_scope=exam_scope, include_source_details=False
        )
        mastery_by_key = getattr(diagnosis_service, "latest_aggregated_mastery", None)
        students = profile.get("students", [])
        population = getattr(diagnosis_service, "semester_mastery", None)
        if len(students) == 1 and callable(population):
            student_id = str(students[0]["student_id"])
            mastery_by_key = {key: item for (sid, key), item in population(profile).items() if sid == student_id}
        return graph_service.query(profile, query, mastery_by_key=mastery_by_key)


    key_fn = getattr(diagnosis_service, "tag_profile_cache_key", None)
    if not callable(key_fn):
        return _compute()
    profile_key = key_fn(scope=dict(scope), exam_scope=dict(exam_scope))
    local_key = (*profile_key, 'graph-query-payload-v1', query.knowledge_keys, query.prerequisite_depth)
    def compute_or_read():
        reader = getattr(diagnosis_service, '_read_local_profile', None)
        entry = reader(local_key) if callable(reader) else None
        if entry is not None:
            payload = pickle.loads(entry[0]).get('graph')
            if isinstance(payload, dict):
                return payload
        return _compute()
    payload = _GRAPH_QUERY_CACHE.get_or_compute(
        (
            "graph-query-v1",
            str(Path(graph_service.db_path).resolve(strict=False)),
            str(getattr(graph_service.resolver, "release_id", "")),
            profile_key,
            query.knowledge_keys,
            query.prerequisite_depth,
        ),
        compute_or_read,
    )
    if getattr(diagnosis_service, 'persist_snapshots', False):
        diagnosis_service._save_local_profile(local_key, (pickle.dumps({'graph': payload}, pickle.HIGHEST_PROTOCOL),
                                                         pickle.dumps({}, pickle.HIGHEST_PROTOCOL)))
    return payload


def _build_profile(
    body: GraphQueryRequest,
    service: DiagnosisProfileService,
) -> dict[str, Any]:
    return _build_profile_dicts(
        service,
        body.scope.model_dump(exclude_none=True),
        body.exam_scope.model_dump(exclude_none=True),
    )


__all__ = [
    "compute_graph_query_payload",
    "get_current_graph_query_service",
    "router",
]
