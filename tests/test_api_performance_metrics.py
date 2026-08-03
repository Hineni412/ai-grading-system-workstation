from __future__ import annotations

import math
import sqlite3
import warnings
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from backend.performance.metrics import (
    InMemoryPerformanceSink,
    instrument_sqlite_connection,
    request_performance_scope,
)


class _TrackingConnection(sqlite3.Connection):
    trace_callback_installed = False

    def set_trace_callback(self, trace_callback):
        self.trace_callback_installed = True
        return super().set_trace_callback(trace_callback)


def _finish_record(request_id: str, statement: str):
    with request_performance_scope(request_id) as recorder:
        connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        connection.execute(statement).fetchall()
        connection.close()
        return recorder.finish(
            method="GET",
            route_template="/api/threaded",
            status_code=200,
            elapsed_ms=0.5,
        )


def test_request_scope_counts_statements_without_retaining_sql() -> None:
    sink = InMemoryPerformanceSink()
    with request_performance_scope("req-1") as recorder:
        connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        connection.execute("CREATE TABLE sample(id INTEGER)")
        connection.execute("INSERT INTO sample VALUES (1)")
        connection.execute("SELECT id FROM sample").fetchall()
        connection.close()
        record = recorder.finish(
            method="GET",
            route_template="/api/items/{item_id}",
            status_code=200,
            elapsed_ms=1.25,
        )

    sink.record(record)
    captured = sink.pop("req-1")

    assert captured.db_statements_total >= 3
    assert captured.db_select_statements == 1
    assert "sample" not in repr(captured)
    assert "req-1" not in repr(captured)


def test_instrumentation_outside_request_scope_does_not_install_callback() -> None:
    connection = sqlite3.connect(":memory:", factory=_TrackingConnection)

    returned = instrument_sqlite_connection(connection)

    assert returned is connection
    assert connection.trace_callback_installed is False
    connection.close()


def test_with_statement_counts_as_select() -> None:
    with request_performance_scope("with-select") as recorder:
        connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        connection.execute("WITH value(item) AS (SELECT 1) SELECT item FROM value").fetchall()
        connection.close()
        record = recorder.finish(
            method="GET",
            route_template="/api/with",
            status_code=200,
            elapsed_ms=0.25,
        )

    assert record.db_statements_total == 1
    assert record.db_select_statements == 1


def test_nested_request_scopes_restore_outer_recorder() -> None:
    with request_performance_scope("outer") as outer:
        outer_connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        outer_connection.execute("SELECT 1").fetchall()

        with request_performance_scope("inner") as inner:
            inner_connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
            inner_connection.execute("SELECT 2").fetchall()
            inner_connection.close()
            inner_record = inner.finish(
                method="GET",
                route_template="/api/inner",
                status_code=200,
                elapsed_ms=0.1,
            )

        outer_connection.execute("SELECT 3").fetchall()
        outer_connection.close()
        outer_record = outer.finish(
            method="GET",
            route_template="/api/outer",
            status_code=200,
            elapsed_ms=0.2,
        )

    assert inner_record.db_statements_total == 1
    assert outer_record.db_statements_total == 2


def test_threaded_request_scopes_keep_independent_totals() -> None:
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(_finish_record, "thread-1", "SELECT 1")
        second_future = executor.submit(
            _finish_record,
            "thread-2",
            "WITH value(item) AS (SELECT 2) SELECT item FROM value",
        )

    first = first_future.result()
    second = second_future.result()

    assert first.request_id == "thread-1"
    assert second.request_id == "thread-2"
    assert first.db_statements_total == 1
    assert second.db_statements_total == 1
    assert first.db_select_statements == 1
    assert second.db_select_statements == 1


@pytest.mark.parametrize("elapsed_ms", [-0.01, math.nan])
def test_finish_rejects_invalid_elapsed_ms(elapsed_ms: float) -> None:
    with request_performance_scope("invalid-elapsed") as recorder:
        with pytest.raises(ValueError, match="elapsed_ms"):
            recorder.finish(
                method="GET",
                route_template="/api/invalid",
                status_code=200,
                elapsed_ms=elapsed_ms,
            )


def test_in_memory_sink_rejects_duplicate_and_missing_request_ids() -> None:
    sink = InMemoryPerformanceSink()
    record = _finish_record("duplicate", "SELECT 1")
    sink.record(record)

    with pytest.raises(ValueError, match="duplicate request_id"):
        sink.record(record)

    assert sink.pop("duplicate") == record
    with pytest.raises(KeyError, match="missing") as missing_error:
        sink.pop("secret-request-42")
    assert "secret-request-42" not in str(missing_error.value)


def test_opt_in_middleware_records_safe_route_template() -> None:
    from backend.api.app import create_app

    sink = InMemoryPerformanceSink()
    paths = SimpleNamespace(version="v-test")
    app = create_app(performance_sink=sink, path_manager=paths)

    @app.get("/api/perf/{item_id}")
    def measured(item_id: int):
        connection = instrument_sqlite_connection(sqlite3.connect(":memory:"))
        connection.execute("SELECT 1").fetchone()
        connection.close()
        return {"id": item_id}

    response = TestClient(app).get(
        "/api/perf/99",
        headers={"x-request-id": "metric-99"},
    )
    record = sink.pop("metric-99")

    assert response.status_code == 200
    assert response.json() == {"id": 99}
    assert response.headers["x-request-id"] == "metric-99"
    assert record.route_template == "/api/perf/{item_id}"
    assert record.db_statements_total == 1
    assert record.db_select_statements == 1
    assert "/api/perf/99" not in repr(record)


def test_create_app_without_sink_does_not_start_performance_scope(
    monkeypatch,
) -> None:
    app_module = import_module("backend.api.app")

    def fail_if_called(request_id: str):
        raise AssertionError(f"unexpected performance scope: {request_id}")

    monkeypatch.setattr(
        app_module,
        "request_performance_scope",
        fail_if_called,
        raising=False,
    )

    response = TestClient(app_module.create_app()).get("/api/healthz")

    assert response.status_code == 200


def test_unmatched_route_uses_fixed_template_without_raw_path() -> None:
    from backend.api.app import create_app

    sink = InMemoryPerformanceSink()
    app = create_app(
        performance_sink=sink,
        path_manager=SimpleNamespace(version="v-test"),
    )

    response = TestClient(app).get(
        "/api/private-404-value",
        headers={"x-request-id": "metric-404"},
    )
    record = sink.pop("metric-404")

    assert response.status_code == 404
    assert record.status_code == 404
    assert record.route_template == "<unmatched>"
    assert "private-404-value" not in repr(record)


def test_handler_exception_records_500_and_is_reraised() -> None:
    from backend.api.app import create_app

    sink = InMemoryPerformanceSink()
    app = create_app(
        performance_sink=sink,
        path_manager=SimpleNamespace(version="v-test"),
    )

    @app.get("/api/perf/failure/{item_id}")
    def fail(item_id: int) -> None:
        raise RuntimeError("handler failure")

    with pytest.raises(RuntimeError, match="handler failure"):
        TestClient(app).get(
            "/api/perf/failure/77",
            headers={"x-request-id": "metric-500"},
        )

    record = sink.pop("metric-500")
    assert record.status_code == 500
    assert record.route_template == "/api/perf/failure/{item_id}"
    assert "/api/perf/failure/77" not in repr(record)


def test_sink_failure_leaves_original_response_unchanged(caplog) -> None:
    from backend.api.app import create_app

    class RaisingSink:
        def record(self, record) -> None:
            raise RuntimeError("private sink detail")

    app = create_app(
        performance_sink=RaisingSink(),
        path_manager=SimpleNamespace(version="v-test"),
    )

    @app.get("/api/perf/sink/{item_id}", status_code=202)
    def accepted(item_id: int):
        return {"accepted": item_id}

    with caplog.at_level("WARNING", logger="ai_grading.api"):
        response = TestClient(app).get(
            "/api/perf/sink/41",
            headers={"x-request-id": "metric-sink"},
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": 41}
    assert response.headers["x-request-id"] == "metric-sink"
    warning_text = " ".join(caplog.messages)
    assert "api_performance_record_failed" in warning_text
    assert "private sink detail" not in warning_text
    assert "/api/perf/sink/41" not in warning_text


def test_recorder_finish_failure_leaves_original_response_unchanged(
    caplog,
    monkeypatch,
) -> None:
    app_module = import_module("backend.api.app")

    class FailingRecorder:
        def finish(self, **kwargs):
            raise RuntimeError("private recorder detail")

    @contextmanager
    def failing_scope(request_id: str):
        yield FailingRecorder()

    monkeypatch.setattr(app_module, "request_performance_scope", failing_scope)
    app = app_module.create_app(
        performance_sink=InMemoryPerformanceSink(),
        path_manager=SimpleNamespace(version="v-test"),
    )

    @app.get("/api/perf/recorder/{item_id}", status_code=202)
    def accepted(item_id: int):
        return {"accepted": item_id}

    with caplog.at_level("WARNING", logger="ai_grading.api"):
        response = TestClient(app).get(
            "/api/perf/recorder/51",
            headers={"x-request-id": "metric-recorder"},
        )

    assert response.status_code == 202
    assert response.json() == {"accepted": 51}
    assert response.headers["x-request-id"] == "metric-recorder"
    warning_text = " ".join(caplog.messages)
    assert "api_performance_record_failed" in warning_text
    assert "private recorder detail" not in warning_text
    assert "/api/perf/recorder/51" not in warning_text


def test_supplied_path_manager_drives_app_health_and_lifespan_factories(
    monkeypatch,
    tmp_path,
) -> None:
    from backend.api import dependencies

    app_module = import_module("backend.api.app")

    data_root = tmp_path / "user_data"
    supplied_paths = SimpleNamespace(
        project_root=Path(__file__).resolve().parents[1],
        version="v-isolated",
        data_root=data_root,
        db_path=data_root / "databases" / "grading_system.db",
        qb_db_path=data_root / "databases" / "question_bank.db",
        reports_dir=data_root / "reports",
        exams_dir=data_root / "exams",
        templates_dir=data_root / "templates",
        upload_config_dir=data_root / "config" / "uploaded",
        outputs_dir=data_root / "outputs",
        backups_dir=data_root / "backups",
        ops_state_dir=data_root / "ops",
        api_profiles_path=tmp_path / "config" / "api_profiles.json",
        legacy_api_profiles_paths=(),
        workspace_dir=lambda workspace_id, *, create=False: (
            data_root / "workspaces" / workspace_id
        ),
    )
    captured: dict[str, object] = {}

    class Manager:
        is_shutdown = False

        def register(self, _name, _handler) -> None:
            return None

        def shutdown(self) -> None:
            self.is_shutdown = True

    manager = Manager()
    ops_service = object()

    def create_manager(paths):
        captured["manager_paths"] = paths
        return manager

    def create_ops_service(paths):
        captured["ops_paths"] = paths
        return ops_service

    monkeypatch.setattr(dependencies, "create_job_manager", create_manager)
    monkeypatch.setattr(dependencies, "create_ops_write_service", create_ops_service)

    def fail_if_default_paths_are_loaded():
        raise AssertionError("supplied paths must isolate the app")

    monkeypatch.setattr(
        app_module,
        "get_default_path_manager",
        fail_if_default_paths_are_loaded,
    )

    app = app_module.create_app(path_manager=supplied_paths)

    assert app.version == "v-isolated"
    assert app.state.path_manager is supplied_paths
    with TestClient(app) as client:
        response = client.get("/api/healthz")
        assert response.json()["version"] == "v-isolated"
        assert app.state.job_manager is manager
        assert app.state.ops_write_service is ops_service

    assert captured == {
        "manager_paths": supplied_paths,
        "ops_paths": supplied_paths,
    }
    assert manager.is_shutdown is True
