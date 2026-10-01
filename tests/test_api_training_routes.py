from __future__ import annotations

import json
import hashlib
import pickle
import re
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
import warnings
from pathlib import Path

import pytest


warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
)

from fastapi.testclient import TestClient

from db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import (
    SourceQuestionLinkService,
)


def _clear_profile_memory():
    from integration import diagnosis_profile_service as profiles
    from integration.data_generation import reset_commit_generations
    with profiles._TAG_PROFILE_CACHE_LOCK:
        profiles._TAG_PROFILE_CACHE.clear()
    profiles._LOCAL_SOURCE_REVISIONS.clear()
    reset_commit_generations()


def test_saved_profiles_survive_new_process_and_wal_checkpoint(training_services):
    service = training_services
    scope, exams = {"mode": "all"}, {"mode": "current", "session_ids": [14]}
    with sqlite3.connect(service.grading_db_path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE students SET name='合成学生龘' WHERE id=12")
        writer.commit()
        service.persist_snapshots = True
        expected = service.build_profiles(scope=scope, exam_scope=exams)
        class_profile = service.build_profiles(scope={"mode":"class", "class_id":"八年级1班"}, exam_scope=exams)
        assert len(class_profile['students']) == 2
        assert service._local_profile_path().is_file()
        digest = hashlib.sha256(pickle.dumps((expected, service.latest_aggregated_mastery))).hexdigest()
        writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    _clear_profile_memory()
    # A new interpreter has neither memory results nor process-local commit
    # counters. Fail if it attempts any full diagnosis/model recalculation.
    script = """
import hashlib,json,pickle,sys
from integration.diagnosis_profile_service import DiagnosisProfileService
s=DiagnosisProfileService(sys.argv[1],sys.argv[2])
def fail(**kwargs): raise AssertionError('recomputed saved diagnosis')
s._compute_tag_profiles=fail
d=s.build_profiles(scope={'mode':'all'},exam_scope={'mode':'current','session_ids':[14]})
c=s.build_profiles(scope={'mode':'class','class_ids':['八年级1班']},exam_scope={'mode':'current','session_ids':[14]})
assert len(c['students'])==2
print(json.dumps({'digest':hashlib.sha256(pickle.dumps((d,s.latest_aggregated_mastery))).hexdigest(),'students':len(d['students'])}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(service.grading_db_path),
                             str(service.question_bank_db_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"digest": digest, "students": 2}


@pytest.mark.parametrize("change", ["teacher_score", "training_record", "knowledge", "parameters", "cause", "corrupt"])
def test_local_profiles_refresh_changed_inputs_and_corrupt_cache(training_services, monkeypatch, change):
    from integration import diagnosis_profile_service as profiles
    service = training_services
    scope, exams = {"mode": "all"}, {"mode": "current", "session_ids": [14]}
    service.persist_snapshots = True
    before = service.build_profiles(scope=scope, exam_scope=exams)
    assert service._local_profile_path().is_file()
    _clear_profile_memory()
    if change == "teacher_score":
        with sqlite3.connect(service.grading_db_path) as writer:
            writer.execute("UPDATE session_details SET score_awarded=10 WHERE result_id=14001 AND question_id='Q1'")
            writer.execute("UPDATE session_results SET student_score=15 WHERE id=14001")
    elif change == "training_record":
        with sqlite3.connect(service.question_bank_db_path) as writer:
            # A committed training-side change must not reuse an earlier result,
            # including when checkpointing folds it into the main file.
            writer.execute("INSERT INTO training_tasks(task_code,status) VALUES('TEST-local-profile-refresh','completed')")
    elif change == "knowledge":
        with sqlite3.connect(service.question_bank_db_path) as writer:
            writer.execute("UPDATE question_tags SET tag_value='合成更新主题' WHERE question_id=101 AND tag_type='knowledge_point'")
    elif change == "parameters":
        old = profiles._profile_semantics()
        monkeypatch.setattr(profiles, "_profile_semantics", lambda: (*old, "TEST-new-parameters"))
    elif change == "cause":
        cause = service.data_root / "reports/.class_analysis/TEST-cause.json"
        cause.parent.mkdir(parents=True, exist_ok=True)
        cause.write_text('{}', encoding='utf-8')
    else:
        service._local_profile_path().write_bytes(b"TEST-truncated-snapshot")
    fresh = DiagnosisProfileService(service.grading_db_path, service.question_bank_db_path)
    compute = fresh._compute_tag_profiles
    calls = []
    def tracked(**kwargs):
        calls.append(True)
        return compute(**kwargs)
    monkeypatch.setattr(fresh, "_compute_tag_profiles", tracked)
    after = fresh.build_profiles(scope=scope, exam_scope=exams)
    assert calls == [True]
    if change == "teacher_score":
        assert next(s for s in after['students'] if s['student_id'] == '12')['score_rate'] == pytest.approx(.75)
        assert after != before
    if change == "knowledge":
        assert after != before


def test_idle_prewarm_saves_local_profiles_without_blocking_grading(training_services, monkeypatch):
    from integration.training_prewarm import TrainingPrewarmWorker
    service = training_services
    paths = SimpleNamespace(db_path=service.grading_db_path, qb_db_path=service.question_bank_db_path,
                            data_root=service.data_root)
    jobs = SimpleNamespace(active=True)
    jobs.list = lambda **kwargs: ([{'status': 'running'}] if jobs.active else [], 0)
    worker = TrainingPrewarmWorker(paths, jobs)
    operation = lambda: worker._compute("diagnosis", {"mode":"all"}, {"mode":"current","session_ids":[14]}, {})
    monkeypatch.setattr(worker, "_refresh_plan", lambda: [operation])
    assert not worker.tick()
    assert not service._local_profile_path().exists()
    jobs.active = False
    assert worker.tick()
    assert worker.batches[-1][0] == 1
    assert service._local_profile_path().exists()
    _clear_profile_memory()
    monkeypatch.setattr(DiagnosisProfileService, "_compute_tag_profiles",
        lambda *args, **kwargs: pytest.fail("prewarm recomputed saved profile"))
    worker._startup_done = False
    assert worker.tick()
    assert worker.batches[-1][0] == 1


def test_local_snapshot_save_failure_preserves_profile_result(training_services, monkeypatch):
    from integration import diagnosis_profile_service as profiles
    service = training_services
    service.persist_snapshots = True
    monkeypatch.setattr(profiles.os, "replace", lambda *args: (_ for _ in ()).throw(PermissionError("TEST disk unavailable")))
    result = service.build_profiles(scope={"mode":"all"}, exam_scope={"mode":"current","session_ids":[14]})
    assert len(result['students']) == 2
    assert not service._local_profile_path().exists()
    assert not list(service._local_profile_path().parent.iterdir())


def test_source_changed_during_preparation_does_not_publish_stale_snapshot(training_services, monkeypatch):
    service = training_services
    service.persist_snapshots = True
    original = service._compute_tag_profiles
    def changed_after_read(**kwargs):
        result = original(**kwargs)
        with sqlite3.connect(service.grading_db_path) as writer:
            writer.execute("UPDATE students SET name='TEST-after-read' WHERE id=12")
        return result
    monkeypatch.setattr(service, "_compute_tag_profiles", changed_after_read)
    result = service.build_profiles(scope={"mode":"all"}, exam_scope={"mode":"current","session_ids":[14]})
    assert next(s for s in result['students'] if s['student_id']=='12')['student_name'] != 'TEST-after-read'
    assert not service._local_profile_path().exists()


@pytest.fixture
def training_services(tmp_path: Path) -> DiagnosisProfileService:
    # db 必须放在名为 "databases" 的目录下,生产代码据此把 tmp_path 推断为
    # data root(受控根),否则 resolve_stored_file_path 会拒绝 tmp_path 下的
    # rubric.json 等存储路径。
    db_dir = tmp_path / "databases"
    db_dir.mkdir()
    grading_db_path = db_dir / "grading.db"
    question_bank_db_path = db_dir / "question_bank.db"
    grading_db = DBManager(grading_db_path)
    grading_db.initialize()
    initialize_database(question_bank_db_path)

    rubric_path = tmp_path / "rubric.json"
    rubric_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"question_id": "Q1", "max_score": 10},
                    {"question_id": "Q2", "max_score": 5},
                    {"question_id": "Q3", "max_score": 5},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    with grading_db._connect() as conn:
        conn.executemany(
            "INSERT INTO students (id, student_code, name, class_name) VALUES (?, ?, ?, ?)",
            [
                (12, "S12", "学生甲", "八年级1班"),
                (15, "S15", "学生乙", "八年级1班"),
            ],
        )
        conn.execute(
            """
            INSERT INTO grading_sessions (
                id, session_name, rubric_path, answer_key_path, status, is_deleted
            ) VALUES (14, '当前考试', ?, '', 'completed', 0)
            """,
            (str(rubric_path),),
        )
        conn.execute(
            """
            INSERT INTO exam_papers (
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES (1401, 14, '', '', 12, 'matched', 'graded')
            """
        )
        conn.execute(
            """
            INSERT INTO session_results (
                id, session_id, student_id, paper_id, total_score, student_score,
                needs_human_review, raw_json
            ) VALUES (14001, 14, 12, 1401, 20, 11, 0, '{}')
            """
        )
        conn.executemany(
            """
            INSERT INTO session_details (
                result_id, question_id, score_awarded, deduction_reason,
                knowledge_ids, error_category, error_summary,
                secondary_errors_json
            ) VALUES (14001, ?, ?, ?, '["UNKNOWN"]', ?, ?, ?)
            """,
            [
                (
                    "Q1",
                    6,
                    "缺少辅助线",
                    "逻辑断裂",
                    "辅助线思路缺失",
                    json.dumps(
                        [{"category": "审题错误", "summary": "条件识别不完整"}],
                        ensure_ascii=False,
                    ),
                ),
                ("Q2", 5, "", None, None, "[]"),
            ],
        )

    with connect(question_bank_db_path) as conn:
        conn.executemany(
            """
            INSERT INTO papers (id, title, source_file, import_status)
            VALUES (?, ?, ?, 'success')
            """,
            [(1, "合成考试", "C:/private/source.docx")]
            + [
                (question_id, f"合成练习 {question_id}", f"practice-{question_id}.docx")
                for question_id in range(201, 209)
            ],
        )
        conn.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type, question_text, difficulty
            ) VALUES (?, ?, ?, '解答题', ?, '5')
            """,
            [
                (101, 1, "1", "当前考试原题"),
                (102, 1, "2", "已掌握的题"),
                (201, 201, "11", "利用边角关系证明两个三角形全等"),
                (202, 202, "12", "根据中点条件构造全等三角形"),
                (203, 203, "13", "在折叠图形中寻找对应边并完成证明"),
                (204, 204, "14", "结合平行线性质判定三角形全等"),
                (205, 205, "15", "运用角平分线条件求未知线段长度"),
                (206, 206, "16", "从旋转图形中识别全等关系"),
                (207, 207, "17", "添加辅助线后证明两条线段相等"),
                (208, 208, "18", "在复杂几何图中选择合适的全等判定"),
            ],
        )
        conn.executemany(
            "INSERT INTO question_tags (question_id, tag_type, tag_value) VALUES (?, ?, ?)",
            [
                (question_id, "knowledge_point", "三角形全等")
                for question_id in [101, 102, *range(201, 209)]
            ]
            + [(101, "method", "构造辅助线")],
        )

    links = SourceQuestionLinkService(question_bank_db_path)
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q1",
        bank_question_id=101,
        link_method="paper_question_number",
    )
    links.confirm_link(
        grading_session_id=14,
        source_question_id="Q2",
        bank_question_id=102,
        link_method="paper_question_number",
    )

    return DiagnosisProfileService(grading_db_path, question_bank_db_path)


@pytest.fixture
def training_client(training_services) -> TestClient:
    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_diagnosis_profile_service,
        get_request_diagnosis_profile_service,
    )

    diagnosis = training_services
    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: diagnosis
    return TestClient(app)


def test_training_diagnosis_uses_question_tag_identity(
    training_client: TestClient,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert not any(key.startswith("_") for key in payload)
    assert payload["diagnosis_identity"] == "question_tag"
    assert payload["scope"]["mode"] == "student"
    assert payload["scope"]["student_ids"] == ["12"]
    assert payload["scope"]["matched_student_count"] == 1
    assert re.fullmatch(r"[0-9a-f]{64}", payload["scope"]["scope_revision"])
    assert (
        payload["scope"]["student_score_profiles"]["12"]["score_rate_source"]
        == "current_exam"
    )
    assert payload["exam_scope"]["session_ids"] == [14]
    weak = payload["students"][0]["weak_points"][0]
    assert weak["knowledge_point"] == "三角形全等"
    assert weak["mastery"] == pytest.approx(11 / 15, abs=0.0001)
    assert weak["tag_context"]["method"] == ["构造辅助线"]
    assert payload["coverage"] == {
        "covered_items": 2,
        "total_items": 3,
        "missing_items": {"Q3": "missing_link"},
    }
    assert "C:/private" not in response.text
