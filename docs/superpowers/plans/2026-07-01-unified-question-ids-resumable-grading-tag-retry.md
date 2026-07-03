# 统一题号、可暂停批改与打标签缺失重试 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 统一阅卷流程的题号身份，增加安全暂停、可靠判重、参考图降载和打标签批次错误重试，并保持旧配置和旧成绩安全兼容。

**Architecture:** 新增纯函数题号契约、批改运行账本和图片预处理三个窄模块。GradingService 继续负责编排，但改为有界增量派发；题库 AI 服务只发出结构化请求事件，场次工作流继续使用现有 question_bank_sync_details_json 作为同步状态来源。

**Tech Stack:** Python 3.12、Streamlit、SQLite、Pillow、pytest、OpenAI 兼容 Chat Completions。

## Global Constraints

- 规范大题号为 Q<正整数>，规范小问号为 Q<正整数>(P<正整数>)；单小问使用父题号。
- 裸 P1 只有在父题上下文中才可解析，不能作为全局题号。
- 页面读取 rubric/answer key 不得写文件；旧文件只做内存兼容。
- 安全暂停不取消已发送请求；停止新派发并保存已返回结果。
- 自动跳过必须同时匹配学生、试卷内容指纹和批改配置指纹。
- 同一学生、同一配置、不同试卷必须标为冲突。
- 参考图默认不放大，最大 960×2048，JPEG 质量 82，原图不变。
- 整卷大图请求并发默认 6，实际值取旧整卷并发与该值的较小者。
- 标签重试以数据库当前完整度为准，只发送缺失题，不覆盖已完成标签。
- 不改评分规则、知识点口径、模型供应商和现有成绩导出字段。
- 保留工作区已有 ARCHITECTURE.md、answer_key_utils.py、grading_service.py、web_app.py 和用户数据库修改；每次提交只暂存本任务文件。

---

## File Structure

Create:

- question_id_contract.py：题号解析、目录、别名解析和纯内存文档规范化。
- question_id_migration.py：仅供显式调用的双文件备份、校验和原子迁移。
- grading_run_identity.py：试卷/配置指纹和同学生候选答卷分类。
- grading_run_store.py：grading_runs、grading_run_items 的 SQLite 访问及暂停状态原子操作。
- reference_image_preparation.py：参考图缩放、JPEG 编码和像素统计。
- migrations/grading/002_add_grading_run_ledger.sql：幂等创建批改运行账本。
- tests/test_question_id_contract.py
- tests/test_question_id_readonly_integration.py
- tests/test_question_id_migration.py
- tests/test_grading_run_identity.py
- tests/test_grading_run_store.py
- tests/test_grading_pause_resume.py
- tests/test_reference_image_preparation.py
- tests/test_grading_run_ui.py
- tests/test_tagging_batch_attempts.py

Modify:

- answer_key_utils.py、answer_region_models.py、grading_completeness.py、grading_service.py、hybrid_batch_grading_service.py、major_region_evidence.py：统一题号边界。
- ai_grader.py、llm_client.py、usage_logger.py：缓存压缩参考图并记录图片指标。
- grading_limits.py、api_profiles.py、web_app.py：大图并发及暂停/恢复 UI。
- question_bank/services/ai_tagging_service.py、question_bank/services/grading_paper_intake_service.py：标签请求事件和保存失败。
- integration/grading_paper_skill_workflow_service.py、pages_shared/grading_paper_skill_workflow_component.py：批次错误持久化和展示。
- integration/question_tag_projection_service.py：规范来源题号。
- ARCHITECTURE.md：只在全部实现验证后记录实际架构。

---

### Task 1: 建立纯函数题号契约

**Files:**

- Create: question_id_contract.py
- Create: tests/test_question_id_contract.py

**Interfaces:**

- Produces: QuestionIdCatalog.from_document(document)
- Produces: QuestionIdCatalog.resolve(raw, parent_id=None)
- Produces: QuestionIdCatalog.expand(raw)
- Produces: canonicalize_question_document(document)
- Produces: QuestionIdContractError
- Depends on: Python 标准库，不依赖 UI、数据库或模型客户端。

- [ ] **Step 1: 写规范格式、兼容别名和不修改输入的失败测试**

~~~python
from copy import deepcopy

import pytest

from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonicalize_question_document,
)


def legacy_document() -> dict:
    return {
        "questions": [
            {"question_id": "Q1", "parts": [{"part_id": "P1"}]},
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "P1"},
                    {"part_id": "Q12(2)"},
                    {"part_id": "Q12_P3"},
                ],
            },
            {
                "question_id": "Q13",
                "parts": [{"part_id": "P1"}, {"part_id": "P2"}],
            },
        ]
    }


def test_catalog_scopes_legacy_parts_to_parent() -> None:
    catalog = QuestionIdCatalog.from_document(legacy_document())

    assert catalog.detail_ids == (
        "Q1",
        "Q12(P1)",
        "Q12(P2)",
        "Q12(P3)",
        "Q13(P1)",
        "Q13(P2)",
    )
    assert catalog.resolve("P1", parent_id="Q12") == "Q12(P1)"
    assert catalog.resolve("P1", parent_id="Q13") == "Q13(P1)"
    assert catalog.resolve("P1") is None
    assert catalog.resolve("Q12(1)") == "Q12(P1)"
    assert catalog.resolve("Q12-2") == "Q12(P2)"
    assert catalog.expand("Q12") == ("Q12(P1)", "Q12(P2)", "Q12(P3)")


def test_canonicalize_returns_copy_without_mutating_input() -> None:
    original = legacy_document()
    before = deepcopy(original)

    normalized = canonicalize_question_document(original)

    assert original == before
    assert normalized["questions"][0]["parts"][0]["part_id"] == "Q1"
    assert normalized["questions"][1]["parts"][0]["part_id"] == "Q12(P1)"


def test_duplicate_canonical_parts_are_rejected() -> None:
    payload = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [{"part_id": "P1"}, {"part_id": "Q12(1)"}],
            }
        ]
    }

    with pytest.raises(QuestionIdContractError, match=r"Q12\(P1\)"):
        QuestionIdCatalog.from_document(payload)
~~~

- [ ] **Step 2: 运行测试并确认因模块不存在而失败**

Run: python -m pytest tests/test_question_id_contract.py -q

Expected: FAIL，包含 ModuleNotFoundError: No module named 'question_id_contract'。

- [ ] **Step 3: 实现目录、解析和纯内存规范化**

核心数据结构固定为：

~~~python
@dataclass(frozen=True, slots=True)
class QuestionIdCatalog:
    parent_ids: tuple[str, ...]
    detail_ids: tuple[str, ...]
    parts_by_parent: dict[str, tuple[str, ...]]
    aliases: dict[str, str]

    @classmethod
    def from_document(cls, document: Mapping[str, Any]) -> "QuestionIdCatalog":
        return _build_catalog(document)

    def resolve(self, raw: object, parent_id: str | None = None) -> str | None:
        return _resolve_with_catalog(self, raw, parent_id)

    def expand(self, raw: object) -> tuple[str, ...]:
        resolved = self.resolve(raw)
        if resolved is None:
            return ()
        return self.parts_by_parent.get(resolved, (resolved,))
~~~

解析器必须接受 Q12(P1)、Q12(1)、Q12-1、Q12_1；仅在 parent_id 存在时接受 P1。单小问父题的 detail id 固定为 Q1。canonicalize_question_document 使用 copy.deepcopy，遇到两个别名归一到同一 id 时一次性抛出所有冲突。

- [ ] **Step 4: 运行题号契约测试**

Run: python -m pytest tests/test_question_id_contract.py -q

Expected: PASS。

- [ ] **Step 5: 提交中央契约**

~~~powershell
git add -- question_id_contract.py tests/test_question_id_contract.py
git commit -m "feat: add canonical question id contract"
~~~

---

### Task 2: 让绑定、批改和标签投影共用题号契约，并停止读取时写 JSON

**Files:**

- Modify: answer_key_utils.py
- Modify: answer_region_models.py
- Modify: grading_completeness.py
- Modify: grading_service.py
- Modify: hybrid_batch_grading_service.py
- Modify: major_region_evidence.py
- Modify: integration/grading_paper_skill_workflow_service.py
- Modify: integration/question_tag_projection_service.py
- Modify: web_app.py
- Create: question_id_migration.py
- Create: tests/test_question_id_readonly_integration.py
- Create: tests/test_question_id_migration.py
- Modify: tests/test_answer_region_models.py
- Modify: tests/test_grading_completeness.py
- Modify: tests/test_hybrid_grading_regressions.py
- Modify: tests/test_question_tag_projection_service.py

**Interfaces:**

- Consumes: QuestionIdCatalog 和 canonicalize_question_document。
- Produces: 所有加载函数返回规范化内存副本，磁盘文件保持不变。
- Produces: 审计、区域绑定、整卷/混合批改和标签投影使用同一规范集合。
- Produces: migrate_question_documents(rubric_path, answer_key_path, backup_dir)，只能由维护入口显式调用。

- [ ] **Step 1: 写跨流程一致性和只读加载失败测试**

~~~python
import hashlib
import json
from pathlib import Path

from answer_region_models import load_question_binding_catalog
from grading_completeness import audit_grading_details
from grading_service import _load_rubric_for_preflight, _target_question_ids_from_regions


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reading_legacy_ids_is_pure_and_all_flows_agree(tmp_path: Path) -> None:
    rubric_path = tmp_path / "rubric.json"
    payload = {
        "questions": [
            {
                "question_id": "Q12",
                "parts": [
                    {"part_id": "P1", "part_score": 2},
                    {"part_id": "P2", "part_score": 3},
                ],
            },
            {
                "question_id": "Q13",
                "parts": [
                    {"part_id": "P1", "part_score": 4},
                    {"part_id": "P2", "part_score": 5},
                ],
            },
        ]
    }
    rubric_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = digest(rubric_path)

    rubric = _load_rubric_for_preflight(rubric_path)
    binding = load_question_binding_catalog(rubric_path)
    targets = _target_question_ids_from_regions(
        [{"mapped_question_id": "Q12"}, {"mapped_question_id": "Q13(P2)"}],
        rubric=rubric,
    )
    audit = audit_grading_details(
        rubric,
        [
            {"question_id": "Q12(1)", "score_awarded": 2},
            {"question_id": "Q12-2", "score_awarded": 3},
            {"question_id": "Q13(P1)", "score_awarded": 4},
            {"question_id": "Q13_2", "score_awarded": 5},
        ],
    )

    assert binding.automatic_candidates == (
        "Q12(P1)", "Q12(P2)", "Q13(P1)", "Q13(P2)"
    )
    assert targets == ["Q12(P1)", "Q12(P2)", "Q13(P2)"]
    assert audit["status"] == "complete"
    assert digest(rubric_path) == before
~~~

- [ ] **Step 2: 运行测试并确认当前局部题号碰撞或文件改写**

Run: python -m pytest tests/test_question_id_readonly_integration.py tests/test_answer_region_models.py tests/test_grading_completeness.py -q

Expected: FAIL。

- [ ] **Step 3: 在所有加载边界使用纯内存规范化**

~~~python
def _load_rubric_for_preflight(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rubric = payload.get("rubric") if isinstance(payload.get("rubric"), dict) else payload
    return canonicalize_question_document(rubric)
~~~

区域目录使用 catalog.detail_ids 和 catalog.parent_ids。_target_question_ids_from_regions 对父题调用 catalog.expand；完整性审计、混合批改和 major_region_evidence 都用 catalog.resolve，删除各自的题号正则。

- [ ] **Step 4: 删除读取时写文件逻辑**

删除 answer_key_utils.normalize_rubric_and_answer_key_files 及其在 grading_service.py、web_app.py 的调用。答案查找先解析规范号，不再裸字符串比较。提示词示例统一为 Q13、Q13(P1)、Q13(P2)，明确模型输出规范格式。

- [ ] **Step 5: 实现显式、可回退的旧文件迁移**

test_question_id_migration.py 先验证：rubric 与 answer key 规范题号集合不一致时两个源文件和备份目录都不变；一致时先分别复制带时间戳的备份，再把两个规范化临时文件 fsync 后原子替换。question_id_migration.py 不得被页面加载或批改启动路径导入调用。

~~~python
@dataclass(frozen=True, slots=True)
class QuestionIdMigrationReport:
    rubric_backup: Path
    answer_key_backup: Path
    changed_files: tuple[Path, ...]


def migrate_question_documents(
    rubric_path: Path,
    answer_key_path: Path,
    backup_dir: Path,
) -> QuestionIdMigrationReport:
    rubric = canonicalize_question_document(_read_json(rubric_path))
    answer_key = canonicalize_question_document(_read_json(answer_key_path))
    if QuestionIdCatalog.from_document(rubric).detail_ids != QuestionIdCatalog.from_document(answer_key).detail_ids:
        raise QuestionIdContractError("rubric and answer key question ids differ")
    return _backup_and_atomically_replace(rubric_path, answer_key_path, rubric, answer_key, backup_dir)
~~~

- [ ] **Step 6: 统一标签投影来源题号**

integration/grading_paper_skill_workflow_service._source_question_ids 返回 QuestionIdCatalog.parent_ids；QuestionTagProjectionService 使用同一目录匹配 scoring item。

- [ ] **Step 7: 运行题号相关回归**

Run:

~~~powershell
python -m pytest tests/test_question_id_contract.py tests/test_question_id_readonly_integration.py tests/test_question_id_migration.py tests/test_answer_region_models.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py tests/test_question_tag_projection_service.py tests/test_grading_tag_context_and_errors.py -q
~~~

Expected: PASS。

- [ ] **Step 8: 提交跨流程统一**

~~~powershell
git add -- question_id_migration.py answer_key_utils.py answer_region_models.py grading_completeness.py grading_service.py hybrid_batch_grading_service.py major_region_evidence.py integration/grading_paper_skill_workflow_service.py integration/question_tag_projection_service.py web_app.py tests/test_question_id_readonly_integration.py tests/test_question_id_migration.py tests/test_answer_region_models.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py tests/test_question_tag_projection_service.py
git commit -m "fix: unify question ids without mutating config files"
~~~

---

### Task 3: 建立批改运行账本、指纹和候选答卷判定

**Files:**

- Create: grading_run_identity.py
- Create: grading_run_store.py
- Create: migrations/grading/002_add_grading_run_ledger.sql
- Create: tests/test_grading_run_identity.py
- Create: tests/test_grading_run_store.py

**Interfaces:**

- Produces: paper_fingerprint(front, back)
- Produces: grading_config_fingerprint(rubric, answer_key, answer_regions, grading_mode, grading_model)
- Produces: classify_student_candidates(candidates, completed, config_fingerprint)
- Produces: GradingRunStore.begin、resume、request_pause、control_state、record_item、finish

- [ ] **Step 1: 写三元判重和冲突失败测试**

~~~python
def test_same_student_same_paper_and_config_is_skipped(tmp_path: Path) -> None:
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    front.write_bytes(b"front")
    back.write_bytes(b"back")
    paper = paper_fingerprint(front, back)
    candidates = [CandidatePaper("a", 7, paper), CandidatePaper("b", 7, paper)]
    completed = [CompletedIdentity(7, paper, "cfg", True)]

    decisions = classify_student_candidates(candidates, completed, "cfg")

    assert [item.action for item in decisions] == [
        "skipped_existing",
        "skipped_duplicate",
    ]


def test_same_student_different_papers_are_all_conflicts() -> None:
    candidates = [
        CandidatePaper("a", 7, "paper-a"),
        CandidatePaper("b", 7, "paper-b"),
    ]

    decisions = classify_student_candidates(candidates, [], "cfg")

    assert {item.action for item in decisions} == {"conflict"}


def test_changed_config_allows_regrading() -> None:
    candidate = CandidatePaper("a", 7, "paper-a")
    completed = [CompletedIdentity(7, "paper-a", "old", True)]

    assert classify_student_candidates([candidate], completed, "new")[0].action == "grade"
~~~

- [ ] **Step 2: 写运行状态原子测试**

~~~python
def test_pause_and_resume_are_scoped_to_active_run(seed_session) -> None:
    store, session_id = seed_session
    run = store.begin(session_id, "cfg", "full_paper")

    assert store.control_state(run.run_token) == "running"
    assert store.request_pause(session_id) is True
    assert store.control_state(run.run_token) == "pause_requested"
    store.finish(run.run_token, "paused")
    assert store.resume(session_id, "cfg", "full_paper").id == run.id
    assert store.resume(session_id, "changed", "full_paper") is None
~~~

- [ ] **Step 3: 运行测试并确认缺少新模块/表**

Run: python -m pytest tests/test_grading_run_identity.py tests/test_grading_run_store.py -q

Expected: FAIL。

- [ ] **Step 4: 创建幂等账本迁移**

~~~sql
CREATE TABLE IF NOT EXISTS grading_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_token TEXT NOT NULL UNIQUE,
    session_id INTEGER NOT NULL,
    config_fingerprint TEXT NOT NULL,
    grading_mode TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('running','pause_requested','paused','completed','failed')
    ),
    started_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    finished_at TEXT,
    FOREIGN KEY(session_id) REFERENCES grading_sessions(id)
);

CREATE TABLE IF NOT EXISTS grading_run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    paper_id INTEGER,
    student_id INTEGER NOT NULL,
    source_label TEXT NOT NULL,
    paper_fingerprint TEXT NOT NULL,
    config_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN (
            'pending','grading','graded','failed',
            'skipped_existing','skipped_duplicate','conflict'
        )
    ),
    disposition_reason TEXT,
    result_id INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(run_id, source_label),
    FOREIGN KEY(run_id) REFERENCES grading_runs(id),
    FOREIGN KEY(paper_id) REFERENCES exam_papers(id),
    FOREIGN KEY(student_id) REFERENCES students(id),
    FOREIGN KEY(result_id) REFERENCES session_results(id)
);

CREATE INDEX IF NOT EXISTS idx_grading_runs_session_state
ON grading_runs(session_id, state, id);

CREATE INDEX IF NOT EXISTS idx_grading_run_items_identity
ON grading_run_items(student_id, paper_fingerprint, config_fingerprint, status);
~~~

GradingRunStore.initialize 执行相同 CREATE TABLE IF NOT EXISTS，兼容当前直接启动；发布迁移仍使用 SQL 文件。

- [ ] **Step 5: 实现稳定配置哈希**

~~~python
def grading_config_fingerprint(
    *,
    rubric,
    answer_key,
    answer_regions,
    grading_mode,
    grading_model,
) -> str:
    payload = {
        "rubric": canonicalize_question_document(rubric),
        "answer_key": canonicalize_question_document(answer_key),
        "answer_regions": sorted(
            (dict(item) for item in answer_regions),
            key=lambda item: (
                str(item.get("page") or ""),
                int(item.get("region_order") or 0),
                str(item.get("mapped_question_id") or ""),
            ),
        ),
        "grading_mode": str(grading_mode),
        "grading_model": str(grading_model or ""),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
~~~

并发/RPM 不进入配置指纹。begin 使用 BEGIN IMMEDIATE 拒绝第二个 active run；finish 只有 token 仍是最新运行时才更新 grading_sessions.status。

- [ ] **Step 6: 运行账本测试**

Run: python -m pytest tests/test_grading_run_identity.py tests/test_grading_run_store.py tests/test_grading_paper_source_state.py -q

Expected: PASS。

- [ ] **Step 7: 提交运行账本**

~~~powershell
git add -- grading_run_identity.py grading_run_store.py migrations/grading/002_add_grading_run_ledger.sql tests/test_grading_run_identity.py tests/test_grading_run_store.py
git commit -m "feat: add resumable grading run ledger"
~~~

---

### Task 4: 把整卷和混合批改改为可暂停、可恢复的增量派发

**Files:**

- Modify: grading_service.py
- Modify: hybrid_batch_grading_service.py
- Modify: objective_batch_recognition_service.py
- Create: tests/test_grading_pause_resume.py
- Modify: tests/test_retry_failed_grading.py
- Modify: tests/test_grading_limits.py

**Interfaces:**

- Consumes: GradingRunStore、CandidatePaper 和 CompletedIdentity。
- Produces: run_session_grading(session_id, exams_dir, rubric_path, answer_key_path, scan_analysis, manual_decisions, enhance_images, max_workers, requests_per_minute, grading_mode, failed_only, resume_run_id=None)
- Produces events: paper_skipped、paper_conflict、pause_requested、session_paused
- Hybrid/objective consume: should_pause: Callable[[], bool] | None

- [ ] **Step 1: 写安全暂停、恢复和冲突失败测试**

~~~python
def test_pause_stops_new_dispatch_and_saves_inflight(grading_fixture) -> None:
    service, store, session_id, analysis, grader = grading_fixture(paper_count=5)
    events = []
    generator = service.run_session_grading(
        session_id=session_id,
        scan_analysis=analysis,
        max_workers=2,
        grading_mode="full_paper",
    )

    for event in generator:
        events.append(event)
        if event["event"] == "grading_started":
            store.request_pause(session_id)

    run = store.latest(session_id)
    assert grader.call_count <= 2
    assert run.state == "paused"
    assert store.counts(run.id)["pending"] == 5 - grader.call_count
    assert store.counts(run.id)["graded"] == grader.call_count


def test_second_run_skips_completed_identities(grading_fixture) -> None:
    service, store, session_id, analysis, grader = grading_fixture(paper_count=3)

    list(service.run_session_grading(session_id=session_id, scan_analysis=analysis))
    calls = grader.call_count
    events = list(
        service.run_session_grading(session_id=session_id, scan_analysis=analysis)
    )

    assert grader.call_count == calls
    assert sum(event["event"] == "paper_skipped" for event in events) == 3


def test_different_papers_for_one_student_never_call_model(grading_fixture) -> None:
    service, store, session_id, analysis, grader = grading_fixture(
        paper_count=2,
        same_student=True,
        distinct_images=True,
    )

    events = list(
        service.run_session_grading(session_id=session_id, scan_analysis=analysis)
    )

    assert grader.call_count == 0
    assert sum(event["event"] == "paper_conflict" for event in events) == 2
~~~

- [ ] **Step 2: 运行测试并确认当前一次性提交行为失败**

Run: python -m pytest tests/test_grading_pause_resume.py -q

Expected: FAIL。

- [ ] **Step 3: 创建答卷记录前完成候选分组**

为每个已匹配 group 计算原始正反面 SHA-256，先按 student_id 分组。相同指纹只保留一个 grade 候选；多个不同指纹全部 conflict。随后再写 exam_papers 和 grading_run_items，避免两份同学生答卷同时入线程池。

completed identities 只读取历史 grading_run_items.status='graded' 且对应结果完整性为 complete 的记录。没有运行账本身份的旧成绩缺少配置指纹，不能仅凭学生或图片自动跳过。

- [ ] **Step 4: 用有界增量调度替换一次性 future_map**

~~~python
pending = iter(grade_items)
inflight: dict[Future, GradingWorkItem] = {}
pause_seen = False
with ThreadPoolExecutor(
    max_workers=effective_workers,
    thread_name_prefix="grading",
) as executor:
    while True:
        while len(inflight) < effective_workers and not pause_seen:
            if run_store.control_state(run.run_token) == "pause_requested":
                pause_seen = True
                break
            try:
                item = next(pending)
            except StopIteration:
                break
            run_store.mark_grading(item.run_item_id)
            future = executor.submit(_grade_and_persist, item)
            inflight[future] = item
            yield item.started_event()
        if not inflight:
            break
        done, _ = wait(
            set(inflight),
            timeout=0.1,
            return_when=FIRST_COMPLETED,
        )
        for future in done:
            inflight.pop(future)
            yield future.result()
        if pause_seen and not inflight:
            break
~~~

_grade_and_persist 在线程内完成模型调用、save_session_result、答卷状态和运行项状态更新。生成器 finally 等待在途任务并按账本状态写 paused 或 failed，未派发项保持 pending。

- [ ] **Step 5: 让混合批改在现有请求批次边界暂停**

run_objective_batch_recognition 每次提交客观题批次前、run_hybrid_batch_grading 每次提交大题批次前执行：

~~~python
if should_pause is not None and should_pause():
    paused = True
    break
~~~

混合结果增加 paused: bool。已完成批次继续合并；未运行批次不记为模型失败。恢复时使用现有缺失题集合，只补未完成大题/客观题批次。

- [ ] **Step 6: 运行暂停、失败重试和混合回归**

Run:

~~~powershell
python -m pytest tests/test_grading_pause_resume.py tests/test_retry_failed_grading.py tests/test_hybrid_grading_regressions.py tests/test_atomic_major_retry.py tests/test_grading_limits.py -q
~~~

Expected: PASS。

- [ ] **Step 7: 提交增量调度**

~~~powershell
git add -- grading_service.py hybrid_batch_grading_service.py objective_batch_recognition_service.py tests/test_grading_pause_resume.py tests/test_retry_failed_grading.py tests/test_grading_limits.py
git commit -m "feat: pause and resume grading safely"
~~~

---

### Task 5: 压缩参考图、固定请求前缀并限制大图并发

**Files:**

- Create: reference_image_preparation.py
- Create: tests/test_reference_image_preparation.py
- Modify: ai_grader.py
- Modify: llm_client.py
- Modify: usage_logger.py
- Modify: grading_limits.py
- Modify: api_profiles.py
- Modify: web_app.py
- Modify: tests/test_grading_limits.py
- Modify: tests/test_api_profile_store.py

**Interfaces:**

- Produces: PreparedImageBytes(blob, width, height, original_width, original_height)
- Produces: prepare_reference_image(blob, max_width=960, max_height=2048, quality=82)
- Produces profile key: grading_large_request_max_workers，范围 1–20，默认 6。

- [ ] **Step 1: 写图片缩放、缓存和并发失败测试**

~~~python
from io import BytesIO

from PIL import Image

from reference_image_preparation import prepare_reference_image


def png(size: tuple[int, int], mode: str = "RGB") -> bytes:
    color = (255, 255, 255, 128) if mode == "RGBA" else "white"
    image = Image.new(mode, size, color)
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def test_reference_image_is_downscaled_without_upscaling() -> None:
    large = prepare_reference_image(png((1191, 2645)))
    small = prepare_reference_image(png((600, 400)))

    assert (large.width, large.height) == (922, 2048)
    assert (small.width, small.height) == (600, 400)
    assert (large.original_width, large.original_height) == (1191, 2645)
    with Image.open(BytesIO(large.blob)) as decoded:
        assert decoded.mode == "RGB"
        assert decoded.format == "JPEG"


def test_ai_grader_prepares_reference_images_once(fake_ai_grader) -> None:
    grader, client, first_paper, second_paper, prepare_calls = fake_ai_grader

    grader.grade(first_paper)
    grader.grade(second_paper)

    assert prepare_calls.count == len(grader._prepared_reference_images)
    assert client.calls[0]["static_image_blobs"] == client.calls[1]["static_image_blobs"]
    assert len(client.calls[0]["image_blobs"]) == 2
~~~

- [ ] **Step 2: 运行图片和并发测试并确认失败**

Run: python -m pytest tests/test_reference_image_preparation.py tests/test_grading_limits.py tests/test_api_profile_store.py -q

Expected: FAIL。

- [ ] **Step 3: 实现参考图预处理**

~~~python
def prepare_reference_image(
    blob: bytes,
    *,
    max_width: int = 960,
    max_height: int = 2048,
    quality: int = 82,
) -> PreparedImageBytes:
    with Image.open(BytesIO(blob)) as source:
        original_width, original_height = source.size
        rgba = source.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        background.alpha_composite(rgba)
        rgb = background.convert("RGB")
        scale = min(
            1.0,
            max_width / original_width,
            max_height / original_height,
        )
        target = (
            round(original_width * scale),
            round(original_height * scale),
        )
        if target != rgb.size:
            rgb = rgb.resize(target, Image.Resampling.LANCZOS)
        output = BytesIO()
        rgb.save(output, format="JPEG", quality=quality, optimize=True)
        return PreparedImageBytes(
            output.getvalue(),
            target[0],
            target[1],
            original_width,
            original_height,
        )
~~~

AIGrader.__init__ 只准备一次不可变 reference image tuple；grade 不重复 base64 解码。

- [ ] **Step 4: 分离稳定参考图前缀和动态学生图片**

~~~python
parsed = self.llm_client.json_from_images_with_options(
    self._reference_prefix_prompt,
    [front_blob, back_blob],
    static_image_blobs=[
        item.blob for item in self._prepared_reference_images
    ],
    dynamic_prompt=self._build_student_prompt(
        paper_group.student_name or ""
    ),
    model=self.grading_model,
    system_prompt=self._cached_system_prompt,
    usage_callback=_usage_callback,
    extra_kwargs={"timeout": 300},
)
~~~

不支持 static_image_blobs 的兼容客户端继续按旧顺序发送，但使用压缩后的参考图。

- [ ] **Step 5: 增加用量指标和大图并发**

usage_logger 增加 reference_image_count、reference_image_pixels、student_image_count、request_attempt。AIGrader.grade 接受 request_attempt=1；_grade_one_paper_with_retries 每次实际模型尝试传入递增值。模型调用抛异常时也写 success=false、Token 为供应商可提供值或 0 的记录，避免失败重试在日志中消失。

grading_limits 增加 LARGE_REQUEST_WORKERS_MIN=1、DEFAULT=6、MAX=20。web_app 从 profile 读取、显示、保存 grading_large_request_max_workers，并设置 AI_GRADING_LARGE_REQUEST_MAX_WORKERS。整卷实际并发为 min(worker_count, large_request_worker_count)。

- [ ] **Step 6: 运行图片、配置和用量回归**

Run:

~~~powershell
python -m pytest tests/test_reference_image_preparation.py tests/test_grading_limits.py tests/test_api_profile_store.py tests/test_grading_pause_resume.py -q
~~~

Expected: PASS。

- [ ] **Step 7: 提交参考图降载**

~~~powershell
git add -- reference_image_preparation.py ai_grader.py llm_client.py usage_logger.py grading_limits.py api_profiles.py web_app.py tests/test_reference_image_preparation.py tests/test_grading_limits.py tests/test_api_profile_store.py
git commit -m "perf: reduce full-paper visual request load"
~~~

---

### Task 6: 记录打标签批次尝试并只重试当前缺失题

**Files:**

- Modify: question_bank/services/ai_tagging_service.py
- Modify: question_bank/services/grading_paper_intake_service.py
- Modify: integration/grading_paper_skill_workflow_service.py
- Modify: pages_shared/grading_paper_skill_workflow_component.py
- Create: tests/test_tagging_batch_attempts.py
- Modify: tests/test_question_bank_ai_tagging_quality.py
- Modify: tests/test_grading_paper_archive_intake.py
- Modify: tests/test_grading_paper_skill_workflow.py
- Modify: tests/test_grading_paper_skill_workflow_ui.py

**Interfaces:**

- Produces: TaggingAttemptEvent(request_number, request_kind, question_ids, phase, error_category, error_message, model_name)
- AITaggingService.analyze_questions accepts attempt_callback。
- GradingPaperIntakeResult produces tagging_attempts 和 failed_question_ids。
- GradingPaperWorkflowStatus produces tagging_attempts 和 missing_tag_question_total。

- [ ] **Step 1: 写请求生命周期、脱敏和缺失重试失败测试**

~~~python
def test_failed_batch_emits_started_and_failed_attempt(fake_batch_service) -> None:
    service, contexts = fake_batch_service(
        error=RuntimeError("429 token=secret-value")
    )
    attempts = []

    results = service.analyze_questions(
        contexts,
        max_workers=1,
        allow_batch_fallback=False,
        attempt_callback=attempts.append,
    )

    assert [event.phase for event in attempts] == ["started", "failed"]
    assert attempts[-1].error_category == "rate_limit"
    assert "secret-value" not in attempts[-1].error_message
    assert set(attempts[-1].question_ids) == set(contexts)
    assert all(not result.ok for result in results.values())


def test_retry_sends_only_current_missing_questions(workflow_fixture) -> None:
    service, session_id, tagger = workflow_fixture
    tagger.fail_question_numbers = {"2"}
    first = service.run(
        session_id,
        ai_service=tagger,
        max_workers=1,
        requests_per_minute=60,
    )
    tagger.requested_question_ids.clear()
    tagger.fail_question_numbers.clear()

    second = service.run(
        session_id,
        ai_service=tagger,
        max_workers=1,
        requests_per_minute=60,
    )

    assert first.missing_tag_question_total == 1
    assert tagger.requested_question_ids == [first.failed_question_ids[0]]
    assert second.state == "ready"
~~~

- [ ] **Step 2: 运行测试并确认当前只记录总数**

Run: python -m pytest tests/test_tagging_batch_attempts.py tests/test_grading_paper_skill_workflow.py -q

Expected: FAIL。

- [ ] **Step 3: 用统一控制器包裹每次真实模型请求**

~~~python
@dataclass(frozen=True, slots=True)
class TaggingAttemptEvent:
    request_number: int
    request_kind: str
    question_ids: tuple[int, ...]
    phase: str
    error_category: str = ""
    error_message: str = ""
    model_name: str = ""
~~~

_TaggingRequestController.run 先发 started，成功发 succeeded，异常发 failed 后重新抛出。batch、single_fallback、quality_retry 和 review 全部通过 run。错误类别固定为 rate_limit、timeout、network、parse、validation、quality、save、unknown；错误文本移除 api_key、authorization、token 值并截断 500 字符。

- [ ] **Step 4: 在入库服务中按数据库完整度构建目标集合**

~~~python
pending_questions = _questions_needing_complete_tags(
    database_path,
    questions,
)
contexts = {
    int(item["id"]): _tagging_context(item)
    for item in pending_questions
}
~~~

AI 合格但 save_tag_analysis 返回 false 时追加 phase=failed、error_category=save 的单题记录。完整题不进入 contexts，因此不会覆盖。

- [ ] **Step 5: 将最近 100 条尝试合并进场次同步 JSON**

~~~python
def _merge_tagging_attempts(
    existing: list[dict],
    incoming: list[dict],
) -> list[dict]:
    return [*existing, *incoming][-100:]
~~~

on_event 先读取 _sync_details(session)，只更新进度键并合并 tagging_attempts，不再用计数字段覆盖整个 JSON。更换原卷时沿用现有空对象重置。

- [ ] **Step 6: 页面显示最近失败批次和准确重试数量**

显示题库题目 ID、失败阶段和简化原因；按钮为“重试缺失题（N）”。标签完整时不显示重试入口。

- [ ] **Step 7: 运行标签与工作流回归**

Run:

~~~powershell
python -m pytest tests/test_tagging_batch_attempts.py tests/test_question_bank_ai_tagging_quality.py tests/test_grading_paper_archive_intake.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py -q
~~~

Expected: PASS。

- [ ] **Step 8: 提交标签批次重试**

~~~powershell
git add -- question_bank/services/ai_tagging_service.py question_bank/services/grading_paper_intake_service.py integration/grading_paper_skill_workflow_service.py pages_shared/grading_paper_skill_workflow_component.py tests/test_tagging_batch_attempts.py tests/test_question_bank_ai_tagging_quality.py tests/test_grading_paper_archive_intake.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py
git commit -m "feat: persist tagging batch failures and retry gaps"
~~~

---

### Task 7: 完成批改页面的暂停、恢复、跳过和冲突展示

**Files:**

- Modify: web_app.py
- Create: tests/test_grading_run_ui.py
- Modify: tests/test_grading_completeness_ui.py

**Interfaces:**

- Consumes: GradingRunStore.latest、counts、request_pause。
- Produces: “安全暂停”“正在安全暂停”“继续批改”和六类计数。
- Produces: 日志处理 paper_skipped、paper_conflict、session_paused。

- [ ] **Step 1: 写 UI 契约失败测试**

~~~python
from pathlib import Path


def test_page_exposes_pause_resume_and_identity_outcomes() -> None:
    source = Path("web_app.py").read_text(encoding="utf-8")

    for text in (
        "安全暂停",
        "正在安全暂停",
        "继续批改",
        "已跳过",
        "冲突",
        "实际整卷大图并发",
    ):
        assert text in source
    assert 'event["event"] == "paper_skipped"' in source
    assert 'event["event"] == "paper_conflict"' in source
    assert 'event["event"] == "session_paused"' in source
~~~

- [ ] **Step 2: 运行 UI 契约并确认失败**

Run: python -m pytest tests/test_grading_run_ui.py tests/test_grading_completeness_ui.py -q

Expected: FAIL。

- [ ] **Step 3: 实现暂停回调和恢复参数**

~~~python
def request_safe_pause() -> None:
    GradingRunStore(db.db_path).request_pause(selected_session_id)


st.button(
    "安全暂停",
    key=f"pause_grading_{selected_session_id}",
    on_click=request_safe_pause,
    disabled=run_state == "pause_requested",
)
~~~

paused 的“继续批改”把最新 run id 传给 resume_run_id；配置指纹变化时要求开始新运行，不能复用旧 pending 项。

- [ ] **Step 4: 展示计数和冲突详情**

~~~python
st.caption(
    f"已完成 {counts['graded']} · 在途 {counts['grading']} · "
    f"待处理 {counts['pending']} · 已跳过 {counts['skipped']} · "
    f"失败 {counts['failed']} · 冲突 {counts['conflict']}"
)
~~~

冲突区显示学生、source_label 和 disposition_reason；指纹只显示前 8 位。

- [ ] **Step 5: 运行 UI 和服务回归**

Run:

~~~powershell
python -m pytest tests/test_grading_run_ui.py tests/test_grading_completeness_ui.py tests/test_grading_pause_resume.py tests/test_grading_limits.py -q
~~~

Expected: PASS。

- [ ] **Step 6: 提交页面控制**

~~~powershell
git add -- web_app.py tests/test_grading_run_ui.py tests/test_grading_completeness_ui.py
git commit -m "feat: add grading pause and resume controls"
~~~

---

### Task 8: 全量验证、真实页面检查和架构文档

**Files:**

- Modify: ARCHITECTURE.md
- Modify tests only when verification exposes a real regression.

- [ ] **Step 1: 检查工作区，确认没有覆盖用户原改动**

Run: git status --short

Expected: 不暂存 user_data/databases/*.db、analysis_outputs 或其他用户资料。

- [ ] **Step 2: 运行静态编译**

Run:

~~~powershell
python -m py_compile question_id_contract.py question_id_migration.py grading_run_identity.py grading_run_store.py reference_image_preparation.py answer_key_utils.py answer_region_models.py grading_completeness.py grading_service.py hybrid_batch_grading_service.py objective_batch_recognition_service.py ai_grader.py llm_client.py integration/grading_paper_skill_workflow_service.py question_bank/services/ai_tagging_service.py question_bank/services/grading_paper_intake_service.py web_app.py
~~~

Expected: exit code 0。

- [ ] **Step 3: 运行全部定向测试**

Run:

~~~powershell
python -m pytest tests/test_question_id_contract.py tests/test_question_id_readonly_integration.py tests/test_question_id_migration.py tests/test_answer_region_models.py tests/test_grading_completeness.py tests/test_hybrid_grading_regressions.py tests/test_question_tag_projection_service.py tests/test_grading_run_identity.py tests/test_grading_run_store.py tests/test_grading_pause_resume.py tests/test_reference_image_preparation.py tests/test_grading_limits.py tests/test_tagging_batch_attempts.py tests/test_grading_paper_skill_workflow.py tests/test_grading_paper_skill_workflow_ui.py tests/test_grading_run_ui.py -q
~~~

Expected: PASS，0 failed。

- [ ] **Step 4: 运行全量测试**

Run: python -m pytest -q

Expected: PASS；环境性跳过需记录准确数量和原因。

- [ ] **Step 5: 在临时数据库演练迁移**

在隔离副本执行 002_add_grading_run_ledger.sql 两次，再调用 GradingRunStore.initialize。检查两张表、两个索引存在，旧 grading_sessions、exam_papers、session_results 行数不变。

- [ ] **Step 6: 使用浏览器技能验证真实 Streamlit 页面**

在 mock 模型或受控假响应下，用 in-app Browser 在 1366×768、1440×900、1920×1080 验证：

1. 作答区域候选包含全部规范父题/小问，打开页面前后旧 JSON SHA-256 不变。
2. 批改中点击安全暂停，状态依次为 running、pause_requested、paused。
3. 恢复后相同成绩显示“已跳过”，不同答卷显示“冲突”。
4. 题库卡片显示失败批次和“重试缺失题（N）”。
5. 控制台无应用异常，状态面板和长错误文本无横向溢出。

不得发送整班真实试卷，不产生额外批改费用。

- [ ] **Step 7: 离线验收当前 15 张参考图**

使用生产预处理函数处理当前 answer key。记录原始/处理后总像素和最长三题尺寸，人工查看 Q12、Q14、Q15 的公式、根号、上下标和作图线条。原图必须不变；处理后预计约 1139 万像素；单图不超过 960×2048。

- [ ] **Step 8: 合并更新架构文档**

在 ARCHITECTURE.md 增加已验证的中央题号契约、运行账本、暂停状态、三元幂等键、参考图预处理和标签批次状态。保留用户已有关于单用户边界、completed 语义和迁移权威的修改。

- [ ] **Step 9: 最终差异检查**

Run:

~~~powershell
git diff --check
git diff --stat
git status --short
~~~

Expected: 无空白错误；无密钥、数据库、用户试卷或生成图片进入任务提交。

- [ ] **Step 10: 提交架构文档**

~~~powershell
git add -- ARCHITECTURE.md
git commit -m "docs: document resilient grading architecture"
~~~
