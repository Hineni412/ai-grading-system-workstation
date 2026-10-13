from __future__ import annotations
import json
import hashlib
import re
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from backend.repositories.db_manager import DBManager
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.database.schema import connect, initialize_database
from question_bank.services.source_question_link_service import SourceQuestionLinkService


import fitz


from question_bank.personalized_papers import CreatePaperCommand, PersonalizedPaperModule
from question_bank.personalized_papers.latex_render import LatexRenderError
from question_bank.recommendation.personalized import PersonalizedRecommendationConfig, PersonalizedRecommendationModule
from tests.training.test_personalized_recommendation import NOW, SK_PARENTS, _diagnosis, _install_release_with_skills, _seed_recommendation_sources
from typing import Any
from question_bank.solution_evidence import CoreResolution


from backend.api.app import create_app
from backend.api.dependencies import get_data_root, get_job_manager, get_question_bank_db_path, get_training_criterion_module
from backend.jobs.manager import JobManager
from backend.jobs.store import JobStore

from question_bank.training_criteria import TrainingCriterionModule


from dataclasses import replace
from io import BytesIO

import cv2

import numpy

from question_bank.personalized_papers import FreezePaperCommand

from question_bank.training_submissions import CreateScanBatchCommand, IngestUploadCommand, TrainingSubmissionModule


@pytest.fixture
def training_services(tmp_path: Path) -> DiagnosisProfileService:
    from backend.api.app import create_app  # Initialize routers before direct imports.
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
    from backend.api.dependencies import get_diagnosis_profile_service, get_request_diagnosis_profile_service

    diagnosis = training_services
    app = create_app()
    app.dependency_overrides[get_diagnosis_profile_service] = lambda: diagnosis
    app.dependency_overrides[get_request_diagnosis_profile_service] = lambda: diagnosis
    return TestClient(app)


def _expand_shared_display(payload):
    catalog = payload['weak_point_catalog']
    def expand(point):
        index, mastery, tier, observations, evidence, references = point
        metadata = catalog[index]
        return {'knowledge_key': metadata['knowledge_key'], 'knowledge_point': metadata['knowledge_point'],
                **({'mastery': mastery} if mastery is not None else {}), 'tier': tier,
                'observation_count': observations, 'evidence_count': evidence,
                **({'parent_knowledge_key': metadata['parent_knowledge_key']}
                   if 'parent_knowledge_key' in metadata else {}),
                'source_reference_count': references}
    return {**{key: value for key, value in payload.items() if key != 'weak_point_catalog'},
            'response_mode': 'display',
            'students': [{**student, 'weak_points': [expand(point) for point in student['weak_points']]}
                         for student in payload['students']],
            'group_weak_points': [expand(point) for point in payload['group_weak_points']]}


def test_training_diagnosis_uses_question_tag_identity(
    training_client: TestClient,
    training_services, monkeypatch,
) -> None:
    response = training_client.post(
        "/api/training/diagnosis",
        json={
            "scope": {"mode": "student", "student_ids": ["12"]},
            "exam_scope": {"mode": "current", "session_ids": [14]},
        },
    )

    assert response.status_code == 200, response.text
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
    with monkeypatch.context() as patch:
        patch.setattr(training_services, 'build_profiles', lambda **kwargs: pytest.fail('repeated public conversion'))
        repeated = training_client.post('/api/training/diagnosis', json={
            'scope': {'mode': 'student', 'student_ids': ['12']},
            'exam_scope': {'mode': 'current', 'session_ids': [14]},
        })
        assert repeated.content == response.content
    from backend.api.routers.training import _grouping_module
    from datetime import datetime, UTC
    grouped_calls = []
    def chapter_groups(**kwargs):
        grouped_calls.append(kwargs['config'].question_count)
        return {'groups': [], 'selection': None, 'unassigned': [],
                'warnings': [f"TEST-count-{kwargs['config'].question_count}"]}
    grouping = SimpleNamespace(db_path=training_services.question_bank_db_path,
        current_knowledge=SimpleNamespace(release_id='TEST-release'), clock=lambda: datetime(2026, 10, 3, tzinfo=UTC),
        chapter_groups=chapter_groups)
    from backend.api.routers import training as router_module
    monkeypatch.setattr(router_module, 'datetime', SimpleNamespace(now=lambda _tz: grouping.clock()))
    training_client.app.dependency_overrides[_grouping_module] = lambda: grouping
    grouped_body = {'scope': {'mode': 'student', 'student_ids': ['12']},
        'exam_scope': {'mode': 'current', 'session_ids': [14]},
        'grouping': {'scope_keys': ['kp_test_scope'], 'curriculum_volume_id': 'bnu24-math-g8-upper',
                     'question_count': 10, 'difficulty_max': 8, 'exclude_current_exam_originals': True}}
    grouped = training_client.post('/api/training/diagnosis', json=grouped_body)
    assert grouped.status_code == 200, grouped.text
    assert grouped.json()['grouping']['warnings'] == ['TEST-count-10']
    from backend.api.routers.training import _public_training_mapping
    from backend.api.schemas.training import TrainingDiagnosisResponse
    original = training_services.build_profiles(scope=grouped_body['scope'], exam_scope=grouped_body['exam_scope'])
    original['grouping'] = grouped.json()['grouping']
    expected = TrainingDiagnosisResponse.model_validate(_public_training_mapping(
        {k: v for k, v in original.items() if not k.startswith('_')})).model_dump(mode='json', exclude_none=True)
    assert grouped.content == json.dumps(expected, ensure_ascii=False, allow_nan=False,
                                       separators=(',', ':')).encode('utf-8')
    summary_fields = {'target_kind', 'knowledge_key', 'knowledge_point', 'mastery', 'tier',
        'observation_count', 'full_correct_count', 'score_sum', 'full_score_sum',
        'deduction_count', 'evidence_count', 'effective_weight', 'exam_count',
        'source_question_refs', 'parent_knowledge_key', 'direct_evidence_count',
        'child_evidence_count', 'precise_training_evidence_count'}
    for request_body, full_response in [
        ({'scope': {'mode': 'student', 'student_ids': ['12']},
          'exam_scope': {'mode': 'current', 'session_ids': [14]}}, response),
        (grouped_body, grouped),
    ]:
        compact_body = {**request_body, 'include_student_detail': False}
        compact = training_client.post('/api/training/diagnosis', json=compact_body)
        assert compact.status_code == 200
        full_payload, compact_payload = full_response.json(), compact.json()
        assert compact_payload['include_student_detail'] is False
        for field in full_payload.keys() - {'students', 'group_weak_points'}:
            assert compact_payload[field] == full_payload[field]
        assert len(compact_payload['students']) == len(full_payload['students'])
        pairs = [(compact_payload['group_weak_points'], full_payload['group_weak_points'])]
        for compact_student, full_student in zip(compact_payload['students'], full_payload['students']):
            assert {k: v for k, v in compact_student.items() if k != 'weak_points'} == {
                k: v for k, v in full_student.items() if k != 'weak_points'}
            pairs.append((compact_student['weak_points'], full_student['weak_points']))
        for compact_points, full_points in pairs:
            assert compact_points == [{k: v for k, v in point.items() if k in summary_fields}
                                      for point in full_points]
        assert len(compact.content) < len(full_response.content)
        display_body = {**request_body, 'response_mode': 'display'}
        display = training_client.post('/api/training/diagnosis', json=display_body)
        assert display.status_code == 200
        displayed = display.json()
        assert displayed['response_mode'] == 'display'
        assert 'include_student_detail' not in displayed
        for field in full_payload.keys() - {'students', 'group_weak_points', 'knowledge_catalog', 'knowledge_associations'}:
            assert displayed[field] == full_payload[field]
        display_fields = {'knowledge_key', 'knowledge_point', 'mastery', 'tier',
                          'observation_count', 'evidence_count', 'parent_knowledge_key'}
        display_pairs = [(displayed['group_weak_points'], full_payload['group_weak_points'])]
        assert len(displayed['students']) == len(full_payload['students'])
        for display_student, full_student in zip(displayed['students'], full_payload['students']):
            assert {k: v for k, v in display_student.items() if k != 'weak_points'} == {
                k: v for k, v in full_student.items() if k != 'weak_points'}
            display_pairs.append((display_student['weak_points'], full_student['weak_points']))
        for display_points, full_points in display_pairs:
            assert display_points == [{**{k: v for k, v in point.items() if k in display_fields},
                                       'source_reference_count': len(point['source_question_refs'])}
                                      for point in full_points]
        assert displayed['knowledge_catalog'] == [{k: v for k, v in node.items()
            if k in {'knowledge_key', 'knowledge_point', 'parent_knowledge_key', 'node_kind'}}
            for node in full_payload['knowledge_catalog']]
        assert len(display.content) < len(compact.content)
        shared_body = {**request_body, 'response_mode': 'display_shared'}
        shared = training_client.post('/api/training/diagnosis', json=shared_body)
        assert shared.status_code == 200, shared.text
        assert _expand_shared_display(shared.json()) == displayed
        assert training_client.post('/api/training/diagnosis', json={
            **shared_body, 'include_student_detail': False}).content == shared.content
        with monkeypatch.context() as patch:
            patch.setattr(training_services, 'build_profiles',
                          lambda **kwargs: pytest.fail('recomputed cached response variant'))
            assert training_client.post('/api/training/diagnosis', json=compact_body).content == compact.content
            assert training_client.post('/api/training/diagnosis', json=request_body).content == full_response.content
            assert training_client.post('/api/training/diagnosis', json=display_body).content == display.content
            assert training_client.post('/api/training/diagnosis', json=shared_body).content == shared.content
        assert training_client.post('/api/training/diagnosis', json={**display_body,
            'response_mode': 'unsupported'}).status_code == 422
    assert grouped_calls == [10]
    from copy import deepcopy
    from backend.api.routers.training import _validated_diagnosis_json
    # Direct serialization must preserve finite values and reject invalid
    # numbers, including numeric strings coerced by the response model.
    for value in [float('nan'), float('inf'), 'NaN', 'Infinity']:
        invalid = deepcopy(expected)
        invalid['students'][0]['weak_points'][0]['score_sum'] = value
        with pytest.raises(ValueError):
            _validated_diagnosis_json(_public_training_mapping(invalid, reject_nonfinite=True))
    invalid = deepcopy(expected)
    invalid['grouping']['TEST-value'] = float('inf')
    with pytest.raises(ValueError):
        _public_training_mapping(invalid, reject_nonfinite=True)
    assert training_client.post('/api/training/diagnosis', json=grouped_body).content == grouped.content
    assert grouped_calls == [10]
    from backend.api.routers.training import _grouped_diagnosis_response_bytes
    from backend.api.schemas.training import TrainingDiagnosisRequest
    request = TrainingDiagnosisRequest.model_validate(grouped_body)
    training_services.persist_snapshots = True
    saved = _grouped_diagnosis_response_bytes(training_services, grouping,
        scope=request.scope.model_dump(exclude_none=True), exam_scope=request.exam_scope.model_dump(exclude_none=True),
        grouping=request.grouping)
    assert saved == grouped.content
    saved_variants = []
    for variant in ({**grouped_body, 'include_student_detail': False},
                    {**grouped_body, 'response_mode': 'display'},
                    {**grouped_body, 'response_mode': 'display_shared'}):
        variant_request = TrainingDiagnosisRequest.model_validate(variant)
        encoded = _grouped_diagnosis_response_bytes(training_services, grouping,
            scope=variant_request.scope.model_dump(exclude_none=True),
            exam_scope=variant_request.exam_scope.model_dump(exclude_none=True), grouping=variant_request.grouping,
            include_student_detail=variant_request.include_student_detail, response_mode=variant_request.response_mode)
        saved_variants.append((variant, encoded))
    script = '''
import hashlib,json,sys
from types import SimpleNamespace
from datetime import datetime,UTC
from backend.api.app import create_app
from backend.api.routers import training
from backend.api.schemas.training import TrainingDiagnosisRequest
from integration.diagnosis_profile_service import DiagnosisProfileService
s=DiagnosisProfileService(sys.argv[1],sys.argv[2])
def fail(*args,**kwargs): raise AssertionError('recomputed full grouped response')
s.build_profiles=fail
training._build_grouped_diagnosis=fail
r=TrainingDiagnosisRequest.model_validate_json(sys.argv[3])
m=SimpleNamespace(current_knowledge=SimpleNamespace(release_id='TEST-release'),
    clock=lambda:datetime(2026,10,3,tzinfo=UTC))
result=training._grouped_diagnosis_response_bytes(s,m,scope=r.scope.model_dump(exclude_none=True),
    exam_scope=r.exam_scope.model_dump(exclude_none=True),grouping=r.grouping,
    include_student_detail=r.include_student_detail,response_mode=r.response_mode)
print(hashlib.sha256(result).hexdigest())
'''
    restored = subprocess.run([sys.executable, '-c', script, str(training_services.grading_db_path),
        str(training_services.question_bank_db_path), json.dumps(grouped_body)],
        capture_output=True, text=True, timeout=30)
    assert restored.returncode == 0, restored.stderr
    assert restored.stdout.strip() == hashlib.sha256(saved).hexdigest()
    for variant, encoded in saved_variants:
        restored_variant = subprocess.run([sys.executable, '-c', script, str(training_services.grading_db_path),
            str(training_services.question_bank_db_path), json.dumps(variant)],
            capture_output=True, text=True, timeout=30)
        assert restored_variant.returncode == 0, restored_variant.stderr
        assert restored_variant.stdout.strip() == hashlib.sha256(encoded).hexdigest()
    from backend.api.routers import training as router_module
    with monkeypatch.context() as patch:
        patch.setattr(router_module, 'get_personalized_recommendation_module',
            lambda: pytest.fail('cached grouped response constructed a module'))
        training_client.app.dependency_overrides.pop(_grouping_module)
        assert training_client.post('/api/training/diagnosis', json=grouped_body).content == grouped.content
    training_client.app.dependency_overrides[_grouping_module] = lambda: grouping
    grouped_body['grouping']['question_count'] = 8
    changed = training_client.post('/api/training/diagnosis', json=grouped_body)
    assert changed.status_code == 200
    assert changed.json()['grouping']['warnings'] == ['TEST-count-8']
    assert grouped_calls == [10, 8]
    with monkeypatch.context() as patch:
        patch.setattr(training_services, 'build_profiles', lambda **kwargs: pytest.fail('repeated grouped profile'))
        assert training_client.post('/api/training/diagnosis', json=grouped_body).content == changed.content
    asset = training_services.data_root / 'question_bank/rich_content/TEST-input.json'
    asset.parent.mkdir(parents=True, exist_ok=True)
    asset.write_text('{}', encoding='utf-8')
    assert training_client.post('/api/training/diagnosis', json=grouped_body).content == changed.content
    assert grouped_calls == [10, 8, 8]


def part_assessment_mastery__insert_feature_rows(conn, question_id, parts, *, fingerprint=None):
    """Write active formula difficulty rows: [(part_id, difficulty), ...]."""
    from question_bank.services.standard_difficulty import _legacy_question_content_fingerprint

    if fingerprint is None:
        row = conn.execute(
            "SELECT * FROM questions WHERE id=?", (int(question_id),)
        ).fetchone()
        fingerprint = _legacy_question_content_fingerprint(dict(row))
    for part_id, difficulty in parts:
        conn.execute(
            """INSERT INTO question_part_difficulty_features
               (question_id, part_id, features_json, formula_difficulty,
                formula_version, source_content_hash, is_active)
               VALUES (?,?,?,?,?,?,1)""",
            (
                int(question_id),
                str(part_id),
                json.dumps({"evidence": "合成难度依据"}),
                float(difficulty),
                "std-difficulty-v1",
                fingerprint,
            ),
        )


@pytest.fixture()
def part_assessment_mastery_refined_training_source(tmp_path):
    from copy import deepcopy
    from question_bank.database.schema import initialize_database, connect
    from question_bank.solution_evidence.contracts import CoreResolution, QuestionSolutionEvidence
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository
    from question_bank.solution_evidence.part_assessments import load_profiles
    from question_bank.training_criteria import QuestionAnalysisInputLoader, TrainingCriterionModule
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash, training_criteria_from_solution_evidence
    from tests.training.test_personalized_recommendation import _install_release_with_skills as _install_release_with_skills
    from tests.test_api_training_routes import solution_evidence_semantics__evidence_payload as _evidence_payload

    root = tmp_path / "data"
    path = root / "databases" / "question_bank.db"
    initialize_database(path)
    _install_release_with_skills(
        path,
        revision=3,
        skill_parents={
            "sk_alg_linear_equation": "kp_bnu24_math_g7_upper_5_2_1",
            "sk_geo_triangle_congruence": "kp_bnu24_math_g7_lower_4_3_9",
            "sk_geo_construction": "kp_bnu24_math_g7_lower_4_3_4",
            "sk_fun_linear": "kp_bnu24_math_g8_upper_4_2_2",
        },
    )
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,import_status) VALUES(1,'合成试卷','ready')"
        )
        conn.execute(
            "INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text,difficulty) VALUES(1,1,'1','解答题','合成第一问及第二问','合成答案',2)"
        )
        conn.execute(
            "INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(1,'knowledge_point','kp_geo_construction','synthetic')"
        )
        conn.execute(
            "INSERT INTO grading_question_links(grading_session_id,source_question_id,bank_question_id,link_method,status) VALUES('1','Q1',1,'manual','confirmed')"
        )
    from question_bank.services.rich_content_service import save_question_rich_content

    save_question_rich_content(
        1,
        root=root / "question_bank" / "rich_content",
        question_blocks=[{"type": "text", "text": "合成题目的完整排版正文"}],
    )
    question = QuestionAnalysisInputLoader(db_path=path, data_root=root).load((1,))[0]

    class Resolver:
        def resolve(self, key):
            return CoreResolution(
                status="resolved", stable_keys=(key,), reason="synthetic"
            )

    raw = _evidence_payload(1)
    raw["parts"][0]["evidence_points"][0]["fine_term_links"] = [
        {
            "fine_term_id": "sk_alg_linear_equation",
            "fine_term_name": "一元一次方程",
            "role": "direct",
        }
    ]
    second = deepcopy(raw["parts"][0])
    second.update(part_id="part-2", label="第2问", full_answer="证明三角形全等")
    second["evidence_points"][0].update(
        evidence_point_id="step-2",
        target="证明全等",
        observable_evidence="列出全等条件",
        fine_term_links=[
            {
                "fine_term_id": "sk_geo_triangle_congruence",
                "fine_term_name": "三角形全等",
                "role": "direct",
            }
        ],
    )
    raw["parts"].append(second)
    old = deepcopy(raw)
    old["parts"][1]["evidence_points"][0]["target"] = "旧的判定目标"
    make = lambda payload: QuestionSolutionEvidence.from_model_dict(
        payload,
        question_id=1,
        source_content_hash=solution_evidence_source_content_hash(question),
        resolver=Resolver(),
    )
    criteria = TrainingCriterionModule(path)
    criteria.propose(
        question=question,
        draft=training_criteria_from_solution_evidence(make(old), question=question),
        source_kind="backfill",
        source_reference="synthetic-old",
        actor_ref="test",
        reason="synthetic",
    )
    old_version = criteria.read(question)["current_version"]["version_id"]
    evidence = make(raw)
    version = SolutionEvidenceRepository(path).save(
        evidence,
        source_kind="backfill",
        source_reference="synthetic-refined",
        created_by="test",
    )
    with connect(path) as conn:
        part_assessment_mastery__insert_feature_rows(conn, 1, [("part-1", 2), ("part-2", 8)])
    return path, root, question, evidence, load_profiles(path, [1])[1], old_version


def personalized_papers__diagnosis(**kwargs):
    """Keep export fixtures within their candidate bank's 4-6 difficulty band."""
    diagnosis = _diagnosis(**kwargs)
    for student in diagnosis["students"]:
        for point in student["weak_points"]:
            for ref in point["source_question_refs"]:
                ref["score_awarded"] = 0.8 * ref["full_score"]
    return diagnosis


class personalized_papers_SyntheticPdfConverter:
    def __init__(self, *, fail: bool = False, pages: int = 2) -> None:
        self.fail = fail
        self.pages = pages

    def convert(self, source_docx: Path, output_pdf: Path) -> None:
        if self.fail:
            raise OSError("synthetic conversion failure")
        document = fitz.open()
        try:
            for page_number in range(1, self.pages + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic reviewed page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


class personalized_papers_SyntheticLatexCompiler:
    def __init__(self, *, fail: bool = False, pages: int = 2) -> None:
        self.fail = fail
        self.pages = pages

    @property
    def available(self) -> bool:
        return True

    def compile(self, tex_source: str, output_pdf: Path) -> None:
        if self.fail:
            raise LatexRenderError("synthetic latex failure")
        document = fitz.open()
        try:
            for page_number in range(1, self.pages + 1):
                page = document.new_page(width=595, height=842)
                page.insert_text(
                    fitz.Point(72, 96),
                    f"Synthetic latex page {page_number}",
                    fontsize=14,
                )
            document.save(output_pdf)
        finally:
            document.close()


@pytest.fixture()
def personalized_papers_paper_workspace(
    tmp_path: Path,
) -> tuple[PersonalizedPaperModule, dict[str, object], Path, Path]:
    db_path = tmp_path / "question_bank.db"
    data_root = tmp_path / "data"
    initialize_database(db_path)
    _install_release_with_skills(db_path, revision=3, skill_parents=SK_PARENTS)
    _seed_recommendation_sources(db_path, data_root)
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path,
        data_root=data_root,
        clock=lambda: NOW,
    )
    draft = recommendation.create(
        request_token="1" * 32,
        diagnosis=personalized_papers__diagnosis(student_ids=("SYN-S01",)),
        config=PersonalizedRecommendationConfig(
            question_count=8,
            expected_minutes=120,
        ),
        actor_ref="teacher-1",
    )
    module = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=personalized_papers_SyntheticPdfConverter(),
        latex_compiler=personalized_papers_SyntheticLatexCompiler(),
        clock=lambda: NOW,
    )
    return module, draft, db_path, data_root


def personalized_papers__create_command(
    token_character: str,
    draft: dict[str, object],
) -> CreatePaperCommand:
    return CreatePaperCommand(
        operation_token=token_character * 32,
        expected_draft_revision=int(draft["revision"]),
        student_id="SYN-S01",
        actor_ref="teacher-1",
    )


class solution_evidence_semantics_Resolver:
    def resolve(self, fine_term_id: str) -> CoreResolution:
        if fine_term_id in {"kp_alg_linear_equation", "knowledge-fine-linear"}:
            return CoreResolution(
                status="resolved",
                stable_keys=("kp_alg_linear_equation",),
                reason="test",
            )
        return CoreResolution(status="unmapped", reason="test")


def solution_evidence_semantics__evidence_payload(
    question_id: int,
    *,
    invented_term: bool = False,
) -> dict[str, Any]:
    direct_id = "invented-term" if invented_term else "kp_alg_linear_equation"
    direct_name = "模型新造词" if invented_term else "一元一次方程"
    return {
        "schema_version": "question-solution-evidence-v1",
        "question_id": question_id,
        "parts": [
            {
                "part_id": "part-1",
                "label": "第1问",
                "response_mode": "process_required",
                "canonical_answer": "",
                "accepted_forms": [],
                "full_answer": "由等式性质移项并化简，得到结论。",
                "proof_obligations": ["说明所用等式性质", "写出结论"],
                "visual_requirements": [],
                "deduction_policy": ["缺少关键变形依据时该步骤未达成"],
                "allow_alternative_methods": True,
                "evidence_points": [
                    {
                        "evidence_point_id": "step-1",
                        "target": "正确移项",
                        "observable_evidence": "写出等价变形后的方程",
                        "fine_term_links": [
                            {
                                "fine_term_id": direct_id,
                                "fine_term_name": direct_name,
                                "role": "direct",
                            },
                            {
                                "fine_term_id": "knowledge-fine-linear",
                                "fine_term_name": "移项解方程",
                                "role": "supporting_prerequisite",
                            },
                        ],
                        "equivalent_rules": ["等价变形顺序可不同"],
                        "counterexamples": ["改变等号一侧符号但另一侧不变"],
                    }
                ],
            }
        ],
        "auxiliary_rules": ["符号等价即可"],
        "rationale": "按可观察解题过程拆分。",
        "confidence": 0.92,
    }


def training_criterion_api__seed(database: Path) -> None:
    initialize_database(database)
    with connect(database) as connection:
        paper_id = connection.execute(
            """
            INSERT INTO papers (title, import_status)
            VALUES ('P4-10 API 合成试卷', 'completed')
            """
        ).lastrowid
        connection.executemany(
            """
            INSERT INTO questions (
                id, paper_id, question_number, question_type,
                question_text, answer_text
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (1, paper_id, "1", "计算题", "解方程 x+1=2", "x=1"),
                (2, paper_id, "2", "选择题", "选择正确选项", "C"),
            ),
        )


def training_criterion_api__client(tmp_path: Path):
    data_root = tmp_path / "data"
    database = data_root / "databases" / "question_bank.db"
    training_criterion_api__seed(database)
    module = TrainingCriterionModule(database)
    manager = JobManager(JobStore(tmp_path / "jobs.db"), max_workers=1)

    def backfill_handler(context):
        ids = module.claim_backfill(context.payload["run_id"])
        for question_id in ids:
            module.finish_backfill_item(
                run_id=context.payload["run_id"],
                question_id=question_id,
                status="failed",
                error_category="synthetic",
            )
        run = module.complete_backfill(context.payload["run_id"])
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "successful_question_ids": [],
            "failed_question_ids": list(ids),
            "retryable": True,
            "question_text": "must not leak",
        }

    manager.register("criterion_backfill", backfill_handler)
    app = create_app()
    app.dependency_overrides[get_data_root] = lambda: data_root
    app.dependency_overrides[get_question_bank_db_path] = lambda: database
    app.dependency_overrides[get_training_criterion_module] = lambda: module
    app.dependency_overrides[get_job_manager] = lambda: manager
    return TestClient(app), manager, module, database


def training_criterion_api__draft_body(*, revision: int, parent: str | None):
    return {
        "expected_revision": revision,
        "parent_version_id": parent,
        "reason": "教师手工建立判定点",
        "points": [
            {
                "point_id": "p-relation",
                "target": "建立等量关系",
                "observable_evidence": "列出正确方程",
                "equivalent_rules": [],
                "counterexamples": [],
            },
            {
                "point_id": "p-answer",
                "target": "得到结论",
                "observable_evidence": "写出 x=1",
                "equivalent_rules": ["1=x"],
                "counterexamples": [],
            },
        ],
        "auxiliary_rules": ["书写清晰但不计数"],
        "rationale": "教师核对",
        "confidence": 1,
    }


def test_teacher_can_create_review_and_read_immutable_criterion_version(
    tmp_path: Path,
) -> None:
    client, _manager, _module, database = training_criterion_api__client(tmp_path)

    missing = client.get("/api/question-bank/criteria/questions/1")
    created = client.post(
        "/api/question-bank/criteria/questions/1/drafts",
        json=training_criterion_api__draft_body(revision=0, parent=None),
    )

    assert missing.status_code == 200
    assert missing.json()["state"] == "missing"
    assert created.status_code == 200, created.text
    proposed = created.json()
    assert proposed["available"] is True
    version_id = proposed["current_version"]["version_id"]
    approved = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": proposed["revision"],
            "action": "approve",
            "reason": "原题、答案和判定点已核对",
        },
    )

    assert approved.status_code == 200, approved.text
    assert approved.json()["available"] is True
    old_version = client.get(f"/api/question-bank/criteria/versions/{version_id}")
    assert old_version.status_code == 200
    assert old_version.json()["status"] == "approved"

    with connect(database) as connection:
        connection.execute(
            "UPDATE questions SET question_text = ? WHERE id = 1",
            ("题干变化：解方程 x+2=3",),
        )
    stale = client.get("/api/question-bank/criteria/questions/1")
    assert stale.status_code == 200
    assert stale.json()["available"] is True
    assert stale.json()["current_version"]["status"] == "approved"
    assert (
        client.get(f"/api/question-bank/criteria/versions/{version_id}").json()[
            "criteria"
        ]
        == old_version.json()["criteria"]
    )


def test_atomic_criterion_approval_and_revision_conflict_are_public(
    tmp_path: Path,
) -> None:
    client, _manager, _module, _database = training_criterion_api__client(tmp_path)
    created = client.post(
        "/api/question-bank/criteria/questions/1/drafts",
        json={
            **training_criterion_api__draft_body(revision=0, parent=None),
            "points": [
                {
                    "point_id": "p-answer",
                    "target": "完成解答",
                    "observable_evidence": "答案正确",
                    "equivalent_rules": [],
                    "counterexamples": [],
                }
            ],
        },
    ).json()
    version_id = created["current_version"]["version_id"]

    quality = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": created["revision"],
            "action": "approve",
            "reason": "尝试批准",
        },
    )
    conflict = client.post(
        "/api/question-bank/criteria/questions/1/review",
        json={
            "version_id": version_id,
            "expected_revision": 99,
            "action": "reject",
            "reason": "过期页面",
        },
    )

    assert quality.status_code == 200
    assert quality.json()["current_version"]["status"] == "approved"
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == ("criterion_revision_conflict")


def test_shuffled_pages_group_by_page_identity_and_survive_restart(
    personalized_papers_paper_workspace,
) -> None:
    paper, draft, db_path, data_root = personalized_papers_paper_workspace
    instance = training_submissions__freeze(paper, draft, token="d")
    frozen_path, _ = paper.artifact_path(
        str(instance["paper_instance_id"]),
        "frozen-pdf",
    )
    pages = training_submissions__pdf_pages(frozen_path)
    module = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = module.create_batch(
        CreateScanBatchCommand(
            operation_token="e" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )

    shuffled = (pages[1], training_submissions__rotate_png(pages[0]))
    for index, content in enumerate(shuffled, start=1):
        batch = module.ingest(
            str(batch["batch_id"]),
            training_submissions__upload(
                token=f"{index + 1:x}",
                revision=int(batch["revision"]),
            ),
            BytesIO(content),
        )

    assert batch["status"] == "ready", [
        (
            page["page_number"],
            page["state"],
            page["issue_code"],
            page["rotation_degrees"],
            page["width_pixels"],
            page["height_pixels"],
        )
        for page in batch["pages"]
    ]
    assert batch["submissions"][0]["missing_pages"] == []
    assert [
        page["page_number"]
        for page in sorted(
            batch["pages"],
            key=lambda item: int(item["page_number"] or 0),
        )
    ] == [1, 2]
    assert all(page["state"] == "assigned" for page in batch["pages"])
    assert any(page["rotation_degrees"] != 0 for page in batch["pages"])
    restarted = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    history = restarted.list_batches(str(batch["paper_batch_id"]))
    assert [item["batch_id"] for item in history] == [batch["batch_id"]]
    assert history[0]["submission_count"] == 1
    assert history[0]["status"] == "ready"
    assert restarted.list_batches("0" * 64) == []
    assert restarted.get_batch(str(batch["batch_id"])) == batch
    finalized = restarted.finalize_submission(
        str(batch["submissions"][0]["submission_id"])
    )
    assert finalized["submission_revision"] == 1
    assert [page["page_number"] for page in finalized["pages"]] == [1, 2]

    # Continue the actual recovered scan through assessment and publication.
    # Only the model is replaced; persistence and mastery use the real modules.
    from backend.training_assessment import EvidenceSyncCommand, FakeTrainingAssessmentGateway, TrainingAssessmentModule
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.current_knowledge import CurrentKnowledgeResolver

    resolver = CurrentKnowledgeResolver.from_active_database(db_path)
    # The submission stores the draft's diagnosis (paper_workspace applies the
    # 0.8-rate fixture); reproduce it so the mastery comparison is identical.
    profile = personalized_papers__diagnosis(student_ids=("SYN-S01",))
    identity = ("SYN-S01", "sk_alg_linear_equation")
    before = CurrentMasteryCalculator(
        db_path, resolver, clock=lambda: NOW, data_root=data_root
    ).calculate(profile)[identity]
    gateway = FakeTrainingAssessmentGateway(
        lambda request: {
            "results": [
                {
                    "task_item_code": item.task_item_code,
                    "point_id": point["point_id"],
                    "state": "met",
                    "evidence": "合成答卷中的步骤和结论均已核对",
                }
                for item in request.items
                for point in item.points
            ]
        }
    )
    assessment = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    submission_id = str(batch["submissions"][0]["submission_id"])
    outcome = assessment.assess(submission_id, 1)
    assert gateway.calls == 1
    # Candidate results do not count until the teacher confirms publication.
    assert (
        CurrentMasteryCalculator(
            db_path, resolver, clock=lambda: NOW, data_root=data_root
        ).calculate(profile)[identity]
        == before
    )
    command = EvidenceSyncCommand(
        operation_token="8" * 32,
        expected_review_revision=outcome.review_revision,
        action="publish",
        actor_ref="teacher-1",
        reason="确认归卷批改结果并更新掌握度",
    )
    feedback = assessment.sync_evidence(submission_id, 1, command)
    assert feedback["summary"]["published_question_count"] == len(outcome.questions)
    assert feedback["summary"]["published_question_count"] > 0
    assert assessment.sync_evidence(submission_id, 1, command) == feedback
    change = next(
        item
        for item in feedback["mastery_changes"]
        if item["stable_key"] == identity[1]
    )
    assert change["mastery_after"]["value"] > before.value
    reopened = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW
    )
    assert reopened.get_feedback(submission_id, 1) == feedback
    after = CurrentMasteryCalculator(
        db_path, resolver, clock=lambda: NOW, data_root=data_root
    ).calculate(profile)[identity]
    assert after.value == change["mastery_after"]["value"]
    assert after.training_evidence_count == len(outcome.questions)
    target = next(
        item
        for item in feedback["next_round"]["student"]["targets"]
        if item["stable_key"] == identity[1]
    )
    assert target["value"] == after.value
    assert paper.get(str(instance["paper_instance_id"])) == instance

    another = restarted.create_batch(
        CreateScanBatchCommand(
            operation_token="f" * 32,
            paper_instance_ids=(str(instance["paper_instance_id"]),),
            actor_ref="teacher-1",
        )
    )
    assert [
        item["batch_id"]
        for item in restarted.list_batches(str(batch["paper_batch_id"]))
    ] == [
        another["batch_id"],
        batch["batch_id"],
    ]


@pytest.mark.parametrize("semester_scope", [False, True])
def test_refined_scanned_paper_updates_each_part_and_survives_reopen(
    part_assessment_mastery_refined_training_source, semester_scope
):
    from backend.training_assessment import EvidenceSyncCommand, FakeTrainingAssessmentGateway, TrainingAssessmentModule
    from question_bank.mastery.current import CurrentMasteryCalculator
    from question_bank.current_knowledge import CurrentKnowledgeResolver

    from backend.repositories.db_manager import DBManager
    from integration.diagnosis_profile_service import DiagnosisProfileService
    from question_bank.training_criteria.analysis import grading_config_skeleton_from_solution_evidence
    import backend.api.app
    from backend.api.routers.training import create_personalized_recommendation_draft
    from backend.api.schemas.training import PersonalizedRecommendationCreateRequest
    import json

    db_path, data_root, question, evidence, _, _ = part_assessment_mastery_refined_training_source
    grading_path = data_root / "databases" / "grading.db"
    grading = DBManager(grading_path)
    grading.initialize()
    rubric = grading_config_skeleton_from_solution_evidence(
        evidence, question_ref="Q1"
    )["rubric_question"]
    for index, part in enumerate(rubric["parts"], 1):
        part.update(part_id=f"Q1(P{index})", part_score=5)
    rubric_path = data_root / "synthetic-rubric.json"
    rubric_path.write_text(json.dumps({"questions": [rubric]}), encoding="utf-8")
    # 设计 E §7.4：旧会话先迁移出冻结证据快照，投影按 evidence_point_ids 归因。
    from question_bank.solution_evidence.evidence_snapshot import freeze_session_evidence_snapshot

    freeze_session_evidence_snapshot(
        db_path,
        grading_session_id=1,
        upload_config_dir=data_root / "config" / "uploaded",
        data_root=data_root,
    )
    with grading._connect() as conn:
        conn.execute(
            "INSERT INTO grading_sessions(id,session_name,rubric_path,answer_key_path,status,is_deleted) VALUES(1,'合成考试',?,'','completed',0)",
            (str(rubric_path),),
        )
        conn.execute(
            "UPDATE grading_sessions SET curriculum_volume_id='bnu24-math-g8-upper' WHERE id=1"
        )
        conn.execute(
            "INSERT INTO students(id,student_code,name,class_name) VALUES(1,'SYN-S01','合成学生','合成班')"
        )
        conn.execute(
            "INSERT INTO exam_papers(id,session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(1,1,'','',1,'matched','graded')"
        )
        conn.execute(
            "INSERT INTO session_results(id,session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json) VALUES(1,1,1,1,10,5,0,'{}')"
        )
        for part, score in (("Q1(P1)", 0), ("Q1(P2)", 5)):
            conn.execute(
                "INSERT INTO session_details(result_id,question_id,score_awarded,deduction_reason,knowledge_ids) VALUES(1,?,?,'',?)",
                (part, score, json.dumps(["UNKNOWN"])),
            )
        conn.execute("INSERT INTO students(id,student_code,name,class_name) VALUES(2,'SYN-S02','合成学生二','合成班')")
        conn.execute("INSERT INTO exam_papers(id,session_id,front_image,back_image,student_id,match_status,processing_status) VALUES(2,1,'','',2,'matched','graded')")
        conn.execute("INSERT INTO session_results(id,session_id,student_id,paper_id,total_score,student_score,needs_human_review,raw_json) VALUES(2,1,2,2,10,5,0,'{}')")
        for part, score in (("Q1(P1)", 5), ("Q1(P2)", 0)):
            conn.execute("INSERT INTO session_details(result_id,question_id,score_awarded,deduction_reason,knowledge_ids) VALUES(2,?,?,'',?)",
                         (part, score, json.dumps(["UNKNOWN"])))
    diagnosis_service = DiagnosisProfileService(
        grading_path, db_path, data_root=data_root
    )
    scope = {"mode": "selected", "student_ids": ["1"]}
    exam_scope = {"mode": "current", "session_ids": [1]}
    if semester_scope:
        exam_scope = {"mode": "semester", "curriculum_volume_id": "bnu24-math-g8-upper"}
    # Warm the real diagnosis cache before publishing training evidence.
    profile = diagnosis_service.build_profiles(scope=scope, exam_scope=exam_scope)
    # Teacher-visible keys plus the documented internal exam-source contract.
    assert {key for key in profile if str(key).startswith("_")} == {"_exam_source_metadata"}
    # A selected student and the whole population share the same fitted result.
    full_profile = diagnosis_service.build_profiles(scope={"mode": "all"}, exam_scope=exam_scope)
    full_student = next(student for student in full_profile["students"] if str(student["student_id"]) == "1")
    assert full_student["weak_points"] == profile["students"][0]["weak_points"]
    group_profile = diagnosis_service.build_profiles(scope={"mode": "class", "class_id": "合成班"}, exam_scope=exam_scope)
    assert next(student for student in group_profile["students"] if str(student["student_id"]) == "1")["weak_points"] == full_student["weak_points"]
    before = {
        point["knowledge_key"]: point["mastery"]
        for point in profile["students"][0]["weak_points"]
    }
    before_counts = {
        point["knowledge_key"]: point["evidence_count"]
        for point in profile["students"][0]["weak_points"]
    }
    frozen_profile = _diagnosis(student_ids=("SYN-S01",))
    frozen_profile["exam_scope"] = profile["exam_scope"]
    frozen_profile["students"][0]["student_id"] = "1"
    # The source exam is older than the three latest graded activities.
    graded_activities = [
        {"student_id": "1", "session_id": i, "occurred_at": f"2026-09-0{i}"}
        for i in (2, 3, 4)
    ]
    frozen_profile["students"][0]["weak_points"][0]["source_question_refs"][0][
        "question_difficulty"
    ] = 8
    # Strong-but-imperfect source: still a loss, but the readiness plan can
    # reach the difficulty-8 candidate instead of capping at foundation.
    frozen_profile["students"][0]["weak_points"][0]["source_question_refs"][0][
        "score_awarded"
    ] = 8
    recommendation = PersonalizedRecommendationModule(
        db_path=db_path, data_root=data_root, clock=lambda: NOW,
        semester_mastery=diagnosis_service.semester_mastery,
    )
    draft = recommendation.create(
        request_token="1" * 32,
        diagnosis=frozen_profile,
        graded_activities=graded_activities,
        config=PersonalizedRecommendationConfig(
            question_count=8, difficulty_max=8, exclude_current_exam_originals=False
        ),
        actor_ref="synthetic",
    )
    assert len(draft["students"][0]["items"]) == 1
    paper = PersonalizedPaperModule(
        db_path=db_path,
        data_root=data_root,
        pdf_converter=personalized_papers_SyntheticPdfConverter(),
        clock=lambda: NOW,
    )
    frozen = training_submissions__freeze(paper, draft, token="a")
    path, _ = paper.artifact_path(frozen["paper_instance_id"], "frozen-pdf")
    scans = TrainingSubmissionModule(db_path=db_path, data_root=data_root)
    batch = scans.create_batch(
        CreateScanBatchCommand(
            operation_token="c" * 32,
            paper_instance_ids=(frozen["paper_instance_id"],),
            actor_ref="synthetic",
        )
    )
    for index, content in enumerate(training_submissions__pdf_pages(path), 1):
        batch = scans.ingest(
            batch["batch_id"],
            training_submissions__upload(token=str(index), revision=batch["revision"]),
            BytesIO(content),
        )
    submission_id = batch["submissions"][0]["submission_id"]
    scans.finalize_submission(submission_id)
    first_points = {
        point.evidence_point_id for point in evidence.parts[0].evidence_points
    }
    gateway = FakeTrainingAssessmentGateway(
        lambda request: {
            "results": [
                {
                    "task_item_code": item.task_item_code,
                    "point_id": point["point_id"],
                    "state": "met" if point["point_id"] in first_points else "not_met",
                    "evidence": "合成第一问正确、第二问未达成",
                }
                for item in request.items
                for point in item.points
            ]
        }
    )
    module = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW,
        semester_mastery=diagnosis_service.semester_mastery,
    )
    outcome = module.assess(submission_id, 1)
    feedback = module.sync_evidence(
        submission_id,
        1,
        EvidenceSyncCommand(
            operation_token="d" * 32,
            expected_review_revision=outcome.review_revision,
            action="publish",
            actor_ref="synthetic",
            reason="确认逐小问结果",
        ),
    )
    assert feedback["summary"]["published_question_count"] == 1
    resolver = CurrentKnowledgeResolver.from_active_database(db_path)
    current = CurrentMasteryCalculator(
        db_path, resolver, data_root=data_root, clock=lambda: NOW,
        semester_mastery=diagnosis_service.semester_mastery,
    ).calculate(profile)
    first = current[("1", "sk_alg_linear_equation")]
    second = current[("1", "sk_geo_triangle_congruence")]
    assert first.training_evidence_count == second.training_evidence_count == 1
    # This fixture has one student and two differently difficult items. Global
    # item/source calibration also changes, so a fixed per-node direction is
    # not an independent expected result for the hierarchical model.
    assert first.full_correct_count == 1
    assert second.full_correct_count == 1
    for item in (first, second):
        assert item.observation_count == before_counts[item.stable_key] + 1
        assert item.interval_low <= item.value <= item.interval_high
        from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS
        assert item.parameter_version == CURRENT_MASTERY_PARAMETERS.version
        change = next(change for change in feedback["mastery_changes"] if change["stable_key"] == item.stable_key)
        assert change["mastery_before"]["value"] == before[item.stable_key]
        assert change["mastery_after"] == item.to_dict()
    reopened = TrainingAssessmentModule(
        db_path=db_path, data_root=data_root, gateway=gateway, clock=lambda: NOW,
        semester_mastery=diagnosis_service.semester_mastery,
    )
    assert reopened.get_feedback(submission_id, 1) == feedback
    assert (
        CurrentMasteryCalculator(
            db_path, resolver, data_root=data_root, clock=lambda: NOW,
            semester_mastery=diagnosis_service.semester_mastery,
        ).calculate(profile)
        == current
    )
    # A separate new-training request must refresh the cached diagnosis, while
    # the completed paper and its saved feedback remain unchanged.
    fresh = create_personalized_recommendation_draft(
        PersonalizedRecommendationCreateRequest(
            scope=scope,
            exam_scope=exam_scope,
            question_count=8,
            difficulty_max=8,
            exclude_current_exam_originals=False,
        ),
        diagnosis_service=diagnosis_service,
        module=recommendation,
    ).model_dump()
    target = next(
        item
        for item in fresh["students"][0]["targets"]
        if item["stable_key"] == "sk_alg_linear_equation"
    )
    assert target["value"] == first.value
    assert target["tier"] == first.tier
    assert target["evidence_count"] == before_counts["sk_alg_linear_equation"] + 1
    assert recommendation.get(draft["draft_id"]) == draft
    # Persist the current full population with the existing diagnosis snapshot.
    # A fresh process must reuse it for recommendation/graph callbacks too.
    if not hasattr(diagnosis_service, "_save_local_profile"):
        return
    import subprocess
    import sys
    diagnosis_service.persist_snapshots = True
    all_after = diagnosis_service.build_profiles(scope={"mode": "all"}, exam_scope=exam_scope)
    script = """
import json,sys
from integration.diagnosis_profile_service import DiagnosisProfileService
from question_bank.mastery.current import CurrentMasteryCalculator
def fail(*args,**kwargs): raise AssertionError('recomputed saved population')
service=DiagnosisProfileService(sys.argv[1],sys.argv[2],data_root=sys.argv[3])
service._compute_tag_profiles=fail
CurrentMasteryCalculator.calculate=fail
profile=service.build_profiles(scope={'mode':'all'},exam_scope=json.loads(sys.argv[4]))
population=service.semester_mastery(profile)
value=population['1','sk_alg_linear_equation']
print(json.dumps({'value':value.value,'count':value.observation_count,'tier':value.tier}))
"""
    process = subprocess.run([sys.executable, "-c", script, str(grading_path), str(db_path),
        str(data_root), json.dumps(exam_scope)], capture_output=True, text=True, timeout=30)
    assert process.returncode == 0, process.stderr
    saved = next(point for student in all_after["students"] if str(student["student_id"]) == "1"
                 for point in student["weak_points"] if point["knowledge_key"] == first.stable_key)
    assert json.loads(process.stdout) == {"value": first.value, "count": first.observation_count, "tier": saved["tier"]}
    assert reopened.get_feedback(submission_id, 1) == feedback
    assert paper.get(frozen["paper_instance_id"]) == frozen
    if semester_scope:
        other = diagnosis_service.build_profiles(
            scope=scope,
            exam_scope={
                "mode": "semester",
                "curriculum_volume_id": "bnu24-math-g7-lower",
            },
        )
        assert not other["exam_scope"]["session_ids"]
        assert not any(
            point["evidence_count"] for point in other["students"][0]["weak_points"]
        )
        # A current-term training result remains available with no exam attached.
        with grading._connect() as conn:
            conn.execute(
                "UPDATE grading_sessions SET curriculum_volume_id='bnu24-math-g7-lower' WHERE id=1"
            )
        training_only = diagnosis_service.build_profiles(
            scope=scope, exam_scope=exam_scope
        )
        assert training_only["students"][0]["score_rate"] is None
        assert not training_only["exam_scope"]["session_ids"]
        point = next(
            point
            for point in training_only["students"][0]["weak_points"]
            if point["knowledge_key"] == "sk_alg_linear_equation"
        )
        assert point["evidence_count"] == 1
        assert point["mastery"] is not None
        assert paper.get(frozen["paper_instance_id"]) == frozen
        assert reopened.get_feedback(submission_id, 1) == feedback


def training_submissions__freeze(paper, draft, *, token: str):
    instance = paper.create_review_instance(
        str(draft["draft_id"]),
        replace(
            personalized_papers__create_command(token, draft),
            student_id=str(draft["students"][0]["student_id"]),
        ),
    )
    review, _ = paper.artifact_path(str(instance["paper_instance_id"]), "review-docx")
    content = review.read_bytes()
    return paper.freeze(
        str(instance["paper_instance_id"]),
        FreezePaperCommand(
            operation_token=f"{int(token, 16) + 1:x}" * 32,
            expected_revision=int(instance["revision"]),
            filename="reviewed.docx",
            actor_ref="teacher-1",
        ),
        BytesIO(content),
    )


def training_submissions__pdf_pages(path: Path) -> tuple[bytes, ...]:
    result: list[bytes] = []
    with fitz.open(path) as document:
        for page in document:
            pixmap = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            image = numpy.frombuffer(pixmap.samples, dtype=numpy.uint8).reshape(
                pixmap.height,
                pixmap.width,
                3,
            )
            success, encoded = cv2.imencode(
                ".png",
                cv2.cvtColor(image, cv2.COLOR_RGB2BGR),
            )
            assert success
            result.append(bytes(encoded))
    return tuple(result)


def training_submissions__upload(
    *,
    token: str,
    revision: int,
) -> IngestUploadCommand:
    return IngestUploadCommand(
        operation_token=token * 32,
        expected_revision=revision,
        filename="scan.png",
        media_type="image/png",
        actor_ref="teacher-1",
    )


def training_submissions__rotate_png(content: bytes) -> bytes:
    image = cv2.imdecode(numpy.frombuffer(content, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    rotated = cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    success, encoded = cv2.imencode(".png", rotated)
    assert success
    return bytes(encoded)
