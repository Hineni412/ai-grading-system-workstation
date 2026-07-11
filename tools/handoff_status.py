from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Final


START: Final = "<!-- HANDOFF_STATUS_START -->"
END: Final = "<!-- HANDOFF_STATUS_END -->"
FULL_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_ID: Final = re.compile(r"^P[1-9][0-9]*-[0-9]{2}$")
PLAN_PACKAGE_FIELD: Final = re.compile(
    r"^\*\*执行包：\*\*\s*`?([^`\s]+)`?\s*$",
    flags=re.MULTILINE,
)
PLAN_USER_TEST_FIELD: Final = re.compile(
    r"^\*\*用户自测：\*\*\s*`?([^`\s]+)`?\s*$",
    flags=re.MULTILINE,
)
PLAN_CHECKLIST_FIELD: Final = re.compile(
    r"^\*\*自测清单：\*\*\s*`?([^`\s]+)`?\s*$",
    flags=re.MULTILINE,
)
CHECKPOINT_ROOT: Final = "docs/user-testing/checkpoints/"
FILENAME_PACKAGE_ID: Final = re.compile(
    r"(?<![a-z0-9])p[1-9][0-9]*-[0-9]{2}(?![a-z0-9])"
)
ACCEPTANCE_START: Final = "<!-- USER_ACCEPTANCE_RESULT_START -->"
ACCEPTANCE_END: Final = "<!-- USER_ACCEPTANCE_RESULT_END -->"
REQUIRED_FORMAL_USER_TEST_PACKAGES: Final = frozenset(
    {"P1-29", "P2-08", "P2-20", "P2-21"}
)

FIELDS: Final = {
    "执行包": "package_id",
    "交接状态": "handoff_status",
    "功能提交": "implementation_commit",
    "自动验证": "automated_validation",
    "独立复审": "independent_review",
    "用户验收": "user_acceptance",
    "真实数据指纹": "real_data_fingerprint",
    "Stash 基线": "stash_baseline",
    "夜间动作": "nightly_action",
}

ALLOWED: Final = {
    "handoff_status": {
        "in_progress",
        "resumable",
        "waiting_review",
        "waiting_user",
        "verified_pending_integration",
    },
    "automated_validation": {"pending", "passed", "failed"},
    "independent_review": {"pending", "passed", "failed"},
    "user_acceptance": {"not_required", "pending", "passed", "failed"},
    "real_data_fingerprint": {"not_touched", "unchanged", "changed"},
    "nightly_action": {
        "report_only",
        "resume_only",
        "independent_candidate_allowed",
    },
}


class HandoffStatusError(ValueError):
    """Raised when a handoff block is missing, ambiguous, or inconsistent."""


@dataclass(frozen=True)
class HandoffRecord:
    package_id: str
    handoff_status: str
    implementation_commit: str
    automated_validation: str
    independent_review: str
    user_acceptance: str
    real_data_fingerprint: str
    stash_baseline: str
    nightly_action: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class HandoffValidation:
    ok: bool
    record: HandoffRecord | None
    resolved_implementation_commit: str | None
    issues: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "record": self.record.to_dict() if self.record else None,
            "resolved_implementation_commit": self.resolved_implementation_commit,
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class UserAcceptanceResult:
    package_id: str
    reviewed_commit: str
    result: str


@dataclass(frozen=True)
class PlanUserTesting:
    user_test: str
    checklist: str


def _extract_block(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise HandoffStatusError("expected exactly one handoff block")
    start = text.index(START)
    end = text.index(END)
    if start >= end:
        raise HandoffStatusError("handoff block markers are out of order")
    return text[start + len(START) : end]


def _read_fields(block: str) -> dict[str, str]:
    labels = re.findall(r"^\*\*([^*：]+)：\*\*", block, flags=re.MULTILINE)
    unknown = sorted(set(labels) - set(FIELDS))
    if unknown:
        raise HandoffStatusError(f"unknown field: {unknown[0]}")

    values: dict[str, str] = {}
    for label, field_name in FIELDS.items():
        matches = re.findall(
            rf"^\*\*{re.escape(label)}：\*\*\s*`?([^`\s]+)`?\s*$",
            block,
            flags=re.MULTILINE,
        )
        if not matches:
            raise HandoffStatusError(f"missing field: {field_name}")
        if len(matches) > 1:
            raise HandoffStatusError(f"duplicate field: {field_name}")
        values[field_name] = matches[0]
    return values


def _validate_record(record: HandoffRecord) -> None:
    if not PACKAGE_ID.fullmatch(record.package_id):
        raise HandoffStatusError("invalid package_id")
    for field_name, choices in ALLOWED.items():
        if getattr(record, field_name) not in choices:
            raise HandoffStatusError(f"invalid {field_name}")
    if record.implementation_commit not in {"none", "branch_head"} and not FULL_SHA.fullmatch(
        record.implementation_commit
    ):
        raise HandoffStatusError("invalid implementation_commit")
    _stash_baseline_commits(record.stash_baseline)

    status = record.handoff_status
    valid = (
        status == "in_progress"
        and record.implementation_commit == "none"
        and record.automated_validation == "pending"
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "report_only"
    ) or (
        status == "resumable"
        and record.implementation_commit == "none"
        and record.automated_validation in {"pending", "failed"}
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "resume_only"
    ) or (
        status == "waiting_review"
        and record.implementation_commit == "branch_head"
        and record.automated_validation == "passed"
        and record.independent_review == "pending"
        and record.user_acceptance in {"pending", "not_required"}
        and record.nightly_action == "report_only"
    ) or (
        status == "waiting_user"
        and (
            (
                record.implementation_commit in {"none", "branch_head"}
                and record.automated_validation in {"pending", "passed"}
                and record.independent_review == "pending"
            )
            or (
                bool(FULL_SHA.fullmatch(record.implementation_commit))
                and record.automated_validation == "passed"
                and record.independent_review == "passed"
            )
        )
        and record.user_acceptance == "pending"
        and record.nightly_action == "report_only"
    ) or (
        status == "verified_pending_integration"
        and bool(FULL_SHA.fullmatch(record.implementation_commit))
        and record.automated_validation == "passed"
        and record.independent_review == "passed"
        and record.user_acceptance in {"passed", "not_required"}
        and record.real_data_fingerprint in {"not_touched", "unchanged"}
        and record.nightly_action == "independent_candidate_allowed"
    )
    if not valid:
        raise HandoffStatusError("invalid field combination")


def parse_handoff_status(text: str) -> HandoffRecord:
    values = _read_fields(_extract_block(text))
    record = HandoffRecord(**values)
    _validate_record(record)
    return record


def _outside_handoff_block(text: str) -> str:
    _extract_block(text)
    start = text.index(START)
    end = text.index(END, start) + len(END)
    return text[:start] + text[end:]


def _plan_package_id(text: str) -> str:
    outside_block = _outside_handoff_block(text)
    matches = PLAN_PACKAGE_FIELD.findall(outside_block)
    if len(matches) != 1:
        raise HandoffStatusError("expected exactly one plan package field outside handoff block")
    package_id = matches[0]
    if not PACKAGE_ID.fullmatch(package_id):
        raise HandoffStatusError("invalid plan package_id")
    return package_id


def _plan_user_testing(text: str, package_id: str) -> PlanUserTesting:
    outside_block = _outside_handoff_block(text)
    user_test_matches = PLAN_USER_TEST_FIELD.findall(outside_block)
    if len(user_test_matches) != 1:
        raise HandoffStatusError("expected exactly one plan user_test field")
    checklist_matches = PLAN_CHECKLIST_FIELD.findall(outside_block)
    if len(checklist_matches) != 1:
        raise HandoffStatusError("expected exactly one plan checklist field")

    user_test = user_test_matches[0]
    checklist = checklist_matches[0]
    if user_test not in {"none", "quick", "formal"}:
        raise HandoffStatusError("invalid plan user_test")
    if package_id in REQUIRED_FORMAL_USER_TEST_PACKAGES and user_test != "formal":
        raise HandoffStatusError(f"{package_id} requires formal user testing")
    if user_test == "none" and checklist != "not_required":
        raise HandoffStatusError("plan without user testing must use not_required checklist")
    if checklist != "not_required":
        normalized = PurePosixPath(checklist)
        if (
            normalized.is_absolute()
            or checklist != normalized.as_posix()
            or ".." in normalized.parts
            or not _matching_checkpoint(checklist, package_id)
        ):
            raise HandoffStatusError("invalid plan checklist")
    return PlanUserTesting(user_test=user_test, checklist=checklist)


def _plan_user_acceptance_issues(
    record: HandoffRecord,
    plan_user_testing: PlanUserTesting,
) -> tuple[str, ...]:
    if plan_user_testing.user_test == "none":
        if record.user_acceptance != "not_required":
            return ("plan declares no user testing",)
        return ()
    if record.user_acceptance == "not_required":
        return ("plan user testing requires passed user acceptance",)
    if (
        record.handoff_status == "verified_pending_integration"
        and record.user_acceptance != "passed"
    ):
        return ("plan user testing requires passed user acceptance",)
    if (
        record.user_acceptance == "passed"
        and plan_user_testing.checklist == "not_required"
    ):
        return ("passed user acceptance requires a plan checklist",)
    return ()


def _filename_package_ids(plan: Path) -> tuple[str, ...]:
    return tuple(FILENAME_PACKAGE_ID.findall(plan.stem.casefold()))


def _stash_baseline_commits(value: str) -> frozenset[str]:
    if value == "none":
        return frozenset()
    commits = value.split(",")
    if (
        not commits
        or len(commits) != len(set(commits))
        or any(not FULL_SHA.fullmatch(commit) for commit in commits)
    ):
        raise HandoffStatusError("invalid stash_baseline")
    return frozenset(commits)


def _parse_acceptance_result(text: str) -> UserAcceptanceResult:
    if text.count(ACCEPTANCE_START) != 1 or text.count(ACCEPTANCE_END) != 1:
        raise HandoffStatusError("expected exactly one user acceptance result block")
    start = text.index(ACCEPTANCE_START)
    end = text.index(ACCEPTANCE_END)
    if start >= end:
        raise HandoffStatusError("user acceptance result markers are out of order")
    block = text[start + len(ACCEPTANCE_START) : end]
    patterns = {
        "package_id": r"^\*\*执行包：\*\*\s*`?([^`\s]+)`?\s*$",
        "reviewed_commit": r"^\*\*验收提交：\*\*\s*`?([^`\s]+)`?\s*$",
        "result": r"^\*\*结果：\*\*\s*`?([^`\s]+)`?\s*$",
    }
    values: dict[str, str] = {}
    for field_name, pattern in patterns.items():
        matches = re.findall(pattern, block, flags=re.MULTILINE)
        if len(matches) != 1:
            raise HandoffStatusError(f"invalid user acceptance field: {field_name}")
        values[field_name] = matches[0]
    result = UserAcceptanceResult(**values)
    if not PACKAGE_ID.fullmatch(result.package_id):
        raise HandoffStatusError("invalid user acceptance package_id")
    if not FULL_SHA.fullmatch(result.reviewed_commit):
        raise HandoffStatusError("invalid user acceptance reviewed_commit")
    if result.result not in {"pending", "passed", "failed"}:
        raise HandoffStatusError("invalid user acceptance result")
    return result


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout.rstrip("\r\n")


def _porcelain_paths(output: str) -> tuple[str, ...]:
    paths: list[str] = []
    expects_rename_source = False
    for token in (item for item in output.split("\0") if item):
        if expects_rename_source:
            paths.append(token.replace("\\", "/"))
            expects_rename_source = False
            continue
        if len(token) < 4 or token[2] != " ":
            paths.append(token.replace("\\", "/"))
            continue
        status = token[:2]
        paths.append(token[3:].replace("\\", "/"))
        expects_rename_source = any(code in status for code in ("R", "C"))
    return tuple(paths)


def _nul_paths(output: str) -> tuple[str, ...]:
    return tuple(
        token.replace("\\", "/")
        for token in output.split("\0")
        if token
    )


def _commit_paths(repo_root: Path, revision: str) -> tuple[str, ...]:
    return _nul_paths(
        _git(
            repo_root,
            "diff-tree",
            "--no-commit-id",
            "--name-only",
            "-r",
            "-z",
            revision,
        )
    )


def _stash_user_data_paths(repo_root: Path, stash_commit: str) -> tuple[str, ...]:
    paths = list(
        _nul_paths(
            _git(
                repo_root,
                "diff",
                "--name-only",
                "-z",
                f"{stash_commit}^1",
                stash_commit,
                "--",
                "user_data",
            )
        )
    )
    try:
        untracked_tree = _git(repo_root, "rev-parse", "--verify", f"{stash_commit}^3")
    except subprocess.CalledProcessError:
        pass
    else:
        paths.extend(
            _nul_paths(
                _git(
                    repo_root,
                    "ls-tree",
                    "-r",
                    "--name-only",
                    "-z",
                    untracked_tree,
                    "--",
                    "user_data",
                )
            )
        )
    return tuple(paths)


def _matching_checkpoint(path: str, package_id: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized.casefold().startswith(CHECKPOINT_ROOT.casefold()):
        return False
    name = Path(normalized).name.casefold()
    return name.startswith(f"{package_id.casefold()}-") and name.endswith(".md")


def _reviewed_waiting_user_record(repo_root: Path, plan_relative: str) -> HandoffRecord:
    prior_text = _git(repo_root, "show", f"HEAD^:{plan_relative}")
    return parse_handoff_status(prior_text)


def _claim_stash_baseline(
    repo_root: Path,
    plan_relative: str,
    base_ref: str,
    current_record: HandoffRecord,
    current_user_testing: PlanUserTesting | None,
) -> tuple[frozenset[str], tuple[str, ...]]:
    issues: list[str] = []
    branch_base = _git(repo_root, "merge-base", base_ref, "HEAD")
    commits = tuple(
        line
        for line in _git(
            repo_root,
            "log",
            "--first-parent",
            "--reverse",
            "--format=%H",
            f"{branch_base}..HEAD",
            "--",
            plan_relative,
        ).splitlines()
        if line
    )
    if not commits:
        return frozenset(), ("missing handoff claim commit",)

    claim_commit = commits[0]
    branch_commits = tuple(
        line
        for line in _git(
            repo_root,
            "rev-list",
            "--first-parent",
            "--reverse",
            f"{branch_base}..HEAD",
        ).splitlines()
        if line
    )
    if not branch_commits or claim_commit != branch_commits[0]:
        issues.append("handoff claim must be the first branch commit")
    claim_text = _git(repo_root, "show", f"{claim_commit}:{plan_relative}")
    try:
        claim_record = parse_handoff_status(claim_text)
        claim_package = _plan_package_id(claim_text)
        claim_user_testing = _plan_user_testing(claim_text, claim_record.package_id)
    except (ValueError, HandoffStatusError) as exc:
        return frozenset(), (f"invalid handoff claim: {exc}",)

    if claim_record.handoff_status != "in_progress":
        issues.append("handoff claim must start in_progress")
    if claim_record.package_id != current_record.package_id or claim_package != current_record.package_id:
        issues.append("handoff claim package must match current package")
    if _commit_paths(repo_root, claim_commit) != (plan_relative,):
        issues.append("handoff claim commit must only change the plan")
    if (
        current_user_testing is not None
        and claim_user_testing.user_test != current_user_testing.user_test
    ):
        issues.append("plan user_test must remain unchanged from claim")

    claim_baseline = _stash_baseline_commits(claim_record.stash_baseline)
    if _stash_baseline_commits(current_record.stash_baseline) != claim_baseline:
        issues.append("stash_baseline must remain unchanged from claim")

    for commit in commits[1:]:
        history_text = _git(repo_root, "show", f"{commit}:{plan_relative}")
        try:
            history_record = parse_handoff_status(history_text)
            history_package = _plan_package_id(history_text)
            history_user_testing = _plan_user_testing(
                history_text,
                history_record.package_id,
            )
        except (ValueError, HandoffStatusError) as exc:
            issues.append(f"invalid handoff history at {commit[:12]}: {exc}")
            continue
        if (
            history_record.package_id != current_record.package_id
            or history_package != current_record.package_id
        ):
            issues.append("handoff history package must remain unchanged")
        if _stash_baseline_commits(history_record.stash_baseline) != claim_baseline:
            issues.append("stash_baseline must remain unchanged from claim")
        if history_user_testing.user_test != claim_user_testing.user_test:
            issues.append("plan user_test must remain unchanged from claim")

    return claim_baseline, tuple(dict.fromkeys(issues))


def validate_handoff(
    plan_path: Path,
    repo_root: Path,
    *,
    base_ref: str = "origin/main",
) -> HandoffValidation:
    repo = repo_root.resolve()
    plan = plan_path.resolve()
    issues: list[str] = []
    try:
        plan_relative = plan.relative_to(repo).as_posix()
        plan_text = plan.read_text(encoding="utf-8")
        record = parse_handoff_status(plan_text)
    except (OSError, ValueError, HandoffStatusError) as exc:
        return HandoffValidation(False, None, None, (str(exc),))

    plan_user_testing: PlanUserTesting | None = None
    try:
        declared_package = _plan_package_id(plan_text)
        if declared_package != record.package_id:
            issues.append("plan package must match handoff package")
    except (ValueError, HandoffStatusError) as exc:
        issues.append(str(exc))
    try:
        plan_user_testing = _plan_user_testing(plan_text, record.package_id)
    except (ValueError, HandoffStatusError) as exc:
        issues.append(str(exc))
    else:
        issues.extend(_plan_user_acceptance_issues(record, plan_user_testing))
    filename_packages = _filename_package_ids(plan)
    expected_filename_package = record.package_id.casefold()
    if filename_packages != (expected_filename_package,):
        if expected_filename_package not in filename_packages:
            issues.append("plan filename must match handoff package")
        else:
            issues.append("plan filename must match exactly one handoff package")

    resolved: str | None = None
    dirty_paths: tuple[str, ...] = ()
    user_data_paths: tuple[str, ...] = ()
    try:
        head = _git(repo, "rev-parse", "HEAD")
        dirty = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all")
        dirty_paths = _porcelain_paths(dirty)
        user_data_status = _git(
            repo,
            "status",
            "--porcelain=v1",
            "-z",
            "--ignored=matching",
            "--untracked-files=all",
            "--",
            "user_data",
        )
        user_data_paths = _porcelain_paths(user_data_status)
        committed_user_data_paths = _nul_paths(
            _git(
                repo,
                "log",
                "--format=",
                "--name-only",
                "-z",
                f"{base_ref}..HEAD",
                "--",
                "user_data",
            )
        )
        if committed_user_data_paths:
            issues.append("committed user_data changes are not allowed")
        current_stashes = frozenset(
            line
            for line in _git(repo, "stash", "list", "--format=%H").splitlines()
            if line
        )
        claim_stash_baseline, claim_issues = _claim_stash_baseline(
            repo,
            plan_relative,
            base_ref,
            record,
            plan_user_testing,
        )
        issues.extend(claim_issues)
        if not claim_stash_baseline.issubset(current_stashes):
            issues.append("stash_baseline contains missing stash commits")
        new_stashes = current_stashes - claim_stash_baseline
        if any(_stash_user_data_paths(repo, stash) for stash in new_stashes):
            issues.append("new user_data stash is not allowed")
        resolved = (
            head
            if record.implementation_commit == "branch_head"
            else record.implementation_commit
            if FULL_SHA.fullmatch(record.implementation_commit)
            else None
        )

        if record.implementation_commit == "branch_head" and dirty:
            issues.append("committed handoff worktree must be clean")
        if (
            record.handoff_status == "waiting_user"
            and FULL_SHA.fullmatch(record.implementation_commit)
        ):
            waiting_user_parent = _git(repo, "rev-parse", "HEAD^")
            if record.implementation_commit != waiting_user_parent:
                issues.append("waiting_user must anchor the direct parent reviewed commit")
            if _commit_paths(repo, "HEAD") != (plan_relative,):
                issues.append("waiting_user anchor commit must only change the plan")
            if dirty:
                issues.append("anchored waiting_user worktree must be clean")
        if record.handoff_status == "verified_pending_integration":
            parent = _git(repo, "rev-parse", "HEAD^")
            changed = _commit_paths(repo, "HEAD")
            parent_changed = _commit_paths(repo, "HEAD^")
            resolved = record.implementation_commit
            if record.implementation_commit != parent:
                issues.append("implementation commit must be the direct parent of HEAD")
            if changed != (plan_relative,):
                issues.append("handoff commit must only change the plan")
            if dirty:
                issues.append("verified handoff worktree must be clean")

            matching_checkpoints = tuple(
                path
                for path in parent_changed
                if _matching_checkpoint(path, record.package_id)
            )
            checkpoint_paths = tuple(
                path
                for path in parent_changed
                if path.casefold().startswith(CHECKPOINT_ROOT.casefold())
            )
            if record.user_acceptance == "passed":
                if not (
                    len(parent_changed) == 1
                    and len(matching_checkpoints) == 1
                ):
                    if checkpoint_paths:
                        issues.append(
                            "user acceptance evidence must only change one matching checklist"
                        )
                    else:
                        issues.append(
                            "passed user acceptance requires a checklist evidence commit"
                        )
                else:
                    try:
                        prior = _reviewed_waiting_user_record(repo, plan_relative)
                    except (HandoffStatusError, subprocess.CalledProcessError) as exc:
                        issues.append(f"invalid pre-evidence handoff: {exc}")
                    else:
                        if not (
                            prior.package_id == record.package_id
                            and prior.handoff_status == "waiting_user"
                            and bool(FULL_SHA.fullmatch(prior.implementation_commit))
                            and prior.automated_validation == "passed"
                            and prior.independent_review == "passed"
                            and prior.user_acceptance == "pending"
                            and prior.real_data_fingerprint != "changed"
                        ):
                            issues.append(
                                "user acceptance evidence must follow reviewed waiting_user state"
                            )
                        else:
                            waiting_user_commit = _git(repo, "rev-parse", "HEAD^^")
                            waiting_user_parent = _git(repo, "rev-parse", "HEAD^^^")
                            if not (
                                prior.implementation_commit == waiting_user_parent
                                and _commit_paths(repo, waiting_user_commit)
                                == (plan_relative,)
                            ):
                                issues.append(
                                    "user acceptance evidence must directly follow anchored waiting_user state"
                                )
                            checklist_path = matching_checkpoints[0]
                            if (
                                plan_user_testing is not None
                                and checklist_path != plan_user_testing.checklist
                            ):
                                issues.append(
                                    "user acceptance evidence must match plan checklist"
                                )
                            try:
                                checklist_text = _git(
                                    repo,
                                    "show",
                                    f"HEAD^:{checklist_path}",
                                )
                                acceptance = _parse_acceptance_result(checklist_text)
                            except (HandoffStatusError, subprocess.CalledProcessError) as exc:
                                issues.append(f"invalid user acceptance checklist: {exc}")
                            else:
                                if acceptance.package_id != record.package_id:
                                    issues.append(
                                        "user acceptance checklist must match handoff package"
                                    )
                                if acceptance.reviewed_commit != prior.implementation_commit:
                                    issues.append(
                                        "user acceptance checklist must match reviewed commit"
                                    )
                                if acceptance.result != "passed":
                                    issues.append(
                                        "user acceptance checklist must record passed result"
                                    )
            elif checkpoint_paths:
                issues.append("checklist evidence requires passed user acceptance")
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(f"git validation failed: {exc}")

    if record.real_data_fingerprint == "changed":
        issues.append("real data fingerprint changed")
    if user_data_paths or any(
        path.casefold() == "user_data" or path.casefold().startswith("user_data/")
        for path in dirty_paths
    ):
        issues.append("user_data changes are not allowed")

    return HandoffValidation(not issues, record, resolved, tuple(issues))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a package handoff block and Git evidence."
    )
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--repo", required=True, type=Path)
    args = parser.parse_args()
    report = validate_handoff(args.plan, args.repo)
    print(json.dumps(report.to_dict(), ensure_ascii=False, sort_keys=True))
    return 0 if report.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
