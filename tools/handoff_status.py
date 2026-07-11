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
        and record.implementation_commit in {"none", "branch_head"}
        and record.automated_validation in {"pending", "passed"}
        and record.independent_review in {"pending", "passed"}
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


def validate_handoff(plan_path: Path, repo_root: Path) -> HandoffValidation:
    repo = repo_root.resolve()
    plan = plan_path.resolve()
    issues: list[str] = []
    try:
        plan_relative = plan.relative_to(repo).as_posix()
        record = parse_handoff_status(plan.read_text(encoding="utf-8"))
    except (OSError, ValueError, HandoffStatusError) as exc:
        return HandoffValidation(False, None, None, (str(exc),))

    resolved: str | None = None
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
        resolved = head if record.implementation_commit == "branch_head" else None

        if record.implementation_commit == "branch_head" and dirty:
            issues.append("committed handoff worktree must be clean")
        if record.handoff_status == "verified_pending_integration":
            parent = _git(repo, "rev-parse", "HEAD^")
            changed = tuple(
                line
                for line in _git(
                    repo,
                    "diff-tree",
                    "--no-commit-id",
                    "--name-only",
                    "-r",
                    "HEAD",
                ).splitlines()
                if line
            )
            resolved = record.implementation_commit
            if record.implementation_commit != parent:
                issues.append("implementation commit must be the direct parent of HEAD")
            if changed != (plan_relative,):
                issues.append("handoff commit must only change the plan")
            if dirty:
                issues.append("verified handoff worktree must be clean")
    except (OSError, subprocess.CalledProcessError) as exc:
        issues.append(f"git validation failed: {exc}")
        dirty_paths = ()
        user_data_paths = ()

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
