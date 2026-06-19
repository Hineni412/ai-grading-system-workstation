# Grading Completeness and Shared Regions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unify answer equivalence across grading modes, deduplicate overlapping/cross-page evidence, detect incomplete grading locally, atomically rerun whole affected major questions, expose incomplete status in UI/exports, align configuration limits, and smooth every model request attempt.

**Architecture:** Add three pure, testable policy modules for answer forms, major-question evidence grouping, and grading completeness. Existing grading services consume those modules; database persistence gains an atomic major-question replacement path; Streamlit and Excel export read the same structured completeness payload. A strict slot-based limiter replaces the initial-burst token bucket and is applied to each retry attempt.

**Tech Stack:** Python 3, pytest, SQLite, Streamlit, pandas/openpyxl, Pillow.

## Global Constraints

- Preserve all unrelated tracked and untracked user changes in the dirty worktree.
- Do not migrate or rewrite legacy rubric/answer-key files.
- Keep question-stem and answer images optional.
- Objective batch size is 1-15; subjective batch size is 1-20; precheck workers are 1-32; full-paper workers are 1-200; hybrid in-flight workers are 1-1000; RPM is 1-10000.
- A retry for one missing sub-question reruns and atomically replaces the student's entire parent major question.
- Cross-page regions remain separate physical crops in one logical major-question request.

---

### Task 1: Unified accepted answer forms

**Files:**
- Create: `answer_key_utils.py`
- Create: `tests/test_answer_key_utils.py`
- Modify: `objective_batch_recognition_service.py:463-492, 966-976`
- Modify: `ai_grader.py:864-910`

**Interfaces:**
- Produces: `answer_forms_for_question(answer_key: dict, question_id: str) -> list[str]`
- Produces: `answer_forms_map(answer_key: dict) -> dict[str, list[str]]`
- Consumed by: full-paper objective post-validation and hybrid objective specs.

- [ ] **Step 1: Write failing answer-form tests**

```python
from answer_key_utils import answer_forms_for_question


def test_answer_forms_prefer_and_preserve_all_equivalent_forms():
    key = {"questions": [{
        "question_id": "Q9",
        "canonical_answer": "y=48x+20",
        "accepted_forms": ["y=48x+20", "y=20+48x", "48x+20=y", ""],
        "standard_answer": "legacy",
    }]}
    assert answer_forms_for_question(key, "Q9") == [
        "y=48x+20", "y=20+48x", "48x+20=y"
    ]


def test_answer_forms_fall_back_to_legacy_fields_and_parts():
    key = {"questions": [{
        "question_id": "Q10",
        "parts": [{"part_id": "Q10(1)", "answer": "4", "accepted_forms": ["4", "四"]}],
    }]}
    assert answer_forms_for_question(key, "Q10(1)") == ["4", "四"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_answer_key_utils.py -q`

Expected: collection fails because `answer_key_utils` does not exist.

- [ ] **Step 3: Implement the minimal shared resolver**

```python
from __future__ import annotations

from typing import Any


def _texts(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if item is not None and str(item).strip()]


def _node_forms(node: dict[str, Any]) -> list[str]:
    forms = _texts(node.get("accepted_forms")) + _texts(node.get("canonical_answer"))
    if not forms:
        for field in ("standard_answer", "correct_answer", "answer", "answers", "reference_answer"):
            forms.extend(_texts(node.get(field)))
            if forms:
                break
    return list(dict.fromkeys(forms))


def answer_forms_map(answer_key: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for question in answer_key.get("questions", []) if isinstance(answer_key, dict) else []:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        if qid:
            result[qid] = _node_forms(question)
        for part in question.get("parts", []) if isinstance(question.get("parts"), list) else []:
            if isinstance(part, dict):
                part_id = str(part.get("part_id") or part.get("question_id") or "").strip()
                if part_id:
                    result[part_id] = _node_forms(part)
    return result


def answer_forms_for_question(answer_key: dict[str, Any], question_id: str) -> list[str]:
    return answer_forms_map(answer_key).get(str(question_id).strip(), [])
```

- [ ] **Step 4: Replace both grading-mode answer lookups**

Use the shared list directly in `ObjectiveQuestionSpec.standard_answer`; only call `objective_answer_loader` when the list is empty. Make `_answer_key_forms_for_question` delegate to `answer_forms_for_question` so full and hybrid use identical forms.

- [ ] **Step 5: Verify GREEN and regressions**

Run: `pytest tests/test_answer_key_utils.py tests/test_objective_batch_recognition_service.py tests/test_answer_normalizer.py -q`

Expected: all tests pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add answer_key_utils.py objective_batch_recognition_service.py ai_grader.py tests/test_answer_key_utils.py
git commit -m "fix: unify grading answer equivalence forms"
```

---

### Task 2: Shared and cross-page major-question evidence groups

**Files:**
- Create: `major_region_evidence.py`
- Create: `tests/test_major_region_evidence.py`
- Modify: `hybrid_batch_grading_service.py:310-425, 990-1065`
- Modify: `tests/test_hybrid_grading_regressions.py`

**Interfaces:**
- Produces: `build_major_evidence_groups(parent_id: str, part_ids: list[str], regions: list[dict], overlap_threshold: float = 0.90) -> list[dict]`
- Each output group contains `page`, `bbox`, and ordered `part_ids`.
- Consumed by: `MajorQuestionAtlasBuilder.build`.

- [ ] **Step 1: Write failing overlap and cross-page tests**

```python
from major_region_evidence import build_major_evidence_groups


def test_nested_q10_regions_become_one_shared_group():
    regions = [
        {"page": "front", "mapped_question_id": "Q10(1)", "x": 100, "y": 100, "w": 100, "h": 100},
        {"page": "front", "mapped_question_id": "Q10(2)", "x": 90, "y": 90, "w": 130, "h": 130},
        {"page": "front", "mapped_question_id": "Q10(3)", "x": 80, "y": 80, "w": 160, "h": 160},
    ]
    groups = build_major_evidence_groups("Q10", ["Q10(1)", "Q10(2)", "Q10(3)"], regions)
    assert groups == [{
        "page": "front",
        "bbox": {"x": 80, "y": 80, "w": 160, "h": 160},
        "part_ids": ["Q10(1)", "Q10(2)", "Q10(3)"],
    }]


def test_cross_page_explicit_and_parent_regions_are_kept_separate():
    regions = [
        {"page": "front", "mapped_question_id": "Q12(1)", "x": 10, "y": 600, "w": 200, "h": 80},
        {"page": "back", "mapped_question_id": "Q12", "x": 20, "y": 20, "w": 600, "h": 700},
    ]
    groups = build_major_evidence_groups("Q12", ["Q12(1)", "Q12(2)", "Q12(3)"], regions)
    assert [(g["page"], g["part_ids"]) for g in groups] == [
        ("front", ["Q12(1)"]),
        ("back", ["Q12(2)", "Q12(3)"]),
    ]


def test_same_part_can_keep_evidence_on_two_pages():
    regions = [
        {"page": "front", "mapped_question_id": "Q10(2)", "x": 10, "y": 10, "w": 100, "h": 100},
        {"page": "back", "mapped_question_id": "Q10(2)", "x": 20, "y": 20, "w": 120, "h": 120},
    ]
    groups = build_major_evidence_groups("Q10", ["Q10(1)", "Q10(2)"], regions)
    assert [g["page"] for g in groups if "Q10(2)" in g["part_ids"]] == ["front", "back"]
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_major_region_evidence.py -q`

Expected: collection fails because the module does not exist.

- [ ] **Step 3: Implement evidence selection and same-page overlap clustering**

Implement these exact rules in pure functions:

```python
def containment_ratio(a: dict, b: dict) -> float:
    intersection = max(0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])) * max(
        0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    )
    smaller = min(a["w"] * a["h"], b["w"] * b["h"])
    return intersection / smaller if smaller > 0 else 0.0
```

Select explicit part regions first. Parent regions map only unresolved parts unless no explicit parts exist, in which case they map all parts. Cluster only groups on the same page whose containment ratio is at least `0.90`; merge with a union bbox and stable rubric part order.

- [ ] **Step 4: Integrate groups into atlas generation**

Replace `_major_sub_regions` and exact-coordinate deduplication with `build_major_evidence_groups`. Add one tile per evidence group per student; add one manifest `sub_item` per linked `part_id`, all sharing the same tile label and bbox. Update prompt wording to state that a shared tile may contain vertically, horizontally, or continuously written answers and must not duplicate score evidence.

- [ ] **Step 5: Verify GREEN using current Q10/Q12 regression shapes**

Run: `pytest tests/test_major_region_evidence.py tests/test_hybrid_grading_regressions.py -q`

Expected: Q10 produces one tile per student, Q12 produces two evidence tiles per student, cross-page evidence remains separate.

- [ ] **Step 6: Commit Task 2**

```powershell
git add major_region_evidence.py hybrid_batch_grading_service.py tests/test_major_region_evidence.py tests/test_hybrid_grading_regressions.py
git commit -m "feat: group shared and cross-page grading evidence"
```

---

### Task 3: Local grading completeness audit

**Files:**
- Create: `grading_completeness.py`
- Create: `tests/test_grading_completeness.py`
- Modify: `ai_grader.py:394-544`
- Modify: `hybrid_batch_grading_service.py:230-265, 950-980`

**Interfaces:**
- Produces: `audit_grading_details(rubric: dict, details: list[Any]) -> dict`
- Produces: `major_question_id(rubric: dict, question_id: str) -> str | None`
- Produces: `major_question_ids_for_issues(audit: dict) -> list[str]`
- Audit payload keys: `status`, `missing_question_ids`, `duplicate_question_ids`, `unexpected_question_ids`, `score_out_of_range`, `affected_major_question_ids`.

- [ ] **Step 1: Write failing completeness tests**

```python
from grading_completeness import audit_grading_details


RUBRIC = {"questions": [{
    "question_id": "Q10", "max_score": 10,
    "parts": [{"part_id": "Q10(1)", "part_score": 4}, {"part_id": "Q10(2)", "part_score": 6}],
}]}


def test_audit_detects_missing_part_and_parent_substitution():
    missing = audit_grading_details(RUBRIC, [{"question_id": "Q10(1)", "score_awarded": 4}])
    assert missing["status"] == "incomplete"
    assert missing["missing_question_ids"] == ["Q10(2)"]
    assert missing["affected_major_question_ids"] == ["Q10"]

    parent = audit_grading_details(RUBRIC, [{"question_id": "Q10", "score_awarded": 10}])
    assert parent["status"] == "invalid"
    assert parent["unexpected_question_ids"] == ["Q10"]


def test_audit_detects_duplicates_and_over_score():
    audit = audit_grading_details(RUBRIC, [
        {"question_id": "Q10(1)", "score_awarded": 5},
        {"question_id": "Q10(1)", "score_awarded": 4},
        {"question_id": "Q10(2)", "score_awarded": 6},
    ])
    assert audit["status"] == "invalid"
    assert audit["duplicate_question_ids"] == ["Q10(1)"]
    assert audit["score_out_of_range"][0]["question_id"] == "Q10(1)"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_grading_completeness.py -q`

Expected: collection fails because `grading_completeness` does not exist.

- [ ] **Step 3: Implement normalized rubric indexing and audit**

Build expected IDs from parts when present, otherwise the parent ID. Treat missing-only as `incomplete`; duplicates, unexpected IDs, non-numeric/negative/over-max scores as `invalid`; otherwise `complete`. Return stable rubric order.

- [ ] **Step 4: Enforce audit at both result boundaries**

In full-paper `_validate_and_convert`, audit converted details and raise `ValueError` unless status is complete. In hybrid `_build_result`, attach the audit as `raw_json["grading_completeness"]`, set `needs_human_review=True` for non-complete results, and preserve fallback reasons in the same payload.

- [ ] **Step 5: Verify GREEN and validator regressions**

Run: `pytest tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py tests/test_prompt_injection_guard.py -q`

Expected: all pass; full-paper parent substitution is rejected; hybrid missing parts remain structured and visible.

- [ ] **Step 6: Commit Task 3**

```powershell
git add grading_completeness.py ai_grader.py hybrid_batch_grading_service.py tests/test_grading_completeness.py
git commit -m "feat: audit grading result completeness locally"
```

---

### Task 4: Atomic whole-major retry and repeatable failures

**Files:**
- Modify: `db_manager.py:1288-1360, 1392-1470`
- Modify: `grading_service.py:80-365`
- Modify: `tests/test_retry_failed_grading.py`
- Create: `tests/test_atomic_major_retry.py`

**Interfaces:**
- Produces: `DBManager.replace_result_details_atomic(result_id: int, remove_question_ids: list[str], replacement_details: list[QuestionGradingDetail], *, student_score: float, needs_human_review: bool, raw_json: dict) -> None`
- Retry service consumes `grading_completeness.affected_major_question_ids` and reruns whole majors.

- [ ] **Step 1: Write failing atomic replacement and retry tests**

```python
import json

import pytest

from ai_grader import GradingResult, QuestionGradingDetail
from db_manager import DBManager


def detail(qid, score):
    return QuestionGradingDetail(qid, score, "", "K1", confidence_score=95)


def seed_result(tmp_path, existing):
    db = DBManager(tmp_path / "atomic.db")
    db.initialize()
    session_id = db.create_grading_session("exam", "rubric.json", "answer.json")
    with db._connect() as conn:
        student_id = conn.execute("INSERT INTO students (student_code, name) VALUES ('1', 'A')").lastrowid
        paper_id = conn.execute(
            "INSERT INTO exam_papers (session_id, front_image, ocr_name, student_id, match_status, processing_status) "
            "VALUES (?, 'front.jpg', 'A', ?, 'matched', 'graded')",
            (session_id, student_id),
        ).lastrowid
        conn.commit()
    result = GradingResult("A", 100, sum(existing.values()), True, [detail(q, s) for q, s in existing.items()], {
        "grading_completeness": {"status": "incomplete"}
    })
    result_id = db.save_session_result(session_id, student_id, paper_id, result)
    return db, result_id


def scores(db, result_id):
    return {row["question_id"]: row["score_awarded"] for row in db.get_result_details(result_id)}


def test_atomic_retry_replaces_all_parts_of_major_and_preserves_other_questions(tmp_path):
    db, result_id = seed_result(tmp_path, {"Q1": 7, "Q10(1)": 1, "Q10(2)": 2})
    db.replace_result_details_atomic(
        result_id,
        ["Q10(1)", "Q10(2)"],
        [detail("Q10(1)", 4), detail("Q10(2)", 6)],
        student_score=17,
        needs_human_review=False,
        raw_json={"grading_completeness": {"status": "complete"}},
    )
    assert scores(db, result_id) == {"Q1": 7, "Q10(1)": 4, "Q10(2)": 6}


def test_failed_atomic_retry_rolls_back_old_major(tmp_path):
    db, result_id = seed_result(tmp_path, {"Q1": 7, "Q10(1)": 1})
    with pytest.raises(AttributeError):
        db.replace_result_details_atomic(
            result_id,
            ["Q10(1)", "Q10(2)"],
            [object()],
            student_score=7,
            needs_human_review=True,
            raw_json={"grading_completeness": {"status": "incomplete"}},
        )
    assert scores(db, result_id) == {"Q1": 7, "Q10(1)": 1}
```

Extend the service test so one missing `Q10(2)` schedules Q10 for that student, replacement contains both Q10 parts, and a second failed retry remains listed for another retry.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q`

Expected: missing method and current partial merge behavior fail.

- [ ] **Step 3: Implement transactional detail replacement**

Use `BEGIN IMMEDIATE`; delete only `remove_question_ids`, insert every replacement detail, update score/review/raw JSON, then commit. Let exceptions roll back. Keep the same `session_results.id` and annotated-result linkage.

- [ ] **Step 4: Change retry selection and merge semantics**

When loading an incomplete result, derive affected parent majors. Add every existing part of those majors to the replacement set instead of the skip set. Continue skipping unaffected complete questions. Accept a replacement only if the rerun returns every part in that major; otherwise retain old details, update the failed-attempt record, and leave status incomplete.

Update `list_failed_papers` and `list_failed_papers_detailed` to include raw JSON containing non-complete `grading_completeness`, not only legacy `hybrid_batch_fallback`.

- [ ] **Step 5: Verify GREEN and repeated retry behavior**

Run: `pytest tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py -q`

Expected: whole major is replaced atomically; unrelated details and result ID survive; repeated failures remain retryable.

- [ ] **Step 6: Commit Task 4**

```powershell
git add db_manager.py grading_service.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py
git commit -m "feat: atomically retry incomplete major questions"
```

---

### Task 5: Incomplete-results UI and export markers

**Files:**
- Modify: `db_manager.py:1470-1540`
- Modify: `web_app.py:3110-3185, 4515-4868`
- Modify: `report.py:72-180, 220-286`
- Create: `tests/test_grading_completeness_ui.py`
- Create: `tests/test_report_completeness.py`

**Interfaces:**
- Produces: `DBManager.list_incomplete_results(session_id: int) -> list[dict]`
- Produces: `ReportGenerator._completeness_fields(raw_json: object) -> tuple[str, str]`
- Produces: report columns `批改完整性` and `缺失题目`.

- [ ] **Step 1: Write failing DB/UI/export tests**

```python
def test_incomplete_results_are_listed_with_missing_questions(db_with_result):
    rows = db_with_result.list_incomplete_results(1)
    assert rows[0]["completeness_status"] == "incomplete"
    assert rows[0]["missing_question_ids"] == ["Q11(1)", "Q11(2)", "Q11(3)"]


def test_report_fields_mark_incomplete_result(tmp_path):
    generator = ReportGenerator(tmp_path / "db.sqlite", tmp_path / "reports")
    status, missing = generator._completeness_fields({
        "grading_completeness": {"status": "incomplete", "missing_question_ids": ["Q11(1)"]}
    })
    assert status == "不完整"
    assert missing == "Q11(1)"
```

AST/UI test expectations: the grading page renders a dataframe from `list_incomplete_results`, contains a “一键补跑不完整大题” button, and does not hide the panel when paper status is `graded`.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py -q`

Expected: DB method and report columns are absent.

- [ ] **Step 3: Add structured incomplete-result query**

Read `raw_json.grading_completeness`, preserve legacy fallback compatibility, and return student/paper identifiers, status, missing IDs, affected majors, last reason, and attempt count. Do not expose API credentials or image bytes.

- [ ] **Step 4: Render the persistent UI panel and retry action**

Above existing retry controls, display count and a table with student, status, affected major, missing small question, and last error. The button invokes existing hybrid `failed_only=True`; copy states that each affected whole major is rerun and unrelated scores are preserved.

- [ ] **Step 5: Add export columns**

Select `sr.raw_json` in `ReportGenerator.export_session`, convert status to `完整/不完整/无效`, join missing IDs with `、`, and include both columns near total score in “成绩与小题明细”. Complete legacy rows default to `完整` only after their detail set has been audited during load.

- [ ] **Step 6: Verify GREEN**

Run: `pytest tests/test_grading_completeness_ui.py tests/test_report_completeness.py tests/test_report_score_adjustment.py -q`

Expected: incomplete results remain visible after failed retries and exported files identify them.

- [ ] **Step 7: Commit Task 5**

```powershell
git add db_manager.py web_app.py report.py tests/test_grading_completeness_ui.py tests/test_report_completeness.py
git commit -m "feat: expose incomplete grading in UI and exports"
```

---

### Task 6: Shared configuration limits and deprecated objective settings

**Files:**
- Create: `grading_limits.py`
- Create: `tests/test_grading_limits.py`
- Modify: `api_profiles.py:70-90`
- Modify: `web_app.py:165-215, 275-325, 340-375, 410-430, 3118-3125`
- Modify: `grading_service.py:155-166, 245-252`
- Modify: `objective_batch_recognition_service.py:588-625`
- Modify: `scanner.py:203-220`

**Interfaces:**
- Produces constants: `OBJECTIVE_BATCH_MIN/MAX`, `MAJOR_BATCH_MIN/MAX`, `PRECHECK_WORKERS_MIN/MAX`, `FULL_WORKERS_MIN/MAX`, `HYBRID_WORKERS_MIN/MAX`, `GRADING_RPM_MIN/MAX`.

- [ ] **Step 1: Write failing limit/profile tests**

```python
def test_objective_api_config_omits_deprecated_output_and_timeout_settings(profile_path):
    profile_path.write_text('[{"objective_timeout":60,"objective_max_tokens":100}]', encoding="utf-8")
    config = load_objective_config(profile_path)
    assert "timeout" not in config
    assert "max_tokens" not in config


def test_ui_and_backend_share_objective_and_precheck_maxima():
    assert OBJECTIVE_BATCH_MAX == 15
    assert PRECHECK_WORKERS_MAX == 32
```

Add AST assertions that `web_app.py` no longer references `objective_timeout_input` or `objective_max_tokens_input` and uses imported constants for number-input limits.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_grading_limits.py -q`

Expected: deprecated keys and mismatched hard-coded maxima are present.

- [ ] **Step 3: Add constants and remove deprecated fields**

Remove UI initialization, display, save, and objective config return values for timeout/max tokens. In `save_api_profiles`, copy each profile and `pop("objective_timeout", None)` / `pop("objective_max_tokens", None)` before JSON serialization so the next save cleans old profiles.

- [ ] **Step 4: Apply constants end to end**

Use the same limits in sidebar controls, quick controls, environment parsing, grading service bounds, and scanner. Keep objective backend max at 15. Make the objective prompt say `across {len(manifest['items'])} students` rather than “up to 15 students”.

- [ ] **Step 5: Verify GREEN**

Run: `pytest tests/test_grading_limits.py tests/test_objective_batch_recognition_service.py tests/test_session_config_state.py -q`

Expected: all limits match and deprecated keys disappear on save.

- [ ] **Step 6: Commit Task 6**

```powershell
git add grading_limits.py api_profiles.py web_app.py grading_service.py scanner.py objective_batch_recognition_service.py tests/test_grading_limits.py
git commit -m "fix: align grading configuration limits"
```

---

### Task 7: Strict request pacing for every attempt

**Files:**
- Create: `request_pacer.py`
- Create: `tests/test_request_pacer.py`
- Modify: `grading_service.py:158-177, 698-790, 841-864`
- Modify: `hybrid_batch_grading_service.py:140-175, 425-540`
- Modify: `objective_batch_recognition_service.py:180-225, 350-445`

**Interfaces:**
- Produces: `RequestPacer(requests_per_minute: int, *, clock=time.monotonic, sleeper=time.sleep)` with `acquire() -> None`.
- Every actual API attempt consumes one time slot.

- [ ] **Step 1: Write failing deterministic pacing tests**

```python
from request_pacer import RequestPacer


class FakeClock:
    def __init__(self):
        self.value = 0.0
        self.sleep_calls = []

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.sleep_calls.append(seconds)
        self.value += seconds


def test_first_request_has_no_burst_credit_and_following_requests_are_evenly_spaced():
    clock = FakeClock()
    pacer = RequestPacer(60, clock=clock.now, sleeper=clock.sleep)
    pacer.acquire()
    pacer.acquire()
    pacer.acquire()
    assert clock.sleep_calls == [1.0, 1.0]
    assert clock.now() == 2.0
```

Add an integration test beside the existing fake LLM client in `tests/test_hybrid_grading_regressions.py`: make the client raise twice and succeed on the third call, pass a `CountingLimiter`, invoke `grade_major_question_batch`, and assert both the client and limiter were called three times.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/test_request_pacer.py -q`

Expected: module missing and subjective retries acquire only once.

- [ ] **Step 3: Implement strict slot reservation**

```python
class RequestPacer:
    def __init__(self, requests_per_minute, *, clock=time.monotonic, sleeper=time.sleep):
        self.interval = 60.0 / max(1, int(requests_per_minute))
        self.clock = clock
        self.sleeper = sleeper
        self.lock = threading.Lock()
        self.next_slot = self.clock()

    def acquire(self):
        with self.lock:
            now = self.clock()
            slot = max(now, self.next_slot)
            self.next_slot = slot + self.interval
        delay = slot - now
        if delay > 0:
            self.sleeper(delay)
```

The first request starts immediately; no additional first-minute requests receive stored credit. Sleeping occurs outside the lock.

- [ ] **Step 4: Apply pacing to each retry attempt**

Replace `_RateLimiter` with `RequestPacer`. Full-paper attempts already re-enter `_grade_one_paper`; retain its acquire. Objective attempts retain their acquire inside retry loops. Pass the pacer into `grade_major_question_batch` and move subjective acquire inside its three-attempt loop; remove the one-time outer acquire.

- [ ] **Step 5: Verify GREEN and concurrency regressions**

Run: `pytest tests/test_request_pacer.py tests/test_objective_batch_recognition_service.py tests/test_retry_failed_grading.py -q`

Expected: deterministic spacing tests pass and every retry is counted.

- [ ] **Step 6: Commit Task 7**

```powershell
git add request_pacer.py grading_service.py hybrid_batch_grading_service.py objective_batch_recognition_service.py tests/test_request_pacer.py
git commit -m "fix: pace every grading request attempt"
```

---

### Task 8: Full verification and current-session dry audit

**Files:**
- Modify only if a failing regression requires an in-scope correction.

**Interfaces:**
- Consumes all prior task interfaces.
- Produces verification evidence; does not rewrite current session data.

- [ ] **Step 1: Run focused suite**

Run:

```powershell
pytest tests/test_answer_key_utils.py tests/test_major_region_evidence.py tests/test_hybrid_grading_regressions.py tests/test_grading_completeness.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py tests/test_grading_completeness_ui.py tests/test_report_completeness.py tests/test_grading_limits.py tests/test_request_pacer.py -q
```

Expected: all pass.

- [ ] **Step 2: Run complete project suite**

Run: `pytest -q`

Expected: all tests pass with no new warnings attributable to these changes.

- [ ] **Step 3: Run read-only audit against session 1**

Load the active rubric and each stored detail set, call `audit_grading_details`, and print only aggregate counts by status and missing question ID. Do not modify the database or expose student names.

Expected: historical missing Q11 details are reported as incomplete; current Q10/Q12 region grouping produces one and two evidence groups respectively.

- [ ] **Step 4: Review diff and preservation**

Run: `git diff --check` and `git status --short`.

Expected: no whitespace errors; unrelated pre-existing changes and user data remain untouched.

- [ ] **Step 5: Commit any final in-scope test adjustment**

Only if Step 1-4 required an in-scope correction:

```powershell
git add answer_key_utils.py major_region_evidence.py grading_completeness.py grading_limits.py request_pacer.py ai_grader.py objective_batch_recognition_service.py hybrid_batch_grading_service.py grading_service.py db_manager.py web_app.py report.py scanner.py tests/test_answer_key_utils.py tests/test_major_region_evidence.py tests/test_hybrid_grading_regressions.py tests/test_grading_completeness.py tests/test_atomic_major_retry.py tests/test_retry_failed_grading.py tests/test_grading_completeness_ui.py tests/test_report_completeness.py tests/test_grading_limits.py tests/test_request_pacer.py
git commit -m "test: verify grading completeness workflow"
```
