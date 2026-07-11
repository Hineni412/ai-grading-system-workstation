from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
