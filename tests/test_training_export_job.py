from __future__ import annotations

from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.services.training_task_service import TrainingTaskService


def _saved_task(db_path: Path):
    initialize_database(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO questions (
                id, question_number, question_type, question_text, answer_text, difficulty
            ) VALUES (201, '1', '解答题', '三角形全等训练', '证明过程', '5')
            """
        )
    return TrainingTaskService(db_path).create_task(
        {
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
                        }
                    ],
                }
            ],
        },
        created_by="teacher",
    )


def test_default_handlers_register_training_export(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=tmp_path / "question_bank.db",
            training_output_root=tmp_path / "outputs" / "training",
        )

        assert "training_export" in manager._handlers
    finally:
        manager.shutdown()


def test_training_export_job_publishes_one_variant_file(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    question_bank_db = tmp_path / "question_bank.db"
    task = _saved_task(question_bank_db)
    output_root = tmp_path / "outputs" / "training"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=question_bank_db,
            training_output_root=output_root,
        )
        job = manager.submit(
            "training_export",
            {
                "task_id": task.id,
                "variant_id": task.variants[0].id,
                "format": "markdown",
                "audience": "teacher",
            },
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "succeeded"
        assert loaded.result["task_id"] == task.id
        assert loaded.result["variant_id"] == task.variants[0].id
        assert loaded.result["export_ids"]
        published = Path(loaded.result["file_path"])
        assert published.is_file()
        assert published.suffix == ".md"
        assert published.parent == output_root / f"job-{job.id}"
        assert loaded.result["filename"] == published.name
        assert not list(output_root.glob(".job-*"))
    finally:
        manager.shutdown()


def test_training_export_job_publishes_task_bundle(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    question_bank_db = tmp_path / "question_bank.db"
    task = _saved_task(question_bank_db)
    output_root = tmp_path / "outputs" / "training"
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=question_bank_db,
            training_output_root=output_root,
        )
        job = manager.submit(
            "training_export",
            {"task_id": task.id, "format": "markdown"},
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "succeeded"
        published = Path(loaded.result["file_path"])
        assert published.is_file()
        assert published.suffix == ".zip"
        assert len(loaded.result["export_ids"]) == 3
        with connect(question_bank_db) as conn:
            paths = [
                Path(row[0])
                for row in conn.execute(
                    "SELECT output_path FROM training_exports WHERE task_id = ? ORDER BY id",
                    (task.id,),
                ).fetchall()
            ]
        assert all(path.parent == output_root / f"job-{job.id}" for path in paths)
        assert all(path.is_file() for path in paths)
    finally:
        manager.shutdown()


def test_cancel_requested_during_export_publishes_nothing(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.jobs.training_export import run_training_export_job
    from question_bank.services.training_export_service import TrainingExportService

    question_bank_db = tmp_path / "question_bank.db"
    task = _saved_task(question_bank_db)
    output_root = tmp_path / "outputs" / "training"

    def runner(**kwargs):
        context = kwargs["context"]

        def exporter(_db_path, _items, target_dir, *, audience, **_other):
            path = Path(target_dir) / f"{audience}.md"
            path.write_text("staged", encoding="utf-8")
            assert context.store.request_cancel(context.job_id)
            return path

        return run_training_export_job(
            **kwargs,
            service_factory=lambda db_path, output_dir: TrainingExportService(
                db_path,
                output_dir,
                exporters={"markdown": exporter},
            ),
        )

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=question_bank_db,
            training_output_root=output_root,
            training_export_runner=runner,
        )
        job = manager.submit(
            "training_export",
            {
                "task_id": task.id,
                "variant_id": task.variants[0].id,
                "format": "markdown",
                "audience": "teacher",
            },
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "cancelled"
        assert not (output_root / f"job-{job.id}").exists()
        assert not list(output_root.glob(".job-*"))
        with connect(question_bank_db) as conn:
            record = conn.execute(
                "SELECT status, output_path FROM training_exports WHERE task_id = ?",
                (task.id,),
            ).fetchone()
        assert tuple(record) == ("failed", None)
    finally:
        manager.shutdown()


def test_exporter_path_outside_staging_fails_closed(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.jobs.training_export import run_training_export_job
    from question_bank.services.training_export_service import TrainingExportService

    question_bank_db = tmp_path / "question_bank.db"
    task = _saved_task(question_bank_db)
    output_root = tmp_path / "outputs" / "training"
    outside = tmp_path / "outside.md"

    def exporter(_db_path, _items, _target_dir, **_kwargs):
        outside.write_text("outside", encoding="utf-8")
        return outside

    def runner(**kwargs):
        return run_training_export_job(
            **kwargs,
            service_factory=lambda db_path, output_dir: TrainingExportService(
                db_path,
                output_dir,
                exporters={"markdown": exporter},
            ),
        )

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=question_bank_db,
            training_output_root=output_root,
            training_export_runner=runner,
        )
        job = manager.submit(
            "training_export",
            {
                "task_id": task.id,
                "variant_id": task.variants[0].id,
                "format": "markdown",
                "audience": "teacher",
            },
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "failed"
        assert loaded.error == "training export failed"
        assert not (output_root / f"job-{job.id}").exists()
        assert not list(output_root.glob(".job-*"))
    finally:
        manager.shutdown()


def test_partial_bundle_record_failure_clears_earlier_success(tmp_path: Path) -> None:
    from backend.jobs.default_handlers import register_default_job_handlers
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore
    from backend.jobs.training_export import run_training_export_job
    from question_bank.services.training_export_service import TrainingExportService

    question_bank_db = tmp_path / "question_bank.db"
    task = _saved_task(question_bank_db)
    output_root = tmp_path / "outputs" / "training"

    def service_factory(db_path: Path, output_dir: Path) -> TrainingExportService:
        service = TrainingExportService(db_path, output_dir)
        real_create = service._create_record
        calls = 0

        def fail_third_record(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("second audience record failed")
            return real_create(*args, **kwargs)

        service._create_record = fail_third_record
        return service

    def runner(**kwargs):
        return run_training_export_job(
            **kwargs,
            service_factory=service_factory,
        )

    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)
    try:
        register_default_job_handlers(
            manager,
            db_path=tmp_path / "grading.db",
            reports_dir=tmp_path / "reports",
            question_bank_db_path=question_bank_db,
            training_output_root=output_root,
            training_export_runner=runner,
        )
        job = manager.submit(
            "training_export",
            {"task_id": task.id, "format": "markdown"},
        )
        manager.wait(job.id, timeout=5)

        loaded = manager.get(job.id)
        assert loaded is not None
        assert loaded.status == "failed"
        assert loaded.error == "training export failed"
        assert not (output_root / f"job-{job.id}").exists()
        assert not list(output_root.glob(".job-*"))
        records = TrainingExportService(question_bank_db, output_root).list_exports(
            task.id
        )
        assert len(records) == 2
        assert {record["status"] for record in records} == {"failed"}
        assert {record["output_path"] for record in records} == {None}
        stored = TrainingTaskService(question_bank_db).get_task(task.id)
        assert stored["status"] == "ready"
        assert stored["variants"][0]["status"] == "ready"
    finally:
        manager.shutdown()
