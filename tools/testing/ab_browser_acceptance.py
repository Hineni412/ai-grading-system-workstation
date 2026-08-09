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
import shutil
import subprocess
import threading
import time
import zipfile
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
        "AI_GRADING_TEACHING_PREP_ENABLED": "1",
        "AI_GRADING_TEACHING_PREP_WPS_ENABLED": "1",
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
        "teaching_prep_model": FAKE_MODEL,
        "class_teacher_model": FAKE_MODEL,
        "max_retries": 0,
    }
    target = paths.machine_config / "api_profiles.json"
    target.write_text(
        json.dumps([profile], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def create_pdf(path: Path, *, fixture_label: str) -> None:
    import fitz

    document = fitz.open()
    try:
        for page_number in range(1, 9):
            page = document.new_page(width=595, height=842)
            page.insert_text(
                (62, 54),
                f"SYNTHETIC WORKBOOK - {fixture_label.upper()}",
                fontsize=14,
            )
            if page_number == 1:
                page.insert_text((72, 105), "CONTENTS", fontsize=22)
                page.insert_text(
                    (82, 155), "Lesson One....................1", fontsize=15
                )
                page.insert_text(
                    (82, 190), "Lesson Two....................3", fontsize=15
                )
                page.insert_text(
                    (82, 240),
                    "All pages contain synthetic acceptance content only.",
                    fontsize=11,
                )
            elif page_number in {3, 5}:
                title = "Lesson One" if page_number == 3 else "Lesson Two"
                page.insert_text((72, 120), title, fontsize=24)
                page.insert_text(
                    (72, 175),
                    f"Worked synthetic example for {fixture_label}.",
                    fontsize=14,
                )
                page.insert_text((72, 225), "x + 3 = 7", fontsize=20)
            else:
                page.insert_text(
                    (72, 130),
                    f"Synthetic practice page {page_number}",
                    fontsize=20,
                )
                page.insert_text(
                    (72, 190),
                    "No real teacher, student, textbook, or assessment data.",
                    fontsize=12,
                )
            printed_page = max(1, page_number - 2)
            page.insert_text((290, 815), str(printed_page), fontsize=10)
        document.set_metadata(
            {
                "title": f"Synthetic {fixture_label} workbook",
                "author": "A/B browser acceptance harness",
                "subject": pathsafe_text(fixture_label),
            }
        )
        document.save(path)
    finally:
        document.close()
    if not path.is_file() or path.stat().st_size <= 0:
        raise RuntimeError("synthetic PDF creation failed")


def pathsafe_text(value: object) -> str:
    return "".join(
        character
        for character in str(value)
        if character.isalnum() or character in {"-", "_", " "}
    )[:120]


def create_valid_pptx(path: Path) -> None:
    helper = PROJECT_ROOT / "tools" / "testing" / "create_synthetic_pptx.ps1"
    creation_flags = (
        subprocess.CREATE_NO_WINDOW
        if hasattr(subprocess, "CREATE_NO_WINDOW")
        else 0
    )
    completed = subprocess.run(
        [
            "pwsh",
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(helper),
            "-OutputPath",
            str(path),
        ],
        cwd=path.parent,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
        shell=False,
        creationflags=creation_flags,
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "WPS failed").strip()
        raise RuntimeError(f"synthetic WPS PPTX creation failed: {message[-500:]}")
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise RuntimeError("WPS did not create a valid PPTX package")


def create_broken_pptx(path: Path) -> None:
    presentation = """
    <p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
      <p:sldSz cx="12192000" cy="6858000"/>
    </p:presentation>
    """.strip()
    slide = """
    <p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:spPr><a:xfrm>
        <a:off x="1200000" y="900000"/><a:ext cx="6000000" cy="1600000"/>
      </a:xfrm></p:spPr><p:txBody><a:p><a:r><a:t>{title}</a:t></a:r></a:p>
      </p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>
    """.strip()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ppt/presentation.xml", presentation)
        archive.writestr(
            "ppt/slides/slide1.xml",
            slide.format(title="STRUCTURAL FALLBACK FIXTURE 1"),
        )
        archive.writestr(
            "ppt/slides/slide2.xml",
            slide.format(title="STRUCTURAL FALLBACK FIXTURE 2 x = 2"),
        )


def create_fixtures(paths: AcceptancePaths) -> dict[str, Path]:
    fixtures = {
        "mapping_pdf": paths.fixtures_root / "synthetic-mapping-workbook.pdf",
        "quick_add_pdf": paths.fixtures_root / "synthetic-quick-add-workbook.pdf",
        "old_pdf": paths.fixtures_root / "synthetic-old-linked-workbook.pdf",
        "valid_pptx": paths.fixtures_root / "synthetic-two-slide-original.pptx",
        "broken_pptx": paths.fixtures_root / "synthetic-structural-fallback.pptx",
    }
    create_pdf(fixtures["mapping_pdf"], fixture_label="mapping")
    create_pdf(fixtures["quick_add_pdf"], fixture_label="quick-add")
    create_pdf(fixtures["old_pdf"], fixture_label="delete-and-reimport")
    create_valid_pptx(fixtures["valid_pptx"])
    create_broken_pptx(fixtures["broken_pptx"])
    return fixtures


def _import_material(
    service: object,
    source: Path,
    *,
    token: str,
    display_name: str,
    parse: bool,
) -> tuple[object, tuple[object, ...]]:
    staged = Path(service.paths["temp"]) / f"{token}{source.suffix.lower()}"
    shutil.copy2(source, staged)
    version, _created = service.import_material_copy(
        request_token=token,
        staged_path=staged,
        original_filename=source.name,
        display_name=display_name,
    )
    units = service.parse_material_version(version.id) if parse else ()
    return service.get_material_version(version.id), tuple(units)


def seed(paths: AcceptancePaths, path_manager: object) -> tuple[dict[str, Any], object]:
    from backend.repositories.students import StudentRecord
    from backend.schema_migrations import ensure_application_schema
    from backend.workspaces.registry import load_default_workspace_registry
    from db_manager import DBManager

    fixtures = create_fixtures(paths)
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
    teaching = services["teaching-prep"]
    class_teacher = services["class-teacher"]

    curriculum, semester, _created = teaching.create_semester_workspace(
        request_token="seed-semester-workspace-001",
        title="合成八年级上册",
        grade_level=8,
        volume="first",
        publisher="合成出版社",
        edition_label="浏览器验收版",
        school_year="2026-2027",
        term="first",
        planned_new_lesson_count=2,
    )
    chapter, _ = teaching.create_lesson_node(
        request_token="seed-chapter-node-001",
        curriculum_id=curriculum.id,
        parent_id=None,
        node_type="chapter",
        title="第一章 合成有理数",
    )
    section, _ = teaching.create_lesson_node(
        request_token="seed-section-node-001",
        curriculum_id=curriculum.id,
        parent_id=chapter.id,
        node_type="section",
        title="第一节 合成数轴",
    )
    lesson_one, _ = teaching.create_lesson_node(
        request_token="seed-lesson-node-001",
        curriculum_id=curriculum.id,
        parent_id=section.id,
        node_type="lesson",
        title="第1课时 合成有理数",
        duration_minutes=45,
    )
    lesson_two, _ = teaching.create_lesson_node(
        request_token="seed-lesson-node-002",
        curriculum_id=curriculum.id,
        parent_id=section.id,
        node_type="lesson",
        title="第2课时 合成数轴",
        duration_minutes=45,
    )
    teaching.set_semester_lesson_progress(
        semester.id,
        lesson_one.id,
        status="not_started",
        expected_revision=None,
    )
    teaching.set_semester_lesson_progress(
        semester.id,
        lesson_two.id,
        status="preparing",
        expected_revision=None,
    )

    mapping_version, mapping_units = _import_material(
        teaching,
        fixtures["mapping_pdf"],
        token="seed-mapping-material-001",
        display_name="合成待映射普通教辅",
        parse=True,
    )
    mapping_record, _ = teaching.attach_semester_material(
        semester.id,
        request_token="seed-mapping-attach-001",
        material_version_id=mapping_version.id,
        material_role="exercise_workbook",
        workbook_series="合成同步练习",
        workbook_volume="A",
    )

    quick_version, quick_units = _import_material(
        teaching,
        fixtures["quick_add_pdf"],
        token="seed-quick-add-material-001",
        display_name="合成本学期快捷添加教辅",
        parse=True,
    )
    quick_record, _ = teaching.attach_semester_material(
        semester.id,
        request_token="seed-quick-add-attach-001",
        material_version_id=quick_version.id,
        material_role="exercise_workbook",
        workbook_series="合成课时练习",
        workbook_volume="A",
    )

    old_version, old_units = _import_material(
        teaching,
        fixtures["old_pdf"],
        token="seed-old-material-001",
        display_name="合成待删除旧教辅",
        parse=True,
    )
    old_record, _ = teaching.attach_semester_material(
        semester.id,
        request_token="seed-old-attach-001",
        material_version_id=old_version.id,
        material_role="exercise_workbook",
        workbook_series="合成旧资料",
        workbook_volume="A",
    )
    old_link, _ = teaching.create_material_link(
        request_token="seed-old-link-001",
        lesson_node_id=lesson_two.id,
        material_version_id=old_version.id,
        start_unit=2,
        end_unit=3,
        crop=None,
        purpose="exercise",
        teacher_note="合成删除影响预览用关联",
        confirmation_status="confirmed",
    )

    valid_ppt, valid_ppt_units = _import_material(
        teaching,
        fixtures["valid_pptx"],
        token="seed-valid-ppt-material-001",
        display_name="合成两页真实课件",
        parse=True,
    )
    valid_ppt_record, _ = teaching.attach_semester_material(
        semester.id,
        request_token="seed-valid-ppt-attach-001",
        material_version_id=valid_ppt.id,
        material_role="reference_ppt",
    )
    valid_ppt_link, _ = teaching.create_material_link(
        request_token="seed-valid-ppt-link-001",
        lesson_node_id=lesson_one.id,
        material_version_id=valid_ppt.id,
        start_unit=1,
        end_unit=2,
        crop=None,
        purpose="reference_ppt",
        teacher_note="合成真实 WPS 原页预览",
        confirmation_status="confirmed",
    )

    broken_ppt, _ = _import_material(
        teaching,
        fixtures["broken_pptx"],
        token="seed-broken-ppt-material-001",
        display_name="合成待显式解析故障课件",
        parse=False,
    )
    broken_ppt_record, _ = teaching.attach_semester_material(
        semester.id,
        request_token="seed-broken-ppt-attach-001",
        material_version_id=broken_ppt.id,
        material_role="reference_ppt",
    )

    mapping_preflight = teaching.semester_mapping_preflight(
        semester.id,
        material_record_ids=[mapping_record.id],
    )
    if (
        mapping_preflight.get("will_call_model") is not True
        or mapping_preflight.get("model_available") is not True
        or int(mapping_preflight.get("toc_entry_count") or 0) < 2
        or int(mapping_preflight.get("directory_page_image_count") or 0) < 1
    ):
        raise RuntimeError("synthetic mapping material is not acceptance-ready")
    deletion_impact = teaching.preview_material_deletion(
        old_version.source_id,
        expected_revision=int(old_version.source_revision),
    )
    if deletion_impact.get("can_delete") is not True:
        raise RuntimeError("synthetic old material cannot exercise deletion")

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

    def material_entry(
        version: object,
        record: object,
        units: tuple[object, ...],
        *,
        fixture_key: str,
    ) -> dict[str, Any]:
        return {
            "source_id": str(version.source_id),
            "version_id": str(version.id),
            "semester_material_id": str(record.id),
            "display_name": str(version.display_name),
            "parse_status": str(version.inspection_status),
            "unit_ids": [str(item.id) for item in units],
            "fixture": fixtures[fixture_key].name,
        }

    manifest: dict[str, Any] = {
        "contract_version": "ab_browser_acceptance.v1",
        "run_id": paths.run_id,
        "synthetic_only": True,
        "automatic_model_retry": False,
        "routes": {
            "teaching_prep": "/teaching-prep",
            "teaching_prep_library": "/teaching-prep?view=library",
            "class_teacher": "/class-teacher?surface=home",
            "model_counts": "/__acceptance__/model-counts",
            "manifest": "/__acceptance__/manifest",
        },
        "teaching_prep": {
            "curriculum_id": str(curriculum.id),
            "semester_id": str(semester.id),
            "chapter_id": str(chapter.id),
            "section_id": str(section.id),
            "lesson_ids": [str(lesson_one.id), str(lesson_two.id)],
            "materials": {
                "mapping_workbook": material_entry(
                    mapping_version,
                    mapping_record,
                    mapping_units,
                    fixture_key="mapping_pdf",
                ),
                "quick_add_workbook": material_entry(
                    quick_version,
                    quick_record,
                    quick_units,
                    fixture_key="quick_add_pdf",
                ),
                "old_linked_workbook": {
                    **material_entry(
                        old_version,
                        old_record,
                        old_units,
                        fixture_key="old_pdf",
                    ),
                    "link_id": str(old_link.id),
                    "source_revision": int(old_version.source_revision),
                    "expected_delete_impact": dict(deletion_impact),
                },
                "valid_pptx": {
                    **material_entry(
                        valid_ppt,
                        valid_ppt_record,
                        valid_ppt_units,
                        fixture_key="valid_pptx",
                    ),
                    "link_id": str(valid_ppt_link.id),
                },
                "broken_pptx": material_entry(
                    broken_ppt,
                    broken_ppt_record,
                    (),
                    fixture_key="broken_pptx",
                ),
            },
            "mapping_preflight_expectation": {
                "will_call_model": bool(mapping_preflight["will_call_model"]),
                "model_available": bool(mapping_preflight["model_available"]),
                "toc_entry_count": int(mapping_preflight["toc_entry_count"]),
                "directory_page_image_count": int(
                    mapping_preflight["directory_page_image_count"]
                ),
                "directory_page_images_sent": bool(
                    mapping_preflight["directory_page_images_sent"]
                ),
                "automatic_retry": bool(mapping_preflight["automatic_retry"]),
            },
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
        if purpose == "semester_mapping":
            content = self._semester_mapping(messages)
        elif purpose == "class_teacher_draft_revision":
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
        if "资料目录的语义标注助手" in text:
            return "semester_mapping"
        if "class_teacher_draft_revision.v1" in text:
            return "class_teacher_draft_revision"
        if "班主任事务整理助手" in text:
            return "class_teacher_intake"
        return "unsupported"

    @staticmethod
    def _json_user_snapshot(messages: list[object]) -> dict[str, Any]:
        for message in messages:
            if not isinstance(message, Mapping) or message.get("role") != "user":
                continue
            content = message.get("content")
            candidates: list[str] = []
            if isinstance(content, str):
                candidates.append(content)
            elif isinstance(content, list):
                candidates.extend(
                    str(item["text"])
                    for item in content
                    if isinstance(item, Mapping) and isinstance(item.get("text"), str)
                )
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if isinstance(parsed, dict) and "mapping_mode" in parsed:
                    return parsed
        raise ValueError("semester mapping snapshot is unavailable")

    def _semester_mapping(self, messages: list[object]) -> dict[str, Any]:
        snapshot = self._json_user_snapshot(messages)
        evidence = snapshot.get("directory_evidence")
        evidence = evidence if isinstance(evidence, Mapping) else {}
        toc = evidence.get("toc_entries")
        toc_items = [item for item in toc if isinstance(item, Mapping)] if isinstance(toc, list) else []
        ranges = evidence.get("resolved_ranges")
        range_items = [item for item in ranges if isinstance(item, Mapping)] if isinstance(ranges, list) else []
        resolved_ids = {
            str(item.get("toc_evidence_id") or "")
            for item in range_items
            if str(item.get("toc_evidence_id") or "")
        }
        annotations = [
            {
                "evidence_id": str(item.get("evidence_id") or ""),
                "title": str(item.get("title") or "Synthetic section")[:160],
                "chapter_title": "",
                "section_title": "",
                "kind": "section",
            }
            for item in toc_items
        ]
        available = snapshot.get("available_lessons")
        lessons = [item for item in available if isinstance(item, Mapping)] if isinstance(available, list) else []
        matches: list[dict[str, Any]] = []
        eligible = [
            item
            for item in toc_items
            if str(item.get("evidence_id") or "") in resolved_ids
        ]
        for index, item in enumerate(eligible):
            if not lessons:
                break
            lesson = lessons[min(index, len(lessons) - 1)]
            matches.append(
                {
                    "lesson_ref": str(lesson.get("id") or ""),
                    "evidence_ids": [str(item.get("evidence_id") or "")],
                    "basis": "合成标题对应",
                }
            )
        return {
            "annotations": annotations,
            "matches": matches,
            "uncertainties": ["目录页不作为课时正文，页段请教师复核。"],
        }

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
            "teaching_prep": client.get("/api/teaching-prep/status"),
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
        "semester_id": manifest["teaching_prep"]["semester_id"],
        "lesson_ids": manifest["teaching_prep"]["lesson_ids"],
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
