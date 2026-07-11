from __future__ import annotations

import pytest

from tools.handoff_status import HandoffStatusError, parse_handoff_status


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
