from __future__ import annotations

from collections import Counter


EXPECTED_OPERATIONS = {
    ("GET", "/healthz"),
    ("GET", "/api/healthz"),
    ("GET", "/api/sessions"),
    ("POST", "/api/sessions"),
    ("GET", "/api/sessions/{session_id}"),
    ("PATCH", "/api/sessions/{session_id}"),
    ("DELETE", "/api/sessions/{session_id}"),
    ("POST", "/api/sessions/{session_id}/restore"),
    ("GET", "/api/students"),
    ("POST", "/api/students"),
    ("PATCH", "/api/students/{student_id}"),
    ("DELETE", "/api/students/{student_id}"),
    ("GET", "/api/sessions/{session_id}/config"),
    ("PUT", "/api/sessions/{session_id}/config"),
    ("GET", "/api/sessions/{session_id}/template"),
    ("PUT", "/api/sessions/{session_id}/template"),
    ("GET", "/api/sessions/{session_id}/regions"),
    ("GET", "/api/sessions/{session_id}/regions/draft"),
    ("PUT", "/api/sessions/{session_id}/regions/draft"),
    ("POST", "/api/sessions/{session_id}/regions/commit"),
    ("GET", "/api/sessions/{session_id}/progress"),
    ("POST", "/api/jobs/{job_type}"),
    ("GET", "/api/jobs/{job_id}"),
    ("POST", "/api/jobs/{job_id}/cancel"),
    ("POST", "/api/sessions/{session_id}/reports/export"),
    ("POST", "/api/sessions/{session_id}/scan/analyze"),
    ("POST", "/api/sessions/{session_id}/grading/run"),
    ("GET", "/api/sessions/{session_id}/review/questions"),
    ("GET", "/api/sessions/{session_id}/review/questions/{question_id}/items"),
    ("POST", "/api/sessions/{session_id}/review/questions/{question_id}/confirm"),
    ("GET", "/api/sessions/{session_id}/results/{result_id}/pages/{page}"),
    ("GET", "/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop"),
    ("GET", "/api/jobs/{job_id}/download"),
    ("GET", "/api/question-bank/papers"),
    ("GET", "/api/question-bank/questions"),
    ("GET", "/api/question-bank/questions/{question_id}"),
    ("GET", "/api/question-bank/questions/{question_id}/assets/{asset_index}"),
    ("GET", "/api/question-bank/questions/{question_id}/previews/{preview_type}"),
}


def _operations(schema: dict) -> list[tuple[str, str, dict]]:
    operations = []
    for path, path_item in schema["paths"].items():
        for method, operation in path_item.items():
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                operations.append((method.upper(), path, operation))
    return operations


def test_openapi_covers_current_phase1_routes_with_unique_operation_ids() -> None:
    from backend.api.app import create_app

    operations = _operations(create_app().openapi())
    assert EXPECTED_OPERATIONS <= {(method, path) for method, path, _ in operations}
    operation_ids = [operation["operationId"] for _, _, operation in operations]
    assert not [key for key, count in Counter(operation_ids).items() if count > 1]


def test_openapi_422_responses_match_unified_runtime_error_shape() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    operations = _operations(schema)
    assert "ErrorResponse" in schema["components"]["schemas"]
    for _, _, operation in operations:
        response = operation.get("responses", {}).get("422")
        if response is None:
            continue
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_question_bank_routes_publish_snapshot_503_error_shape() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    for path in (
        "/api/question-bank/papers",
        "/api/question-bank/questions",
        "/api/question-bank/questions/{question_id}",
        "/api/question-bank/questions/{question_id}/assets/{asset_index}",
        "/api/question-bank/questions/{question_id}/previews/{preview_type}",
    ):
        response = schema["paths"][path]["get"]["responses"]["503"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }


def test_binary_routes_do_not_accept_client_file_paths() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    for path in (
        "/api/jobs/{job_id}/download",
        "/api/sessions/{session_id}/results/{result_id}/pages/{page}",
        "/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop",
        "/api/question-bank/questions/{question_id}/assets/{asset_index}",
        "/api/question-bank/questions/{question_id}/previews/{preview_type}",
    ):
        parameters = schema["paths"][path]["get"].get("parameters", [])
        assert not {
            parameter["name"]
            for parameter in parameters
            if parameter["name"] in {"path", "file", "file_path", "filename"}
        }


def test_binary_routes_publish_exact_200_media_types_and_binary_schemas() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected_media_types = {
        "/api/jobs/{job_id}/download": {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        },
        "/api/sessions/{session_id}/results/{result_id}/pages/{page}": {
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/bmp",
        },
        "/api/sessions/{session_id}/results/{result_id}/details/{detail_id}/crop": {
            "image/jpeg"
        },
        "/api/question-bank/questions/{question_id}/assets/{asset_index}": {
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/bmp",
        },
        "/api/question-bank/questions/{question_id}/previews/{preview_type}": {
            "image/jpeg",
            "image/png",
            "image/webp",
            "image/bmp",
        },
    }

    for path, media_types in expected_media_types.items():
        content = schema["paths"][path]["get"]["responses"]["200"]["content"]
        assert set(content) == media_types
        for media_type in media_types:
            assert content[media_type]["schema"] == {
                "type": "string",
                "format": "binary",
            }
