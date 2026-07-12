from __future__ import annotations

from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services.training_export_service import TrainingExportService
from question_bank.services.training_task_service import TrainingTaskService


@pytest.fixture
def saved_task_system(tmp_path: Path) -> tuple[Path, Path, object]:
    db_path = tmp_path / "question_bank.db"
    output_dir = tmp_path / "exports"
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text, answer_text, difficulty
            ) VALUES (201, '1', '解答题', '原始任务题干', '原始答案', '5')
            """
        )
    plan = {
        "scope_snapshot": {"mode": "selected", "student_ids": ["12"]},
        "exam_scope": {"mode": "current", "session_ids": [14]},
        "diagnosis_snapshot": {
            "students": [
                {
                    "student_id": "12",
                    "student_name": "张三",
                    "class_id": "九年级1班",
                    "weak_points": [],
                }
            ]
        },
        "generation_config": {"question_count": 1},
        "variants": [
            {
                "variant_key": "student-12",
                "variant_type": "individual",
                "student_ids": ["12"],
                "items": [
                    {
                        "question_id": 201,
                        "item_order": 1,
                        "stage": "direct",
                        "recommend_score": 0.9,
                        "frequency": {
                            "available": True,
                            "exam_type": "中考",
                            "national_matched_question_count": 8,
                            "national_eligible_paper_count": 20,
                            "shenzhen_matched_question_count": 3,
                            "shenzhen_eligible_paper_count": 5,
                            "shenzhen_fit_available": True,
                            "shenzhen_fit_score": 0.8,
                        },
                    }
                ],
            }
        ],
    }
    task = TrainingTaskService(db_path).create_task(plan, created_by="teacher")
    return db_path, output_dir, task


def test_export_variant_creates_student_and_teacher_records(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system
    service = TrainingExportService(db_path, output_dir)

    bundle = service.export_variant(task.id, task.variants[0].id, formats=["markdown"])

    assert {item["audience"] for item in bundle["exports"]} == {"student", "teacher"}
    assert all(item["status"] == "succeeded" for item in bundle["exports"])
    assert all(Path(item["output_path"]).exists() for item in bundle["exports"])


def test_export_uses_immutable_task_snapshot_and_teacher_tracking_codes(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system
    with connect(db_path) as conn:
        conn.execute("UPDATE questions SET question_text = '后来修改的题干' WHERE id = 201")

    bundle = TrainingExportService(db_path, output_dir).export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
        audiences=["teacher"],
    )
    text = Path(bundle["exports"][0]["output_path"]).read_text(encoding="utf-8")

    assert "原始任务题干" in text
    assert "后来修改的题干" not in text
    assert task.task_code in text
    assert task.variants[0].items[0].task_item_code in text
    assert "深圳适配 80%" in text


def test_failed_export_can_retry_without_regenerating_task(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system
    calls = 0

    def flaky_exporter(_db_path, _items, target_dir, *, audience, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary export failure")
        path = Path(target_dir) / f"{audience}-retry.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("retry succeeded", encoding="utf-8")
        return path

    service = TrainingExportService(
        db_path,
        output_dir,
        exporters={"markdown": flaky_exporter},
    )
    failed = service.export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
        audiences=["student"],
    )["exports"][0]

    retried = service.retry_export(failed["id"])

    assert failed["status"] == "failed"
    assert retried["status"] == "succeeded"
    assert retried["retry_count"] == 1
    assert retried["task_id"] == task.id


def test_export_task_bundle_records_zip_from_saved_variants(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system
    service = TrainingExportService(db_path, output_dir)

    bundle = service.export_task_bundle(task.id, formats=["markdown"])

    assert bundle["export"]["audience"] == "bundle"
    assert bundle["export"]["status"] == "succeeded"
    assert Path(bundle["export"]["output_path"]).suffix == ".zip"


def test_bundle_is_failed_instead_of_silently_omitting_failed_audience(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system

    def partial_exporter(_db_path, _items, target_dir, *, audience, **_kwargs):
        if audience == "teacher":
            raise RuntimeError("teacher export failed")
        path = Path(target_dir) / "student.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("student", encoding="utf-8")
        return path

    bundle = TrainingExportService(
        db_path,
        output_dir,
        exporters={"markdown": partial_exporter},
    ).export_task_bundle(task.id, formats=["markdown"])

    assert bundle["export"]["status"] == "failed"
    assert "failed" in bundle["export"]["error_message"]


def test_aborted_attempt_preserves_prior_completed_task_and_variant(
    saved_task_system: tuple[Path, Path, object],
) -> None:
    db_path, output_dir, task = saved_task_system
    service = TrainingExportService(db_path, output_dir)
    first = service.export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
        audiences=["teacher"],
    )["exports"][0]
    second = service.export_variant(
        task.id,
        task.variants[0].id,
        formats=["markdown"],
        audiences=["student"],
    )["exports"][0]

    service.abort_unpublished_exports(
        [second["id"]],
        task_id=task.id,
        variant_ids=[task.variants[0].id],
    )

    stored = service.tasks.get_task(task.id)
    exports = {item["id"]: item for item in service.list_exports(task.id)}
    assert stored["status"] == "completed"
    assert stored["variants"][0]["status"] == "completed"
    assert exports[first["id"]]["status"] == "succeeded"
    assert exports[second["id"]]["status"] == "failed"
    assert exports[second["id"]]["output_path"] is None


def test_variant_final_state_failure_clears_succeeded_export_record(
    saved_task_system: tuple[Path, Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, output_dir, task = saved_task_system
    service = TrainingExportService(db_path, output_dir)
    real_mark = service.tasks.mark_export_state
    calls = 0

    def fail_final_state(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("late state failure")
        return real_mark(*args, **kwargs)

    monkeypatch.setattr(service.tasks, "mark_export_state", fail_final_state)

    with pytest.raises(RuntimeError, match="late state failure"):
        service.export_variant(
            task.id,
            task.variants[0].id,
            formats=["markdown"],
            audiences=["teacher"],
        )

    record = service.list_exports(task.id)[0]
    assert record["status"] == "failed"
    assert record["output_path"] is None


def test_bundle_final_state_failure_clears_all_created_export_records(
    saved_task_system: tuple[Path, Path, object],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path, output_dir, task = saved_task_system
    service = TrainingExportService(db_path, output_dir)
    real_mark = service.tasks.mark_export_state
    calls = 0

    def fail_bundle_final_state(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("bundle final state failure")
        return real_mark(*args, **kwargs)

    monkeypatch.setattr(service.tasks, "mark_export_state", fail_bundle_final_state)

    with pytest.raises(RuntimeError, match="bundle final state failure"):
        service.export_task_bundle(task.id, formats=["markdown"])

    records = service.list_exports(task.id)
    assert len(records) == 3
    assert {record["status"] for record in records} == {"failed"}
    assert {record["output_path"] for record in records} == {None}
