"""Isolated, synthetic A/B browser-acceptance server.

This module must be launched through ``tools/run_project_module.py`` when the
portable runtime belongs to another linked worktree.  It deliberately imports
application modules only after all data/config/log paths have been redirected
to a fresh test-run directory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from starlette.requests import Request


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUNS_ROOT = PROJECT_ROOT / ".test-runs"
EVIDENCE_ROOT = PROJECT_ROOT / "output" / "playwright"
FAKE_API_KEY = "synthetic-loopback-only"
FAKE_MODEL = "ab-browser-synthetic-v1"
_MAX_MODEL_REQUEST_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AcceptancePaths:
    run_id: str
    run_root: Path
    data_root: Path
    logs_root: Path
    local_appdata: Path
    machine_config: Path
    taxonomy_state: Path
    ops_root: Path
    fixtures_root: Path
    evidence_root: Path


def _timestamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def _fresh_child(root: Path, name: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve(strict=True)
    target = root / name
    resolved_target = target.resolve(strict=False)
    try:
        resolved_target.relative_to(resolved_root)
    except ValueError as exc:
        raise RuntimeError("acceptance target escaped its controlled root") from exc
    if target.exists() or target.is_symlink():
        raise RuntimeError(f"acceptance target already exists: {target}")
    target.mkdir(parents=False)
    return target.resolve(strict=True)


def allocate_paths() -> AcceptancePaths:
    run_id = f"ab-browser-{_timestamp()}-{uuid4().hex[:8]}"
    run_root = _fresh_child(RUNS_ROOT, run_id)
    evidence_root = _fresh_child(EVIDENCE_ROOT, run_id)
    paths = AcceptancePaths(
        run_id=run_id,
        run_root=run_root,
        data_root=run_root / "data",
        logs_root=run_root / "logs",
        local_appdata=run_root / "local-appdata",
        machine_config=run_root / "machine-config",
        taxonomy_state=(
            run_root
            / "local-appdata"
            / "AIGradingSystem"
            / "config"
            / "taxonomy_state_v2.json"
        ),
        ops_root=run_root / "ops",
        fixtures_root=run_root / "fixtures",
        evidence_root=evidence_root,
    )
    for directory in (
        paths.data_root,
        paths.logs_root,
        paths.local_appdata,
        paths.machine_config,
        paths.ops_root,
        paths.fixtures_root,
        paths.evidence_root / "screenshots",
        paths.evidence_root / "network",
        paths.evidence_root / "reload-state",
        paths.evidence_root / "model-counts",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return paths


def configure_process(paths: AcceptancePaths) -> object:
    """Redirect every known mutable root before importing the application."""

    environment = {
        "AI_GRADING_WORKTREE_DATA_DIR": str(paths.data_root),
        "AI_GRADING_DATA_DIR": str(paths.data_root),
        "AI_GRADING_API_PROFILES_PATH": str(
            paths.machine_config / "api_profiles.json"
        ),
        "AI_GRADING_TAXONOMY_STATE_PATH": str(paths.taxonomy_state),
        "AI_GRADING_OPS_STATE_DIR": str(paths.ops_root),
        "AI_GRADING_PREVIEW_INSTANCE_ID": paths.run_id,
        "LOCALAPPDATA": str(paths.local_appdata),
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    }
    os.environ.update(environment)
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "AZURE_OPENAI_API_KEY",
        "LLM_API_KEY",
        "LLM_CONFIG_API_KEY",
        "LLM_BASE_URL",
        "LLM_CONFIG_BASE_URL",
        "LLM_GRADING_MODEL",
        "LLM_CONFIG_MODEL",
        "AUTHORIZATION",
        "COOKIE",
    ):
        os.environ.pop(key, None)

    # The approved full-text diagnostic journal is cwd-relative.  A unique cwd
    # keeps its synthetic bodies away from repository or production logs.
    os.chdir(paths.run_root)

    import path_manager as path_manager_module

    class AcceptancePathManager(path_manager_module.PathManager):
        @property
        def legacy_api_profiles_paths(self) -> tuple[Path, ...]:
            # Never probe project/user profile locations during acceptance.
            return ()

        @property
        def migration_project_root(self) -> Path:
            return PROJECT_ROOT

    path_manager = AcceptancePathManager()
    path_manager._logs_root = paths.logs_root
    path_manager._api_profiles_path = paths.machine_config / "api_profiles.json"
    path_manager._taxonomy_state_path = paths.taxonomy_state
    path_manager._ops_state_dir = paths.ops_root
    path_manager.ensure_directories()
    path_manager_module._instance = path_manager

    controlled = paths.run_root.resolve(strict=True)
    for candidate in (
        path_manager.data_root,
        path_manager.logs_dir,
        path_manager.api_profiles_path,
        path_manager.taxonomy_state_path,
        path_manager.ops_state_dir,
    ):
        try:
            Path(candidate).resolve(strict=False).relative_to(controlled)
        except ValueError as exc:
            raise RuntimeError(
                f"mutable acceptance path is not isolated: {candidate}"
            ) from exc
    return path_manager


def write_fake_profile(paths: AcceptancePaths, *, port: int) -> None:
    base_url = f"http://127.0.0.1:{port}/__acceptance__/v1"
    profile = {
        "name": "A/B 合成回环模型",
        "api_key": FAKE_API_KEY,
        "base_url": base_url,
        "config_api_key": FAKE_API_KEY,
        "config_base_url": base_url,
        "config_model": FAKE_MODEL,
        "class_teacher_model": FAKE_MODEL,
        "max_retries": 0,
    }
    target = paths.machine_config / "api_profiles.json"
    target.write_text(
        json.dumps([profile], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def seed(paths: AcceptancePaths, path_manager: object) -> tuple[dict[str, Any], object]:
    from backend.repositories.students import StudentRecord
    from backend.schema_migrations import ensure_application_schema
    from backend.workspaces.registry import load_default_workspace_registry
    from db_manager import DBManager

    ensure_application_schema(path_manager)
    database = DBManager(Path(path_manager.db_path))
    database.upsert_students(
        [
            StudentRecord(
                student_code="SYN-A001",
                name="合成学生甲",
                class_name="合成一班",
            ),
            StudentRecord(
                student_code="SYN-A002",
                name="合成学生乙",
                class_name="合成一班",
            ),
        ]
    )

    registry = load_default_workspace_registry(path_manager)
    registry.run_migrations()
    services = registry.create_services()
    class_teacher = services["class-teacher"]
    class_teacher.ordinary_database.initialize_schema()
    class_teacher.session_key("")
    preference = class_teacher.intake.preferences.get()
    preference = class_teacher.intake.preferences.set(
        homeroom_class="合成一班",
        expected_revision=int(preference["revision"]),
        expected_source_revision=str(preference["source_revision"]),
        operation_id="seed-homeroom-preference-001",
    )
    candidates = class_teacher.class_roster.ai_candidates(
        token="", class_label="合成一班"
    )
    if len(candidates) != 2:
        raise RuntimeError("synthetic class roster did not contain two students")

    manifest: dict[str, Any] = {
        "contract_version": "ab_browser_acceptance.v1",
        "run_id": paths.run_id,
        "synthetic_only": True,
        "automatic_model_retry": False,
        "routes": {
            "class_teacher": "/class-teacher?surface=home",
            "model_counts": "/__acceptance__/model-counts",
            "manifest": "/__acceptance__/manifest",
        },
        "class_teacher": {
            "homeroom_class": str(preference["homeroom_class"]),
            "students": [dict(item) for item in candidates],
            "flow_markers": {
                "record": "【合成记录事务】家长转述并带来医院书面材料，请按记录方式整理合成学生甲的已提供事实。",
                "plan_calendar": "【合成日程事务】请按计划日程方式整理本月班会准备。",
                "sop": "【合成流程事务】请按 SOP 方式整理教师已说明的学生争执处置流程。",
            },
        },
        "evidence_layout": {
            "screenshots": "screenshots",
            "network": "network",
            "reload_state": "reload-state",
            "model_counts": "model-counts",
        },
    }
    return manifest, registry


class FakeModelState:
    """In-memory-only response generator and physical-request counter."""

    def __init__(self, candidates: list[dict[str, str]]) -> None:
        self._candidates = tuple(dict(item) for item in candidates)
        self._counts: dict[str, int] = {}
        self._lock = threading.Lock()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counts = dict(sorted(self._counts.items()))
        return {
            "contract_version": "ab_model_counts.v1",
            "total_physical_requests": sum(counts.values()),
            "by_purpose": counts,
            "automatic_retry": False,
            "stored_request_or_response_bodies": False,
        }

    def complete(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages")
        if not isinstance(messages, list):
            raise ValueError("messages must be a list")
        purpose = self._purpose(messages)
        with self._lock:
            self._counts[purpose] = self._counts.get(purpose, 0) + 1
        if purpose == "class_teacher_draft_revision":
            content = self._draft_revision(messages)
        elif purpose == "class_teacher_intake":
            content = self._class_teacher_triage(messages)
        else:
            raise ValueError("acceptance fake received an unsupported purpose")
        return {
            "id": f"chatcmpl-synthetic-{uuid4().hex}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": FAKE_MODEL,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": json.dumps(
                            content,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        ),
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }

    @staticmethod
    def _message_text(messages: list[object]) -> str:
        values: list[str] = []
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if isinstance(content, str):
                values.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str):
                        values.append(str(item["text"]))
        return "\n".join(values)

    def _purpose(self, messages: list[object]) -> str:
        text = self._message_text(messages)
        if "class_teacher_draft_revision.v1" in text:
            return "class_teacher_draft_revision"
        if "班主任事务整理助手" in text:
            return "class_teacher_intake"
        return "unsupported"

    @staticmethod
    def _teacher_message(messages: list[object]) -> str:
        for message in reversed(messages):
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            content = message.get("content")
            if isinstance(content, str):
                return content
        return ""

    def _class_teacher_triage(self, messages: list[object]) -> dict[str, Any]:
        text = self._teacher_message(messages)
        common: dict[str, Any] = {
            "contract_version": "class_teacher_triage.v1",
            "assistant_message": "已形成一份仅供教师审核的合成草稿。",
            "clarification_questions": [],
        }
        if "【合成日程事务】" in text:
            item = {
                "work_item_id": "synthetic-plan-item-001",
                "domain": "activities_culture",
                "primary_mode": "plan_calendar",
                "secondary_modes": ["record"],
                "intent": "plan",
                "reason_summary": "教师请求整理班会准备日程",
                "subject_refs": [],
                "time_facts": [{"text": "2026年8月20日前完成"}],
                "safety_level": "normal",
                "missing_fields": [],
                "draft": {
                    "summary": "合成班会准备计划，待教师逐项确认。",
                    "plan_title": "合成班会准备",
                    "final_deadline": "2026-08-20T16:00:00+08:00",
                    "actions": [
                        {
                            "draft_action_id": "action-1",
                            "title": "确认合成班会议程",
                            "details": "仅使用合成验收信息",
                            "due_at": "2026-08-15T16:00:00+08:00",
                            "depends_on_draft_action_ids": [],
                        }
                    ],
                },
            }
        elif "【合成流程事务】" in text:
            item = {
                "work_item_id": "synthetic-sop-item-001",
                "domain": "conflict_safety",
                "primary_mode": "sop",
                "secondary_modes": ["record", "plan_calendar"],
                "intent": "follow_up",
                "reason_summary": "教师请求把已说明事实整理为待审流程",
                "subject_refs": [],
                "time_facts": [],
                "safety_level": "teacher_review_required",
                "missing_fields": ["请由教师选择学校流程模板"],
                "draft": {
                    "summary": "教师说明两名合成参与人发生争执，目前已分开且无人受伤；事实与步骤均待教师确认。",
                    "template_key": "",
                    "participant_refs": ["synthetic-a", "synthetic-b"],
                    "fact_source": "teacher_provided",
                    "teacher_confirmation_required": True,
                },
            }
        else:
            if not self._candidates:
                raise ValueError("synthetic student candidate is unavailable")
            candidate = next(
                (
                    item
                    for item in self._candidates
                    if item.get("display_name") == "合成学生甲"
                ),
                self._candidates[0],
            )
            revision = str(candidate["revision"])
            wrong_revision = (revision + "f" * 79)[:79]
            if wrong_revision == revision:
                wrong_revision = "f" * 79
            item = {
                "work_item_id": "synthetic-record-item-001",
                "domain": "student_support",
                "primary_mode": "record",
                "secondary_modes": [],
                "intent": "create",
                "reason_summary": "教师提供了待核对的支持事实",
                "subject_refs": [
                    {
                        "kind": "student",
                        "id": str(candidate["id"]),
                        "revision": wrong_revision,
                    }
                ],
                "time_facts": [{"text": "2026年8月由教师记录"}],
                "safety_level": "teacher_review_required",
                "missing_fields": [],
                "draft": {
                    "summary": "家长转述并提供了合成医院书面材料；这里只保留已提供事实，尚未形成系统诊断或处置决定。",
                    "observed_at": "2026-08-09T09:00:00+08:00",
                    "fact_source": "parent_and_hospital_provided",
                    "teacher_confirmation_required": True,
                },
            }
        return {**common, "work_items": [item]}

    @staticmethod
    def _draft_revision(messages: list[object]) -> dict[str, Any]:
        current: dict[str, Any] = {"summary": "合成草稿，待教师确认。"}
        for message in messages:
            if not isinstance(message, Mapping):
                continue
            content = message.get("content")
            if not isinstance(content, str) or not content.startswith("当前草稿："):
                continue
            try:
                parsed = json.loads(content.removeprefix("当前草稿："))
            except (ValueError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                current = parsed
        return {
            "contract_version": "class_teacher_draft_revision.v1",
            "content": current,
        }


def build_app(
    *,
    path_manager: object,
    registry: object,
    manifest: dict[str, Any],
    model_state: FakeModelState,
) -> object:
    from fastapi import HTTPException

    from backend.api.app import create_app

    app = create_app(path_manager=path_manager, workspace_registry=registry)

    @app.get("/__acceptance__/manifest")
    def acceptance_manifest() -> dict[str, Any]:
        return manifest

    @app.get("/__acceptance__/model-counts")
    def acceptance_model_counts() -> dict[str, Any]:
        return model_state.snapshot()

    @app.get("/__acceptance__/health")
    def acceptance_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "run_id": manifest["run_id"],
            "synthetic_only": True,
        }

    @app.post("/__acceptance__/v1/chat/completions")
    async def fake_chat_completions(request: Request) -> dict[str, Any]:
        if request.headers.get("authorization") != f"Bearer {FAKE_API_KEY}":
            raise HTTPException(status_code=401, detail="synthetic credential required")
        raw_length = request.headers.get("content-length")
        if raw_length and int(raw_length) > _MAX_MODEL_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="synthetic request too large")
        try:
            payload = await request.json()
            if not isinstance(payload, Mapping):
                raise TypeError("payload is not an object")
            return model_state.complete(payload)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=422,
                detail=f"unsupported synthetic model request: {type(exc).__name__}",
            ) from None

    return app


def write_manifests(
    paths: AcceptancePaths,
    manifest: dict[str, Any],
    *,
    port: int,
) -> None:
    public = {
        **manifest,
        "base_url": f"http://127.0.0.1:{port}",
    }
    (paths.run_root / "seed-manifest.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    private_fixture_index = {
        "run_id": paths.run_id,
        "fixtures": {
            path.name: str(path.resolve(strict=True))
            for path in sorted(paths.fixtures_root.iterdir())
            if path.is_file()
        },
        "evidence_root": str(paths.evidence_root),
    }
    (paths.run_root / "fixture-paths.json").write_text(
        json.dumps(private_fixture_index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (paths.evidence_root / "run-metadata.json").write_text(
        json.dumps(public, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_frontend() -> None:
    completed = subprocess.run(
        ["npm.cmd", "run", "build"],
        cwd=PROJECT_ROOT / "frontend",
        check=False,
        shell=False,
    )
    if completed.returncode != 0:
        raise RuntimeError("current frontend build failed")


def prepare(*, port: int, build_current_frontend: bool) -> tuple[object, AcceptancePaths, dict[str, Any]]:
    if not 1024 <= port <= 65535:
        raise RuntimeError("acceptance port must be between 1024 and 65535")
    if build_current_frontend:
        build_frontend()
    paths = allocate_paths()
    path_manager = configure_process(paths)
    write_fake_profile(paths, port=port)
    manifest, registry = seed(paths, path_manager)
    students = manifest["class_teacher"]["students"]
    model_state = FakeModelState([dict(item) for item in students])
    app = build_app(
        path_manager=path_manager,
        registry=registry,
        manifest=manifest,
        model_state=model_state,
    )
    write_manifests(paths, manifest, port=port)
    return app, paths, manifest


def self_check(*, port: int) -> int:
    from fastapi.testclient import TestClient

    app, paths, manifest = prepare(port=port, build_current_frontend=False)
    with TestClient(app) as client:
        checks = {
            "health": client.get("/api/healthz"),
            "acceptance_health": client.get("/__acceptance__/health"),
            "manifest": client.get("/__acceptance__/manifest"),
            "class_teacher": client.get("/api/class-teacher/vault/status"),
            "model_counts": client.get("/__acceptance__/model-counts"),
        }
        failures = {
            name: response.status_code
            for name, response in checks.items()
            if response.status_code != 200
        }
        if failures:
            raise RuntimeError(f"acceptance startup self-check failed: {failures}")
        counts = checks["model_counts"].json()
        if counts.get("total_physical_requests") != 0:
            raise RuntimeError("startup self-check unexpectedly called the model")
        if checks["manifest"].json().get("run_id") != manifest["run_id"]:
            raise RuntimeError("acceptance manifest changed during startup")
    print(
        json.dumps(
            {
                "status": "self-check-passed",
                "run_id": paths.run_id,
                "run_root": str(paths.run_root),
                "logs_root": str(paths.logs_root),
                "evidence_root": str(paths.evidence_root),
                "model_calls": 0,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def serve(*, host: str, port: int, build_current_frontend: bool) -> int:
    if host not in {"127.0.0.1", "localhost"}:
        raise RuntimeError("acceptance server may bind only to loopback")
    app, paths, manifest = prepare(
        port=port,
        build_current_frontend=build_current_frontend,
    )
    from backend.api.frontend import validate_frontend_dist

    validate_frontend_dist(PROJECT_ROOT / "frontend" / "dist")
    summary = {
        "status": "ready",
        "url": f"http://127.0.0.1:{port}",
        "run_id": paths.run_id,
        "run_root": str(paths.run_root),
        "data_root": str(paths.data_root),
        "local_appdata": str(paths.local_appdata),
        "logs_root": str(paths.logs_root),
        "evidence_root": str(paths.evidence_root),
        "model_counts_url": (
            f"http://127.0.0.1:{port}/__acceptance__/model-counts"
        ),
        "student_ids": [
            item["id"] for item in manifest["class_teacher"]["students"]
        ],
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=port,
        access_log=True,
        log_level="info",
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run isolated synthetic A/B browser acceptance",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    check = subparsers.add_parser("self-check")
    check.add_argument("--port", type=int, default=8765)
    server = subparsers.add_parser("serve")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    server.add_argument(
        "--build-frontend",
        action="store_true",
        help="build the current worktree frontend before serving",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.command == "self-check":
        return self_check(port=int(args.port))
    return serve(
        host=str(args.host),
        port=int(args.port),
        build_current_frontend=bool(args.build_frontend),
    )


if __name__ == "__main__":
    raise SystemExit(main())
