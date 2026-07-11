from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.handoff_status import (
    HandoffStatusError,
    parse_handoff_status,
    validate_handoff,
)


FULL_SHA = "0123456789abcdef0123456789abcdef01234567"


def _block(
    *,
    package_id: str = "P1-16",
    handoff_status: str = "verified_pending_integration",
    implementation_commit: str = FULL_SHA,
    automated_validation: str = "passed",
    independent_review: str = "passed",
    user_acceptance: str = "not_required",
    real_data_fingerprint: str = "unchanged",
    nightly_action: str = "independent_candidate_allowed",
) -> str:
    return f"""\
<!-- HANDOFF_STATUS_START -->
## 昼夜交接

**执行包：** {package_id}
**交接状态：** {handoff_status}
**功能提交：** {implementation_commit}
**自动验证：** {automated_validation}
**独立复审：** {independent_review}
**用户验收：** {user_acceptance}
**真实数据指纹：** {real_data_fingerprint}
**夜间动作：** {nightly_action}
<!-- HANDOFF_STATUS_END -->
"""


VALID = _block()


def test_parse_valid_verified_handoff() -> None:
    record = parse_handoff_status(VALID)

    assert record.package_id == "P1-16"
    assert record.handoff_status == "verified_pending_integration"
    assert record.implementation_commit == FULL_SHA
    assert record.to_dict()["nightly_action"] == "independent_candidate_allowed"


@pytest.mark.parametrize(
    "text",
    [
        _block(
            handoff_status="in_progress",
            implementation_commit="none",
            automated_validation="pending",
            independent_review="pending",
            user_acceptance="not_required",
            real_data_fingerprint="changed",
            nightly_action="report_only",
        ),
        _block(
            handoff_status="resumable",
            implementation_commit="none",
            automated_validation="failed",
            independent_review="pending",
            nightly_action="resume_only",
        ),
        _block(
            handoff_status="waiting_review",
            implementation_commit="branch_head",
            automated_validation="passed",
            independent_review="pending",
            user_acceptance="pending",
            nightly_action="report_only",
        ),
        _block(
            handoff_status="waiting_user",
            implementation_commit="branch_head",
            automated_validation="passed",
            independent_review="passed",
            user_acceptance="pending",
            nightly_action="report_only",
        ),
    ],
)
def test_parse_accepts_each_nonfinal_state_combination(text: str) -> None:
    assert parse_handoff_status(text).package_id == "P1-16"


def test_parse_accepts_backticked_values() -> None:
    text = VALID.replace("P1-16", "`P1-16`").replace(
        "verified_pending_integration",
        "`verified_pending_integration`",
    )

    record = parse_handoff_status(text)

    assert record.package_id == "P1-16"
    assert record.handoff_status == "verified_pending_integration"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (VALID + VALID, "exactly one handoff block"),
        (
            VALID.replace("**自动验证：** passed\n", ""),
            "missing field: automated_validation",
        ),
        (
            VALID.replace(
                "**自动验证：** passed\n",
                "**自动验证：** passed\n**自动验证：** passed\n",
            ),
            "duplicate field: automated_validation",
        ),
        (
            VALID.replace("## 昼夜交接", "## 昼夜交接\n\n**额外字段：** yes"),
            "unknown field: 额外字段",
        ),
        (VALID.replace("P1-16", "package-one"), "invalid package_id"),
        (VALID.replace(FULL_SHA, "not-a-full-sha"), "invalid implementation_commit"),
        (VALID.replace("unchanged", "unknown"), "invalid real_data_fingerprint"),
        (
            VALID.replace("independent_candidate_allowed", "resume_only"),
            "invalid field combination",
        ),
    ],
)
def test_invalid_handoff_is_rejected(text: str, message: str) -> None:
    with pytest.raises(HandoffStatusError, match=message):
        parse_handoff_status(text)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.strip()


def _initialize_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.name", "Handoff Test")
    _git(repo, "config", "user.email", "handoff@example.invalid")
    _git(repo, "config", "core.autocrlf", "false")


def _commit_all(repo: Path, message: str) -> str:
    _git(repo, "add", "--all")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _plan_document(block: str, *, package_id: str = "P1-16") -> str:
    return f"**执行包：** {package_id}\n\n{block}"


def _record_origin_main(repo: Path) -> str:
    base_sha = _commit_all(repo, "test: base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base_sha)
    return base_sha


def _make_verified_repo(
    tmp_path: Path,
    *,
    recorded_sha: str | None = None,
    extra_handoff_file: bool = False,
    committed_user_data_change: str | None = None,
    final_user_acceptance: str = "not_required",
    metadata_package_id: str = "P1-16",
    filename_package_id: str = "p1-16",
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    _initialize_repo(repo)
    (repo / ".gitignore").write_text("user_data/\n", encoding="utf-8")
    (repo / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    historical = repo / "user_data" / "historical.db"
    historical.parent.mkdir()
    historical.write_text("historical\n", encoding="utf-8")
    _git(repo, "add", "--all")
    _git(repo, "add", "--force", "user_data/historical.db")
    _record_origin_main(repo)

    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    if committed_user_data_change == "modified":
        historical.write_text("package change\n", encoding="utf-8")
    elif committed_user_data_change == "added":
        added = repo / "user_data" / "added.db"
        added.write_text("package data\n", encoding="utf-8")
        _git(repo, "add", "--force", "user_data/added.db")
    elif committed_user_data_change == "deleted":
        historical.unlink()
    elif committed_user_data_change == "reverted":
        historical.write_text("temporarily committed package data\n", encoding="utf-8")
        _commit_all(repo, "test: accidental user data commit")
        historical.write_text("historical\n", encoding="utf-8")
    plan = repo / "docs" / f"2026-07-11-{filename_package_id}-current-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        _plan_document(
            _block(
                handoff_status="waiting_review",
                implementation_commit="branch_head",
                automated_validation="passed",
                independent_review="pending",
                nightly_action="report_only",
            ),
            package_id=metadata_package_id,
        ),
        encoding="utf-8",
    )
    implementation_sha = _commit_all(repo, "feat: implementation")

    plan.write_text(
        _plan_document(
            _block(
                implementation_commit=recorded_sha or implementation_sha,
                user_acceptance=final_user_acceptance,
            ),
            package_id=metadata_package_id,
        ),
        encoding="utf-8",
    )
    if extra_handoff_file:
        (repo / "extra.txt").write_text("not allowed\n", encoding="utf-8")
    _commit_all(repo, "docs: record handoff")
    return repo, plan, implementation_sha


def _make_branch_head_repo(tmp_path: Path) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    _initialize_repo(repo)
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    _record_origin_main(repo)
    plan = repo / "docs" / "2026-07-11-p1-16-current-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        _plan_document(
            _block(
                handoff_status="waiting_review",
                implementation_commit="branch_head",
                automated_validation="passed",
                independent_review="pending",
                nightly_action="report_only",
            )
        ),
        encoding="utf-8",
    )
    head = _commit_all(repo, "feat: implementation")
    return repo, plan, head


def test_verified_handoff_requires_plan_only_commit(tmp_path: Path) -> None:
    repo, plan, implementation_sha = _make_verified_repo(tmp_path)

    report = validate_handoff(plan, repo)

    assert report.ok is True
    assert report.resolved_implementation_commit == implementation_sha
    assert report.issues == ()


def test_verified_handoff_rejects_wrong_parent_sha(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path, recorded_sha="0" * 40)

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "implementation commit must be the direct parent of HEAD" in report.issues


def test_verified_handoff_rejects_extra_file_in_handoff_commit(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path, extra_handoff_file=True)

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "handoff commit must only change the plan" in report.issues


def test_verified_handoff_rejects_dirty_worktree(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "verified handoff worktree must be clean" in report.issues


def test_branch_head_resolves_clean_head(tmp_path: Path) -> None:
    repo, plan, head = _make_branch_head_repo(tmp_path)

    report = validate_handoff(plan, repo)

    assert report.ok is True
    assert report.resolved_implementation_commit == head


def test_branch_head_rejects_dirty_worktree(tmp_path: Path) -> None:
    repo, plan, _ = _make_branch_head_repo(tmp_path)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "committed handoff worktree must be clean" in report.issues


@pytest.mark.parametrize("change", ["added", "modified", "deleted", "reverted"])
def test_committed_user_data_delta_from_origin_main_fails_validation(
    tmp_path: Path,
    change: str,
) -> None:
    repo, plan, _ = _make_verified_repo(
        tmp_path,
        committed_user_data_change=change,
    )

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "committed user_data changes are not allowed" in report.issues


def test_unchanged_historical_user_data_does_not_block_validation(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path)

    report = validate_handoff(plan, repo)

    assert report.ok is True


def test_plan_package_identity_must_match_metadata_and_filename(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path, metadata_package_id="P1-17")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "plan package must match handoff package" in report.issues


def test_plan_filename_must_match_handoff_package(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path, filename_package_id="p1-17")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "plan filename must match handoff package" in report.issues


def _make_user_accepted_repo(
    tmp_path: Path,
    *,
    extra_source_change: bool = False,
    independent_review: str = "passed",
    checkpoint_package_id: str = "P1-16",
) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    _initialize_repo(repo)
    (repo / "app.py").write_text("VALUE = 0\n", encoding="utf-8")
    _record_origin_main(repo)

    plan = repo / "docs" / "2026-07-11-p1-16-current-plan.md"
    plan.parent.mkdir(parents=True)
    plan.write_text(
        _plan_document(
            _block(
                handoff_status="waiting_user",
                implementation_commit="branch_head",
                automated_validation="passed",
                independent_review=independent_review,
                user_acceptance="pending",
                nightly_action="report_only",
            )
        ),
        encoding="utf-8",
    )
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _commit_all(repo, "feat: reviewed implementation")

    checklist = (
        repo
        / "docs"
        / "user-testing"
        / "checkpoints"
        / f"{checkpoint_package_id}-2026-07-11.md"
    )
    checklist.parent.mkdir(parents=True)
    checklist.write_text("# P1-16\n\nResult: passed\n", encoding="utf-8")
    if extra_source_change:
        (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    evidence_sha = _commit_all(repo, "docs: record user acceptance")

    plan.write_text(
        _plan_document(
            _block(
                implementation_commit=evidence_sha,
                user_acceptance="passed",
            )
        ),
        encoding="utf-8",
    )
    _commit_all(repo, "docs: record handoff")
    return repo, plan


def test_passed_user_acceptance_allows_matching_checklist_only_evidence(
    tmp_path: Path,
) -> None:
    repo, plan = _make_user_accepted_repo(tmp_path)

    report = validate_handoff(plan, repo)

    assert report.ok is True


def test_user_acceptance_evidence_commit_rejects_source_changes(tmp_path: Path) -> None:
    repo, plan = _make_user_accepted_repo(tmp_path, extra_source_change=True)

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "user acceptance evidence must only change one matching checklist" in report.issues


def test_user_acceptance_evidence_requires_matching_package_checklist(
    tmp_path: Path,
) -> None:
    repo, plan = _make_user_accepted_repo(
        tmp_path,
        checkpoint_package_id="P1-17",
    )

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "user acceptance evidence must only change one matching checklist" in report.issues


def test_user_acceptance_evidence_requires_prior_review_passed(tmp_path: Path) -> None:
    repo, plan = _make_user_accepted_repo(
        tmp_path,
        independent_review="pending",
    )

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "user acceptance evidence must follow reviewed waiting_user state" in report.issues


def test_passed_user_acceptance_requires_checklist_evidence_commit(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(
        tmp_path,
        final_user_acceptance="passed",
    )

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "passed user acceptance requires a checklist evidence commit" in report.issues


def test_changed_real_data_fingerprint_fails_validation(tmp_path: Path) -> None:
    repo, plan, _ = _make_branch_head_repo(tmp_path)
    plan.write_text(
        _block(
            handoff_status="in_progress",
            implementation_commit="none",
            automated_validation="pending",
            independent_review="pending",
            real_data_fingerprint="changed",
            nightly_action="report_only",
        ),
        encoding="utf-8",
    )

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "real data fingerprint changed" in report.issues


def test_tracked_user_data_change_fails_validation(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path)
    user_file = repo / "user_data" / "historical.db"
    user_file.write_text("changed\n", encoding="utf-8")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "user_data changes are not allowed" in report.issues


def test_ignored_user_data_file_fails_validation(tmp_path: Path) -> None:
    repo, plan, _ = _make_verified_repo(tmp_path)
    user_file = repo / "user_data" / "ignored.db"
    user_file.parent.mkdir(exist_ok=True)
    user_file.write_text("local data\n", encoding="utf-8")

    report = validate_handoff(plan, repo)

    assert report.ok is False
    assert "user_data changes are not allowed" in report.issues


def test_plan_outside_repository_fails_closed(tmp_path: Path) -> None:
    repo, _plan, _ = _make_verified_repo(tmp_path)
    outside = tmp_path / "outside-plan.md"
    outside.write_text(VALID, encoding="utf-8")

    report = validate_handoff(outside, repo)

    assert report.ok is False
    assert report.record is None


def test_cli_emits_one_json_line_and_success_exit(tmp_path: Path) -> None:
    repo, plan, implementation_sha = _make_verified_repo(tmp_path)
    script = Path(__file__).resolve().parents[1] / "tools" / "handoff_status.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--plan", str(plan), "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True
    assert payload["resolved_implementation_commit"] == implementation_sha


def test_cli_fails_closed_with_json_for_missing_block(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _initialize_repo(repo)
    plan = repo / "plan.md"
    plan.write_text("# Historical plan\n", encoding="utf-8")
    _commit_all(repo, "docs: historical plan")
    script = Path(__file__).resolve().parents[1] / "tools" / "handoff_status.py"

    completed = subprocess.run(
        [sys.executable, str(script), "--plan", str(plan), "--repo", str(repo)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert completed.returncode == 2
    payload = json.loads(completed.stdout)
    assert payload["ok"] is False
    assert payload["record"] is None
    assert "handoff block" in " ".join(payload["issues"])
