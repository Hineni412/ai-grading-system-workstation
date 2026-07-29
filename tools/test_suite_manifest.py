from __future__ import annotations

from pathlib import Path


# These files exercise process-wide state, Windows file handles, subprocesses,
# or timing-sensitive locks. Keep each file intact and run this lane with one
# pytest process after the parallel lane has finished.
SERIAL_TEST_PATHS = (
    Path("tests/api_e2e/test_failure_recovery.py"),
    Path("tests/api_e2e/test_five_flow.py"),
    Path("tests/api_e2e/test_harness.py"),
    Path("tests/api_e2e/test_restart_recovery.py"),
    Path("tests/test_answer_region_commit_service.py"),
    Path("tests/test_answer_region_draft_service.py"),
    Path("tests/test_answer_region_session_lock.py"),
    Path("tests/test_api_ops_routes.py"),
    Path("tests/test_config_source_service.py"),
    Path("tests/test_job_manager.py"),
    Path("tests/test_ops_lock.py"),
    Path("tests/test_p1_29_acceptance.py"),
    Path("tests/test_performance_benchmark.py"),
    Path("tests/test_question_bank_local_file_dialog.py"),
    Path("tests/test_request_connection_benchmark.py"),
    Path("tests/test_review_media_service.py"),
    Path("tests/test_secure_config_filesystem.py"),
    Path("tests/test_storage_maintenance.py"),
)

# A serial lane avoids simultaneous access, but these files also inherit
# process-global state from earlier files. Give each one a fresh pytest
# process so prior dependency overrides cannot change its result.
PROCESS_ISOLATED_TEST_PATHS = (
    Path("tests/test_api_ops_routes.py"),
)


# These tests protect frozen phase evidence, packaging, retirement assertions,
# and benchmark publication. They remain available for a release, but they do
# not need to delay every P3.5 merge candidate.
RELEASE_AUDIT_TEST_PATHS = (
    Path("tests/test_frontend_portable_packaging.py"),
    Path("tests/test_handoff_status.py"),
    Path("tests/test_p1_29_acceptance.py"),
    Path("tests/test_p2_20_acceptance.py"),
    Path("tests/test_p2_22_streamlit_retirement.py"),
    Path("tests/test_p3_01_structural_baseline.py"),
    Path("tests/test_p3_18_performance.py"),
    Path("tests/test_p3_19_phase3_gate.py"),
    Path("tests/test_performance_benchmark.py"),
    Path("tests/test_performance_dataset.py"),
    Path("tests/test_performance_report.py"),
    Path("tests/test_request_connection_benchmark.py"),
)


# A small cross-module sentinel set for routine development. Paths are reused
# directly; no duplicate "quick versions" of product tests are maintained.
QUICK_TEST_PATHS = (
    Path("test_answer_normalizer.py"),
    Path("tests/api_e2e/test_five_flow.py"),
    Path("tests/test_annotation_margin_layout.py"),
    Path("tests/test_api_app.py"),
    Path("tests/test_api_grading_run_control.py"),
    Path("tests/test_api_job_lifecycle.py"),
    Path("tests/test_api_openapi_contract.py"),
    Path("tests/test_api_report_jobs.py"),
    Path("tests/test_api_scan_grading_workspace.py"),
    Path("tests/test_grading_completeness.py"),
    Path("tests/test_grading_limits.py"),
    Path("tests/test_frontend_foundation.py"),
    Path("tests/test_job_manager.py"),
    Path("tests/test_llm_execution_control.py"),
    Path("tests/test_llm_gateway.py"),
    Path("tests/test_model_profile_execution_settings.py"),
    Path("tests/test_question_id_contract.py"),
    Path("tests/test_question_id_readonly_integration.py"),
    Path("tests/test_report_export_job.py"),
    Path("tests/test_report_print_layout.py"),
    Path("tests/test_request_pacer.py"),
    Path("tests/test_run_bat_api_entry.py"),
    Path("tests/test_scan_grading_workspace_service.py"),
    Path("tests/test_test_suite_runner.py"),
)


def normalized_path(path: Path | str) -> str:
    return Path(path).as_posix()


SERIAL_TEST_KEYS = frozenset(normalized_path(path) for path in SERIAL_TEST_PATHS)
RELEASE_AUDIT_TEST_KEYS = frozenset(
    normalized_path(path) for path in RELEASE_AUDIT_TEST_PATHS
)
QUICK_TEST_KEYS = frozenset(normalized_path(path) for path in QUICK_TEST_PATHS)


def categories_for_path(path: Path | str) -> frozenset[str]:
    key = normalized_path(path)
    categories: set[str] = set()
    if key in SERIAL_TEST_KEYS:
        categories.add("acceptance_serial")
    if key in RELEASE_AUDIT_TEST_KEYS:
        categories.add("release_audit")
    if key in QUICK_TEST_KEYS:
        categories.add("acceptance_quick")
    return frozenset(categories)
