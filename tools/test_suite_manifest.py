from __future__ import annotations

from pathlib import Path


# Focused review acceptance reuses existing rules and adds a real browser flow.
REVIEW_TEST_PATHS = (
    Path("tests/test_api_review_routes.py"),
    Path("tests/test_review_step_confirmation.py"),
    Path("tests/test_manual_review_atomic.py"),
    Path("tests/test_review_results_question_id_compatibility.py"),
)
REVIEW_FRONTEND_TEST_PATHS = (
    Path("frontend/src/__tests__/review-scoring-inspector.spec.ts"),
    Path("frontend/src/__tests__/review-drafts-store.spec.ts"),
    Path("frontend/src/__tests__/review-batch-workspace.spec.ts"),
    Path("frontend/src/__tests__/review-queue-view.spec.ts"),
)


# These files exercise process-wide state, Windows file handles, subprocesses,
# or timing-sensitive locks. Keep each file intact and run this lane with one
# pytest process after the parallel lane has finished.
SERIAL_TEST_PATHS = (
    Path("tests/api_e2e/test_failure_recovery.py"),
    Path("tests/api_e2e/test_five_flow.py"),
    Path("tests/api_e2e/test_restart_recovery.py"),
    Path("tests/test_answer_region_commit_service.py"),
    Path("tests/test_answer_region_draft_service.py"),
    Path("tests/test_api_ops_routes.py"),
    Path("tests/test_config_source_service.py"),
    Path("tests/test_job_manager.py"),
    Path("tests/test_ops_lock.py"),
    Path("tests/test_performance_benchmark.py"),
    Path("tests/test_request_connection_benchmark.py"),
    Path("tests/test_review_media_service.py"),
)

# A serial lane avoids simultaneous access, but these files also inherit
# process-global state from earlier files. Give each one a fresh pytest
# process so prior dependency overrides cannot change its result.
PROCESS_ISOLATED_TEST_PATHS = (Path("tests/test_api_ops_routes.py"),)


# These tests protect packaging, historical upgrades, and benchmark
# publication. They remain available for a release, but do not need to delay
# every routine development check.
RELEASE_AUDIT_TEST_PATHS = (
    Path("tests/test_frontend_portable_packaging.py"),
    Path("tests/test_p3_19_phase3_gate.py"),
    Path("tests/test_performance_benchmark.py"),
    Path("tests/test_performance_dataset.py"),
    Path("tests/test_request_connection_benchmark.py"),
)


# A small cross-module sentinel set for routine development. Paths are reused
# directly; no duplicate "quick versions" of product tests are maintained.
QUICK_TEST_PATHS = (
    Path("tests/api_e2e/test_failure_recovery.py"),
    Path("tests/api_e2e/test_five_flow.py"),
    Path("tests/test_manual_review_atomic.py"),
    Path("tests/phase4/test_personalized_recommendation.py"),
    Path("tests/test_api_config_editor.py"),
    Path("tests/test_llm_gateway.py"),
    Path("tests/test_api_report_jobs.py"),
)


# Paths are relative to the project root, like the backend manifest. The full
# frontend suite still discovers every spec; only quick runs use this selection.
QUICK_FRONTEND_TEST_PATHS = (
    Path("frontend/src/api/__tests__/client.spec.ts"),
    Path("frontend/src/components/config/__tests__/config-save-result.spec.ts"),
    Path("frontend/src/__tests__/review-scoring-inspector.spec.ts"),
    Path("frontend/src/__tests__/scan-grading-view.spec.ts"),
    Path("frontend/src/__tests__/personalized-recommendation-draft.spec.ts"),
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


# Business tests need independent current databases, not repeated historical upgrades.
# Schema, migration, recovery tooling and API end-to-end startup keep real fresh initialization.
DATABASE_BASELINE_TEST_PATHS = frozenset(
    Path(path)
    for path in (
        "tests/phase4/test_combined_question_analysis.py",
        "tests/phase4/test_part_assessment_mastery.py",
        "tests/phase4/test_personalized_papers.py",
        "tests/phase4/test_personalized_recommendation.py",
        "tests/phase4/test_solution_evidence_semantics.py",
        "tests/phase4/test_teaching_skill_repair.py",
        "tests/phase4/test_training_assessment.py",
        "tests/phase4/test_training_criterion_api.py",
        "tests/phase4/test_training_criterion_versioning.py",
        "tests/phase4/test_training_submissions.py",
        "tests/phase5/test_exam_evidence_projection.py",
        "tests/phase7/test_scope_mode_and_links.py",
        "tests/phase7/test_topic_skill_matching.py",
        "tests/test_analysis_report.py",
        "tests/test_answer_draft.py",
        "tests/test_answer_region_commit_service.py",
        "tests/test_api_config_editor.py",
        "tests/test_api_config_generation_jobs.py",
        "tests/test_api_grading_run_control.py",
        "tests/test_api_legacy_job_rows.py",
        "tests/test_api_media_routes.py",
        "tests/test_api_question_bank_jobs.py",
        "tests/test_api_question_bank_paper_trash.py",
        "tests/test_api_question_bank_routes.py",
        "tests/test_api_question_bank_write_routes.py",
        "tests/test_api_report_jobs.py",
        "tests/test_api_review_routes.py",
        "tests/test_api_review_rubric_routes.py",
        "tests/test_api_scan_grading_workspace.py",
        "tests/test_api_session_drafts.py",
        "tests/test_api_student_routes.py",
        "tests/test_api_taxonomy_suggestions.py",
        "tests/test_api_template_region_routes.py",
        "tests/test_api_training_routes.py",
        "tests/test_api_write_routes.py",
        "tests/test_assembly_assistant.py",
        "tests/test_assembly_export_job.py",
        "tests/test_atomic_major_retry.py",
        "tests/test_authoring_practice.py",
        "tests/test_backend_intake_followups.py",
        "tests/test_class_analysis.py",
        "tests/test_config_duplicate_decisions.py",
        "tests/test_config_generation_job.py",
        "tests/test_config_generation_targeted.py",
        "tests/test_config_source_duplicates.py",
        "tests/test_current_knowledge_resolver.py",
        "tests/test_dual_track_ai_scores.py",
        "tests/test_error_patterns.py",
        "tests/test_flexible_grading_acceptance.py",
        "tests/test_grading_completeness_db_regressions.py",
        "tests/test_grading_pause_resume.py",
        "tests/test_job_manager.py",
        "tests/test_manual_review_atomic.py",
        "tests/test_objective_batch_recognition_service.py",
        "tests/test_question_bank_importer.py",
        "tests/test_question_bank_paper_trash.py",
        "tests/test_question_bank_read_cache.py",
        "tests/test_question_document_pipeline.py",
        "tests/test_question_frequency_service.py",
        "tests/test_question_import_duplicates.py",
        "tests/test_question_import_job.py",
        "tests/test_report_ai_teacher_comparison.py",
        "tests/test_report_export_job.py",
        "tests/test_report_print_layout.py",
        "tests/test_report_score_adjustment.py",
        "tests/test_request_read_connections.py",
        "tests/test_retag_contract.py",
        "tests/test_review_media_service.py",
        "tests/test_review_results_question_id_compatibility.py",
        "tests/test_scan_analysis_job.py",
        "tests/test_scan_grading_workspace_service.py",
        "tests/test_secondary_error_persistence.py",
        "tests/test_session_cleanup.py",
        "tests/test_session_question_bank_sync_job.py",
        "tests/test_smoke_check.py",
        "tests/test_student_roster_service.py",
        "tests/test_template_upload_service.py",
    )
)
