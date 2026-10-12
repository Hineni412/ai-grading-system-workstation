from __future__ import annotations

from pathlib import Path

REVIEW_TEST_PATHS = (Path('tests/test_api_review_routes.py'),)
REVIEW_FRONTEND_TEST_PATHS = (Path('frontend/src/__tests__/review-scoring-inspector.spec.ts'),)
SERIAL_TEST_PATHS = (
    Path('tests/api_e2e/test_failure_recovery.py'),
    Path('tests/api_e2e/test_five_flow.py'),
    Path('tests/test_ops_offline.py'),
)
PROCESS_ISOLATED_TEST_PATHS = (Path('tests/test_ops_offline.py'),)
RELEASE_AUDIT_TEST_PATHS = (Path('tests/test_p3_11_schema_version_gate.py'),)
QUICK_TEST_PATHS = (
    *REVIEW_TEST_PATHS,
    *SERIAL_TEST_PATHS[:2],
    Path('tests/test_api_config_editor.py'),
    Path('tests/test_llm_gateway.py'),
    Path('tests/training/test_personalized_recommendation.py'),
    Path('tests/test_report_ai_teacher_comparison.py'),
)
QUICK_FRONTEND_TEST_PATHS = (
    *REVIEW_FRONTEND_TEST_PATHS,
    Path('frontend/src/components/config/__tests__/config-save-result.spec.ts'),
)
DATABASE_BASELINE_TEST_PATHS = frozenset(Path(path) for path in (
    'tests/test_api_review_routes.py',
    'tests/test_api_config_editor.py',
    'tests/test_api_grading_run_control.py',
    'tests/test_api_question_bank_write_routes.py',
    'tests/test_api_training_routes.py',
    'tests/test_report_ai_teacher_comparison.py',
    'tests/test_session_question_bank_sync_job.py',
    'tests/training/test_personalized_recommendation.py',
    'tests/training/test_training_criterion_versioning.py',
))


def normalized_path(path: Path | str) -> str:
    return Path(path).as_posix()


SERIAL_TEST_KEYS = frozenset(map(normalized_path, SERIAL_TEST_PATHS))
RELEASE_AUDIT_TEST_KEYS = frozenset(map(normalized_path, RELEASE_AUDIT_TEST_PATHS))
QUICK_TEST_KEYS = frozenset(map(normalized_path, QUICK_TEST_PATHS))


def categories_for_path(path: Path | str) -> frozenset[str]:
    key = normalized_path(path)
    return frozenset(category for paths, category in (
        (SERIAL_TEST_KEYS, 'acceptance_serial'),
        (RELEASE_AUDIT_TEST_KEYS, 'release_audit'),
        (QUICK_TEST_KEYS, 'acceptance_quick'),
    ) if key in paths)
