from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final


START: Final = "<!-- HANDOFF_STATUS_START -->"
END: Final = "<!-- HANDOFF_STATUS_END -->"
FULL_SHA: Final = re.compile(r"^[0-9a-f]{40}$")
PACKAGE_ID: Final = re.compile(r"^P[1-9][0-9]*-[0-9]{2}$")
PLAN_PACKAGE_FIELD: Final = re.compile(
    r"^\*\*执行包：\*\*\s*`?([^`\s]+)`?\s*$",
    flags=re.MULTILINE,
)
CHECKPOINT_ROOT: Final = "docs/user-testing/checkpoints/"
FILENAME_PACKAGE_ID: Final = re.compile(
    r"(?<![a-z0-9])p[1-9][0-9]*-[0-9]{2}(?![a-z0-9])"
)
ACCEPTANCE_START: Final = "<!-- USER_ACCEPTANCE_RESULT_START -->"
ACCEPTANCE_END: Final = "<!-- USER_ACCEPTANCE_RESULT_END -->"

FIELDS: Final = {
    "执行包": "package_id",
    "交接状态": "handoff_status",
    "功能提交": "implementation_commit",
    "自动验证": "automated_validation",
    "独立复审": "independent_review",
    "用户验收": "user_acceptance",
    "真实数据指纹": "real_data_fingerprint",
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


def _extract_block(text: str) -> str:
    if text.count(START) != 1 or text.count(END) != 1:
        raise HandoffStatusError("expected exactly one handoff block")
    before, rest = text.split(START, 1)
    block, after = rest.split(END, 1)
    if START in before or END in before or START in after or END in after:
        raise HandoffStatusError("expected exactly one handoff block")
    return block


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


def _plan_package_id(text: str) -> str:
    start = text.index(START)
    end = text.index(END, start) + len(END)
    outside_block = text[:start] + text[end:]
    matches = PLAN_PACKAGE_FIELD.findall(outside_block)
    if len(matches) != 1:
        raise HandoffStatusError("expected exactly one plan package field outside handoff block")
    package_id = matches[0]
    if not PACKAGE_ID.fullmatch(package_id):
        raise HandoffStatusError("invalid plan package_id")
    return package_id


def _filename_package_ids(plan: Path) -> tuple[str, ...]:
    return tuple(FILENAME_PACKAGE_ID.findall(plan.stem.casefold()))


def _parse_acceptance_result(text: str) -> UserAcceptanceResult:
    if text.count(ACCEPTANCE_START) != 1 or text.count(ACCEPTANCE_END) != 1:
        raise HandoffStatusError("expected exactly one user acceptance result block")
    block = text.split(ACCEPTANCE_START, 1)[1].split(ACCEPTANCE_END, 1)[0]
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


def _matching_checkpoint(path: str, package_id: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized.casefold().startswith(CHECKPOINT_ROOT.casefold()):
        return False
    name = Path(normalized).name.casefold()
    return name.startswith(f"{package_id.casefold()}-") and name.endswith(".md")


def _reviewed_waiting_user_record(repo_root: Path, plan_relative: str) -> HandoffRecord:
    prior_text = _git(repo_root, "show", f"HEAD^:{plan_relative}")
    return parse_handoff_status(prior_text)


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

    try:
        declared_package = _plan_package_id(plan_text)
        if declared_package != record.package_id:
            issues.append("plan package must match handoff package")
    except (ValueError, HandoffStatusError) as exc:
        issues.append(str(exc))
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
