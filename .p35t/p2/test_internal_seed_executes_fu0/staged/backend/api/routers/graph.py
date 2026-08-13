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
)
from integration.diagnosis_profile_service import DiagnosisProfileService
from integration.skill_graph_projection import (
    build_question_tag_graph_evidence,
    build_question_tag_graph_nodes,
    build_question_tag_graph_rows,
)


router = APIRouter(prefix="/api/graph", tags=["graph"])
GRAPH_DATABASE_RESPONSES = {
    503: {
        "model": ErrorResponse,
        "description": "Graph data is temporarily unavailable",
    }
}


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


__all__ = ["router"]
