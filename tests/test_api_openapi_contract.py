from __future__ import annotations

import json
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
    ("GET", "/api/students/workspace"),
    ("POST", "/api/students"),
    ("POST", "/api/students/import/preview"),
    ("POST", "/api/students/import/commit"),
    ("PATCH", "/api/students/{student_id}"),
    ("GET", "/api/students/{student_id}/deletion-impact"),
    ("DELETE", "/api/students/{student_id}"),
    ("GET", "/api/sessions/{session_id}/config"),
    ("PUT", "/api/sessions/{session_id}/config"),
    ("POST", "/api/sessions/{session_id}/config/generate"),
    ("POST", "/api/sessions/{session_id}/config/generate/retry"),
    ("GET", "/api/sessions/{session_id}/config/editor"),
    ("PUT", "/api/sessions/{session_id}/config/editor"),
    ("POST", "/api/sessions/{session_id}/config/editor/refine"),
    ("GET", "/api/sessions/{session_id}/template"),
    ("POST", "/api/sessions/{session_id}/template"),
    ("PUT", "/api/sessions/{session_id}/template"),
    ("GET", "/api/sessions/{session_id}/regions"),
    ("GET", "/api/sessions/{session_id}/regions/readiness"),
    ("GET", "/api/sessions/{session_id}/regions/workspace"),
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
    ("PUT", "/api/question-bank/questions/{question_id}/tags"),
    ("DELETE", "/api/question-bank/questions/{question_id}"),
    ("POST", "/api/question-bank/questions/{question_id}/restore"),
    ("POST", "/api/question-bank/import-uploads"),
    ("POST", "/api/question-bank/import-requests"),
    ("POST", "/api/training/diagnosis"),
    ("POST", "/api/training/plans/preview"),
    ("POST", "/api/training/tasks"),
    ("GET", "/api/training/tasks"),
    ("GET", "/api/training/tasks/{task_id}"),
    ("POST", "/api/training/tasks/{task_id}/exports"),
    ("POST", "/api/training/exports/jobs/{job_id}/retry"),
    ("POST", "/api/graph/profiles"),
    ("POST", "/api/graph/rows"),
    ("POST", "/api/graph/evidence"),
    ("GET", "/api/ops/self-check"),
    ("GET", "/api/ops/backups"),
    ("POST", "/api/ops/transfer-import/uploads"),
    ("POST", "/api/ops/preflights"),
    ("POST", "/api/ops/jobs"),
    ("GET", "/api/ops/operations/{operation_id}"),
    ("POST", "/api/ops/operations/{operation_id}/cancel"),
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


def test_workbench_analysis_gets_publish_stable_response_models() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected_gets = {
        "/api/jobs": "JobSummaryListResponse",
        "/api/workbench/overview": "WorkbenchOverviewResponse",
        "/api/sessions/{session_id}/anomalies": "SessionAnomalyListResponse",
        "/api/sessions/{session_id}/analysis/questions": (
            "QuestionAnalysisListResponse"
        ),
        "/api/sessions/{session_id}/analysis/questions/{question_id}/students": (
            "StudentAnalysisListResponse"
        ),
    }

    for path, response_model in expected_gets.items():
        assert schema["paths"][path]["get"]["responses"]["200"]["content"][
            "application/json"
        ]["schema"] == {
            "$ref": f"#/components/schemas/{response_model}"
        }


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
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/pdf",
                "application/zip",
            "text/markdown",
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


def test_question_bank_write_openapi_declares_binary_upload_and_stable_errors() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    upload = schema["paths"]["/api/question-bank/import-uploads"]["post"]
    upload_schema = upload["requestBody"]["content"]["application/octet-stream"][
        "schema"
    ]
    assert upload_schema["type"] == "string"
    assert upload_schema["format"] == "binary"

    expected_errors = {
        ("put", "/api/question-bank/questions/{question_id}/tags"): {404, 409, 422},
        ("delete", "/api/question-bank/questions/{question_id}"): {404, 409, 422},
        ("post", "/api/question-bank/questions/{question_id}/restore"): {404, 409, 422},
        ("post", "/api/question-bank/import-uploads"): {415, 422},
        ("post", "/api/question-bank/import-requests"): {404, 422},
    }
    for (method, path), statuses in expected_errors.items():
        responses = schema["paths"][path][method]["responses"]
        assert statuses <= {int(status) for status in responses}
        for status in statuses:
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }


def test_template_upload_openapi_declares_binary_pdf_and_required_headers() -> None:
    from backend.api.app import create_app

    operation = create_app().openapi()["paths"][
        "/api/sessions/{session_id}/template"
    ]["post"]
    parameters = {
        (parameter["in"], parameter["name"].lower()): parameter
        for parameter in operation["parameters"]
    }
    for header in (
        "x-client-request-token",
        "x-content-sha256",
        "x-upload-filename",
    ):
        assert parameters[("header", header)]["required"] is True
    digest_schema = parameters[("header", "x-content-sha256")]["schema"]
    assert digest_schema["pattern"] == "^[0-9a-fA-F]{64}$"
    body_schema = operation["requestBody"]["content"]["application/pdf"]["schema"]
    assert body_schema == {"type": "string", "format": "binary"}
    assert operation["responses"]["201"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/TemplateUploadResponse"
    }


def test_config_generation_openapi_declares_safe_requests_and_stable_errors() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected_errors = {
        "/api/sessions/{session_id}/config/generate": {404, 422, 503},
        "/api/sessions/{session_id}/config/generate/retry": {404, 409, 422, 503},
    }
    forbidden = {"api_key", "token", "secret", "password", "path", "destination"}

    for path, statuses in expected_errors.items():
        operation = schema["paths"][path]["post"]
        responses = operation["responses"]
        assert statuses <= {int(status) for status in responses}
        for status in statuses:
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        assert request_schema["additionalProperties"] is False
        assert not (forbidden & set(request_schema.get("properties", {})))


def test_config_editor_openapi_forbids_nested_unknown_request_fields() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    for method, path in (
        ("put", "/api/sessions/{session_id}/config/editor"),
        ("post", "/api/sessions/{session_id}/config/editor/refine"),
    ):
        operation = schema["paths"][path][method]
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        assert request_schema["additionalProperties"] is False
        assert not ({"rubric", "answer_key", "config", "path"} & set(request_schema["properties"]))


def test_training_export_openapi_declares_dedicated_safe_operations() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected_errors = {
        "/api/training/tasks/{task_id}/exports": {404, 422, 503},
        "/api/training/exports/jobs/{job_id}/retry": {404, 409, 503},
    }
    forbidden = {
        "api_key",
        "token",
        "secret",
        "password",
        "path",
        "destination",
        "student_name",
        "question_text",
    }
    for path, statuses in expected_errors.items():
        operation = schema["paths"][path]["post"]
        responses = operation["responses"]
        assert statuses <= {int(status) for status in responses}
        for status in statuses:
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
    submit = schema["paths"]["/api/training/tasks/{task_id}/exports"]["post"]
    request_ref = submit["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
    assert request_schema["additionalProperties"] is False
    assert not (forbidden & set(request_schema.get("properties", {})))


def test_report_export_openapi_declares_strict_requests_and_pdf_download() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    operation = schema["paths"]["/api/sessions/{session_id}/reports/export"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    request_ref = request_schema["anyOf"][0]["$ref"]
    body_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]

    assert body_schema["additionalProperties"] is False
    assert set(body_schema["properties"]) == {
        "report_type",
        "force_regenerate",
        "excel_options",
    }
    excel_ref = body_schema["properties"]["excel_options"]["anyOf"][0]["$ref"]
    excel_schema = schema["components"]["schemas"][excel_ref.rsplit("/", 1)[-1]]
    assert excel_schema["additionalProperties"] is False
    assert set(excel_schema["properties"]) == {
        "hide_bottom_enabled",
        "hide_bottom_n",
        "manual_hidden_student_ids",
    }
    download_content = schema["paths"]["/api/jobs/{job_id}/download"]["get"][
        "responses"
    ]["200"]["content"]
    assert "application/pdf" in download_content


def test_question_bank_job_openapi_declares_dedicated_safe_operations() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected = {
        "/api/question-bank/import-requests/{request_id}/jobs": {404, 422, 503},
        "/api/question-bank/question-import-jobs/{job_id}/retry": {
            404,
            409,
            422,
            503,
        },
        "/api/question-bank/tagging-jobs": {404, 409, 422, 503},
        "/api/question-bank/tagging-jobs/{job_id}/retry": {404, 409, 422, 503},
    }
    forbidden = {
        "api_key",
        "token",
        "secret",
        "password",
        "path",
        "destination",
        "question_text",
    }

    for path, statuses in expected.items():
        operation = schema["paths"][path]["post"]
        responses = operation["responses"]
        assert statuses <= {int(status) for status in responses}
        for status in statuses:
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
        request_body = operation.get("requestBody")
        if request_body is None:
            continue
        request_ref = request_body["content"]["application/json"]["schema"]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        assert request_schema["additionalProperties"] is False
        assert not (forbidden & set(request_schema.get("properties", {})))

    import_submit = schema["paths"][
        "/api/question-bank/import-requests/{request_id}/jobs"
    ]["post"]
    assert "409" not in import_submit["responses"]


def test_training_openapi_declares_strict_safe_requests_and_stable_errors() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    expected_errors = {
        ("post", "/api/training/diagnosis"): {422, 503},
        ("post", "/api/training/plans/preview"): {422, 503},
        ("post", "/api/training/personalized-drafts"): {409, 422, 503},
        ("get", "/api/training/personalized-drafts/{draft_id}"): {
            404,
            422,
            503,
        },
        ("post", "/api/training/personalized-drafts/{draft_id}/edits"): {
            404,
            409,
            422,
            503,
        },
        ("post", "/api/training/tasks"): {409, 422, 503},
        ("get", "/api/training/tasks"): {422, 503},
        ("get", "/api/training/tasks/{task_id}"): {404, 422, 503},
    }
    forbidden = {
        "api_key",
        "created_by",
        "destination",
        "file",
        "file_path",
        "password",
        "path",
        "read_mode",
        "secret",
        "token",
    }

    for (method, path), statuses in expected_errors.items():
        operation = schema["paths"][path][method]
        responses = operation["responses"]
        assert statuses <= {int(status) for status in responses}
        for status in statuses:
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
        request_body = operation.get("requestBody")
        if request_body is None:
            continue
        request_ref = request_body["content"]["application/json"]["schema"]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        assert request_schema["additionalProperties"] is False
        assert not (forbidden & set(request_schema.get("properties", {})))


def test_training_openapi_does_not_offer_legacy_or_broad_recommendation_controls() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    for path in ("/api/training/plans/preview", "/api/training/tasks"):
        operation = schema["paths"][path]["post"]
        request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        properties = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]][
            "properties"
        ]
        assert "allow_broad_fallback" not in properties
        assert "related_fill_policy" not in properties
        assert "read_mode" not in properties


def test_graph_openapi_declares_strict_tag_only_operations() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    graph_operations = {
        ("post", "/api/graph/profiles"),
        ("post", "/api/graph/rows"),
        ("post", "/api/graph/evidence"),
    }
    for method, path in graph_operations:
        operation = schema["paths"][path][method]
        responses = operation["responses"]
        assert {422, 503} <= {int(status) for status in responses}
        for status in (422, 503):
            assert responses[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/ErrorResponse"
            }
        request_ref = operation["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]
        assert request_schema["additionalProperties"] is False

    graph_schema = json.dumps(
        {
            path: schema["paths"][path]
            for _method, path in graph_operations
        },
        sort_keys=True,
    )
    for forbidden in (
        "allow_broad_fallback",
        "include_relations",
        "read_mode",
        "related_fill_policy",
        "tag_relations",
    ):
        assert forbidden not in graph_schema


def test_ops_write_openapi_uses_only_protected_inputs() -> None:
    from backend.api.app import create_app

    schema = create_app().openapi()
    ops_paths = {
        path: item
        for path, item in schema["paths"].items()
        if path.startswith("/api/ops")
    }
    assert set(ops_paths) == {
        "/api/ops/self-check",
        "/api/ops/backups",
        "/api/ops/transfer-import/uploads",
        "/api/ops/preflights",
        "/api/ops/jobs",
        "/api/ops/operations/{operation_id}",
        "/api/ops/operations/{operation_id}/cancel",
    }
    for path in ("/api/ops/self-check", "/api/ops/backups"):
        response = ops_paths[path]["get"]["responses"]["503"]
        assert response["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/ErrorResponse"
        }
    limit = next(
        parameter
        for parameter in ops_paths["/api/ops/backups"]["get"]["parameters"]
        if parameter["name"] == "limit"
    )
    assert limit["schema"]["minimum"] == 1
    assert limit["schema"]["maximum"] == 100
    serialized = json.dumps(ops_paths, sort_keys=True).lower()
    assert ops_paths["/api/ops/self-check"]["get"].get("parameters", []) == []
    assert [
        parameter["name"]
        for parameter in ops_paths["/api/ops/backups"]["get"]["parameters"]
    ] == ["limit"]
    for forbidden in ("destination", "command", "migrations_dir", "api_key", "password", "secret"):
        assert forbidden not in serialized
