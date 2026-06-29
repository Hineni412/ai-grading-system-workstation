# Import Dialog and Direct Tag Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the question-bank import dialog stable and stop automatic AI skill disambiguation when saving question tags.

**Architecture:** Put transient dialog-state rules in a small UI helper and keep the Streamlit page as orchestration. Make legacy skill-link synchronization explicit opt-in at the service boundary while preserving every existing raw tag field.

**Tech Stack:** Python 3.12, Streamlit 1.58, SQLite, pytest.

## Global Constraints

- Do not change database schema or add dependencies.
- Preserve existing raw AI tag fields and active exact-tag graph/training behavior.
- Preserve legacy skill resolution only behind `resolve_skills=True`.
- Do not modify user databases or runtime artifacts.

---

### Task 1: Import dialog session state

**Files:**
- Create: `pages_shared/question_bank_import_state.py`
- Modify: `pages/题库管理.py`
- Test: `tests/test_question_bank_import_state.py`
- Test: `tests/test_question_bank_import_dialog_ui.py`

**Interfaces:**
- Produces: `replace_import_scan_rows(state, rows)` and `clear_import_dialog_state(state)`.
- Consumes: mutable Streamlit session-state-compatible mapping.

- [x] Write failing tests asserting scan replacement clears stale editor/success state and dismissal preserves unrelated preferences.
- [x] Run `pytest tests/test_question_bank_import_state.py tests/test_question_bank_import_dialog_ui.py -q` and confirm failure because the helper and dismissal callback do not exist.
- [x] Add the state helper, wire `on_dismiss`, clear state on the visible close button, remove selection-time app reruns, and use fragment reruns for in-dialog table operations.
- [x] Re-run the two tests and confirm they pass.

### Task 2: Disable automatic legacy skill disambiguation

**Files:**
- Modify: `question_bank/services/question_service.py`
- Modify: `pages/题库管理.py`
- Test: `tests/test_question_skill_dual_write.py`
- Test: `tests/test_question_bank_import_dialog_ui.py`

**Interfaces:**
- Changes: `QuestionService.save_tag_analysis(..., resolve_skills: bool = False)`.
- Preserves: explicit `resolve_skills=True` legacy synchronization.

- [x] Write a failing test with a resolver that raises if called and assert default tag saving succeeds without resolver use or `question_skill_links` writes.
- [x] Update existing legacy dual-write tests to pass `resolve_skills=True` explicitly.
- [x] Run the focused service tests and confirm the new default-behavior test fails before production changes.
- [x] Change the default and remove LLM resolver construction from the active question-bank tagging page.
- [x] Re-run focused tests and confirm both direct-tag default and explicit legacy compatibility pass.

### Task 3: Documentation and verification

**Files:**
- Modify: `ARCHITECTURE.md`

**Interfaces:**
- Documents: current direct-tag save behavior and explicit legacy opt-in boundary.

- [x] Update the current implementation facts and integration table without changing unrelated architecture sections.
- [x] Run focused tests, then the full pytest suite.
- [x] Run syntax compilation for modified Python modules.
- [x] Verify the dialog in a real browser: open, close, reopen, confirm no stale scan rows and no console errors.
- [x] Review `git diff --check`, `git diff --stat`, and the final source diff for unrelated changes.
