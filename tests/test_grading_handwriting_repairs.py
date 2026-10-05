"""Synthetic regressions for final-answer recognition and partial retry evidence."""

from __future__ import annotations

import pytest

from agent_bridge.respond_subj import _detail
from backend.scan_grading.grading_completeness import (
    is_objective_detail,
    merge_detail_metadata,
    details_require_review,
)
from backend.review.service import (
    _is_substantive_review_reason,
    _detail_metadata_for_qid,
)


def test_subjective_bridge_requires_real_evidence_and_confidence():
    spec = {"steps": {"S1": [3, "full", "符合关系"]}, "conf": 95}
    with pytest.raises(ValueError, match="evidence"):
        _detail("Q1", spec)
    spec["steps"]["S1"].append("3²+4²=5²")
    assert _detail("Q1", spec)["step_assessments"][0]["student_evidence"] == "3²+4²=5²"
    del spec["conf"]
    with pytest.raises(ValueError, match="confidence"):
        _detail("Q1", spec)
