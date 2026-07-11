# User Communication and Model Guidance Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make project handoffs understandable to a user without programming experience and remove per-package Codex model recommendations without changing nightly automation.

**Architecture:** Put the durable communication rule in `AGENTS.md`, then remove package-level model-selection metadata from the package registry, phase maps, eligibility matrix, and master plan. Preserve operational model settings in `NIGHTLY_AUTOMATION.md` and preserve all product meanings of the word “model”.

**Tech Stack:** Markdown, PowerShell, ripgrep, Git.

## Global Constraints

- Do not modify business code, business tests, databases, or any path under `user_data/`. The documentation-governance checker and its focused tests may change only as required to stop treating removed model metadata as the package registry key.
- Do not change package status, dependencies, scope, acceptance criteria, nightly eligibility, or safety gates.
- Preserve the actual model configuration and release conditions in `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`.
- Preserve product and code concepts such as grading models, review models, data models, Pydantic models, model inputs, and provider compatibility.
- User-facing delivery must lead with outcome, impact, required user action, and next choices; unavoidable technical terms need a plain-language explanation or analogy.

---

### Task 1: Add the durable user-communication rule

**Files:**
- Modify: `AGENTS.md`
- Modify: `docs/user-testing/README.md`
- Modify: `docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md`

**Interfaces:**
- Consumes: The approved design in `docs/superpowers/specs/2026-07-11-user-communication-and-model-guidance-cleanup-design.md`.
- Produces: One authoritative communication rule and matching handoff/testing wording.

- [x] **Step 1: Add a communication section to `AGENTS.md`**

  State that the user has no programming background; default delivery order is result, impact, whether the user must act, and next choice. State that internal filenames, commands, test counts, Git details, and implementation mechanics are secondary unless needed for a decision or explicitly requested. Require a plain-language explanation or everyday analogy for unavoidable terms, while preserving complete safety and decision information.

- [x] **Step 2: Align user testing and package handoff wording**

  In `docs/user-testing/README.md`, explicitly say Codex translates technical failures into observable impact and simple next actions. In `PARALLEL_WORKTREE_EXECUTION.md`, require the technical handoff evidence to remain available while the user-facing summary follows the nontechnical delivery order.

- [x] **Step 3: Verify the rule is direct and non-contradictory**

  Run:

  ```powershell
  rg -n "编程基础|生活化|用户是否需要|用户可理解|技术证据" AGENTS.md docs/user-testing/README.md docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md
  ```

  Expected: all three documents contain compatible wording; no rule permits omitting safety risks or user decisions.

### Task 2: Remove per-package model recommendations

**Files:**
- Modify: `docs/superpowers/packages/EXECUTION_INDEX.md`
- Modify: `docs/superpowers/packages/README.md`
- Modify: `docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md`
- Modify: `docs/superpowers/packages/phase-1-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-2-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-3-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-4-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-5-execution-packages.md`
- Modify: `docs/superpowers/packages/phase-6-deferred.md`
- Modify: `docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md`

**Interfaces:**
- Consumes: Existing package definitions and nightly eligibility values.
- Produces: Package documentation that describes work, risk, and eligibility without recommending a Codex model per task.

- [x] **Step 1: Remove model fields from Index and phase maps**

  Remove the Index queue model column, every package-level `**模型：**` line in Phase 1–5, and the Phase 6 recommended-model sentence. Preserve any adjacent requirement such as official dependency verification, `frontend-design`, browser verification, or user participation by moving that requirement into the preceding acceptance/risk text where needed.

- [x] **Step 2: Remove the general per-task selection system**

  Delete the packages README model-code table, allocation percentages, package model-slot explanation, and model upgrade language. Rewrite workflow wording so planning and review are selected according to scope and risk without naming Sol, Terra, or Luna. Keep the five nightly release fields only where required by the unchanged automation contract.

- [x] **Step 3: Remove model recommendations from Index responsibilities and Master Plan**

  Remove the Index model column and README responsibility references to model codes. In the Master Plan, remove D9 model division and the appendix rule assigning categories of work to Sol, Terra, and Luna while preserving the decisions to use formal packages, run experiments first for Phase 5, and defer Phase 6.

- [x] **Step 4: Remove model columns and model-only prose from the eligibility matrix**

  Remove each `执行模型` column. Rewrite reasons such as “Sol 执行” as direct risk rules such as “涉及高风险迁移，只允许白天人工监督”. Preserve every `eligible_after_plan`, `daytime_only`, and `completed_not_applicable` value and every substantive safety reason.

- [x] **Step 5: Verify package metadata is gone**

  Run:

  ```powershell
  rg -n "\*\*模型：\*\*|推荐模型|模型（规划/执行/复核）|执行模型|模型代码|模型分配|Sol Extra High 负责|Terra High/Medium 负责|Luna" docs/superpowers/packages docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md
  ```

  Expected: no per-package or per-task model recommendation remains. Any result must be an unchanged nightly operational setting or a product meaning, not selection advice.

### Task 3: Protect nightly automation and validate documentation

**Files:**
- Verify only: `docs/superpowers/packages/NIGHTLY_AUTOMATION.md`
- Verify all modified Markdown files.
- Modify: `tools/check_documentation.py`
- Modify: `tests/test_documentation_governance.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: Evidence that automatic nightly operation and Markdown structure remain intact.

- [x] **Step 0: Align the documentation-governance checker**

  Add a focused failing test proving that package titles and nightly eligibility can be checked without model metadata. Update the checker to recognize Phase packages by their headings and matrix packages by the four remaining columns; keep duplicate, unexpected-ID, missing-ID, title, and historical-record checks.

- [x] **Step 1: Confirm nightly automation was not modified**

  Run:

  ```powershell
  git diff --exit-code -- docs/superpowers/packages/NIGHTLY_AUTOMATION.md
  ```

  Expected: exit code 0 and no diff.

- [x] **Step 2: Confirm package counts, statuses, and eligibility values are unchanged**

  Compare the pre-change and post-change package identifiers and eligibility tokens. Expected: all package IDs P1-09 through P5-13 remain present, and only table columns or model-only wording change.

- [x] **Step 3: Check Markdown and whitespace**

  Run:

  ```powershell
  git diff --check
  rg -n "\|.*\|" docs/superpowers/packages/EXECUTION_INDEX.md docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md
  ```

  Expected: `git diff --check` succeeds and edited tables have matching headers, separators, and row columns.

- [x] **Step 4: Review the complete diff for scope**

  Run:

  ```powershell
  git diff --stat
  git diff -- AGENTS.md docs/user-testing/README.md docs/superpowers/packages docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md
  git status --short
  ```

  Expected: only approved documentation files, this plan, the documentation-governance checker, and its focused tests are changed; no `user_data/`, business code, business tests, or nightly automation file is modified.

- [x] **Step 5: Commit the documentation cleanup**

  Stage only the approved documentation paths and commit with:

  ```powershell
  git commit -m "docs: simplify user handoffs and remove model guidance"
  ```
