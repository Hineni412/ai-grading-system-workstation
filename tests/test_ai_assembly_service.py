"""AI 组卷确定性编排服务测试。"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from question_bank.database.schema import initialize_database
from question_bank.models.question import QuestionCreate, TagCreate
from question_bank.services.ai_assembly_service import (
    AssemblySpec,
    AssemblySpecError,
    SpecRow,
    build_bank_profile,
    clear_bank_profile_cache,
    extract_template_structure,
    resolve_scope_knowledge_points,
    select_questions,
    suggest_relaxations,
    validate_spec,
)
from question_bank.services.assembly_workspace_service import (
    AssemblyRecordCreate,
    AssemblyWorkspaceService,
)
from question_bank.taxonomy.curriculum_catalog import load_curriculum_catalog
from tests.question_bank_support import QuestionBankTestStore

_CATALOG = load_curriculum_catalog()
_CHAPTER = _CATALOG["volumes"][0]["chapters"][0]
_SECTION = _CHAPTER["sections"][0]
_SECTION_KPS = tuple(
    point["display_name"] for point in _SECTION["knowledge_points"]
)
_KP_A1 = _SECTION_KPS[0]
_KP_A2 = _SECTION_KPS[1]
_OTHER_SECTION_KP = _CHAPTER["sections"][1]["knowledge_points"][0][
    "display_name"
]


def _make_store(tmp_path: Path) -> QuestionBankTestStore:
    data_root = tmp_path / "data"
    return QuestionBankTestStore(data_root / "databases" / "question_bank.db")


def _add_question(
    store: QuestionBankTestStore,
    *,
    number: str,
    question_type: str = "选择题",
    difficulty: str = "5",
    knowledge_points: tuple[str, ...] = (),
    text: str = "匿名题干",
    paper_id: int | None = None,
) -> int:
    return store.add_question(
        QuestionCreate(
            question_number=number,
            question_type=question_type,
            question_text=text,
            answer_text="A",
            difficulty=difficulty,
            paper_id=paper_id,
            tags=[
                TagCreate(tag_type="knowledge_point", tag_value=name)
                for name in knowledge_points
            ],
        )
    )


def _insert_paper(store: QuestionBankTestStore, paper_id: int, title: str) -> None:
    initialize_database(store.db_path)
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute(
            "INSERT INTO papers (id, title, import_status) VALUES (?, ?, 'success')",
            (paper_id, title),
        )
        connection.commit()
    finally:
        connection.close()


class _StubFrequencyService:
    def __init__(self, weights: dict[int, float]) -> None:
        self._weights = weights

    def metrics_for_questions(self, question_ids):
        return {
            int(question_id): SimpleNamespace(
                available=True,
                weighted_frequency=self._weights.get(int(question_id), 0.0),
            )
            for question_id in question_ids
        }


def _workspace_with_record(
    tmp_path: Path,
    question_ids: list[int],
) -> AssemblyWorkspaceService:
    data_root = tmp_path / "data"
    workspace = AssemblyWorkspaceService(data_root)
    empty = workspace.load_draft()
    draft = workspace.save_draft(
        expected_revision=empty.revision,
        draft={
            "basket_ids": list(question_ids),
            "order_ids": list(question_ids),
            "sections": [],
            "title": "历史组卷",
            "header_text": "",
            "include_answer": True,
            "layout_mode": "sequential",
            "preview_mode": "student",
        },
    )
    workspace.create_record(
        AssemblyRecordCreate(
            title="历史组卷",
            draft=draft,
            output_path=data_root / "历史组卷.md",
            export_format="markdown",
        )
    )
    return workspace


# ---------------------------------------------------------------------------
# 题库概况
# ---------------------------------------------------------------------------


def test_build_bank_profile_aggregates_without_question_text(
    tmp_path: Path,
) -> None:
    clear_bank_profile_cache()
    store = _make_store(tmp_path)
    secret_text_1 = "绝密题干甲一二三"
    secret_text_2 = "绝密题干乙四五六"
    _add_question(
        store,
        number="1",
        question_type="选择题",
        difficulty="3",
        knowledge_points=(_KP_A1,),
        text=secret_text_1,
    )
    _add_question(
        store,
        number="2",
        question_type="填空题",
        difficulty="5",
        knowledge_points=(_KP_A2,),
        text=secret_text_2,
    )
    _add_question(
        store,
        number="3",
        question_type="选择题",
        difficulty="5",
        knowledge_points=(_KP_A1, "目录外的知识点"),
    )
    deleted_id = _add_question(
        store,
        number="4",
        knowledge_points=(_KP_A1,),
    )
    store.delete_question(deleted_id)

    profile = build_bank_profile(store.reader)

    assert profile.total_questions == 3
    assert len(profile.sections) == 1
    section = profile.sections[0]
    assert section.section_id == _SECTION["id"]
    assert section.chapter_id == _CHAPTER["id"]
    assert section.question_count == 3
    assert section.difficulty_distribution == {"3": 1, "5": 2}
    assert section.question_type_distribution == {"选择题": 2, "填空题": 1}
    assert set(section.knowledge_points) == {_KP_A1, _KP_A2}
    assert profile.uncatalogued_knowledge_points == ("目录外的知识点",)

    # 红线：概况只含统计与名录，不得夹带题目正文。
    payload_text = json.dumps(profile.to_payload(), ensure_ascii=False)
    assert secret_text_1 not in payload_text
    assert secret_text_2 not in payload_text
    assert "question_text" not in payload_text


def test_build_bank_profile_cache_invalidates_on_update(
    tmp_path: Path,
) -> None:
    clear_bank_profile_cache()
    store = _make_store(tmp_path)
    _add_question(store, number="1", knowledge_points=(_KP_A1,))

    first = build_bank_profile(store.reader)
    assert build_bank_profile(store.reader) is first

    _add_question(store, number="2", knowledge_points=(_KP_A1,))
    # updated_at 精度为秒，直接推进时间戳保证失效令牌变化。
    connection = sqlite3.connect(store.db_path)
    try:
        connection.execute(
            "UPDATE questions SET updated_at = '2999-01-01 00:00:00'"
        )
        connection.commit()
    finally:
        connection.close()

    second = build_bank_profile(store.reader)
    assert second is not first
    assert second.total_questions == 2
    assert second.sections[0].question_count == 2


# ---------------------------------------------------------------------------
# 模板结构提取
# ---------------------------------------------------------------------------


def test_extract_template_structure_orders_by_question_number(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    _insert_paper(store, 1, "2024 期末真卷")
    _add_question(
        store, number="10", question_type="解答题", difficulty="7", paper_id=1
    )
    _add_question(
        store, number="2", question_type="填空题", difficulty="4", paper_id=1
    )
    _add_question(
        store, number="1", question_type="选择题", difficulty="2", paper_id=1
    )
    _add_question(store, number="1", question_type="选择题", difficulty="9")

    structure = extract_template_structure(store.reader, 1)

    assert structure.paper_id == 1
    assert structure.paper_title == "2024 期末真卷"
    assert [
        (entry.question_number, entry.question_type, entry.difficulty)
        for entry in structure.entries
    ] == [
        ("1", "选择题", 2),
        ("2", "填空题", 4),
        ("10", "解答题", 7),
    ]
    assert all(entry.score is None for entry in structure.entries)


# ---------------------------------------------------------------------------
# 细目表校验
# ---------------------------------------------------------------------------


def test_validate_spec_rejects_illegal_rows() -> None:
    with pytest.raises(AssemblySpecError):
        validate_spec(AssemblySpec(title="空", rows=()))
    with pytest.raises(AssemblySpecError):
        validate_spec(
            AssemblySpec(
                title="x",
                rows=(SpecRow(question_type="选择题", count=0),),
            )
        )
    with pytest.raises(AssemblySpecError):
        validate_spec(
            AssemblySpec(
                title="x",
                rows=(SpecRow(question_type="", count=1),),
            )
        )
    with pytest.raises(AssemblySpecError):
        validate_spec(
            AssemblySpec(
                title="x",
                rows=(SpecRow(question_type="选择题", count=1, difficulty=10),),
            )
        )
    with pytest.raises(AssemblySpecError):
        validate_spec(
            AssemblySpec(
                title="x",
                rows=(SpecRow(question_type="选择题", count=1, score=0),),
            )
        )


def test_spec_payload_roundtrip() -> None:
    spec = AssemblySpec(
        title="周测",
        rows=(
            SpecRow(
                question_type="选择题",
                count=2,
                knowledge_points=(_KP_A1,),
                difficulty=5,
                score=3.5,
            ),
        ),
        scope_knowledge_points=(_KP_A1, _KP_A2),
    )
    restored = AssemblySpec.from_payload(spec.to_payload())
    assert restored == spec


# ---------------------------------------------------------------------------
# 范围解析
# ---------------------------------------------------------------------------


def test_resolve_scope_knowledge_points_expands_catalog_ids() -> None:
    chapter_kps = tuple(
        point["display_name"]
        for section in _CHAPTER["sections"]
        for point in section["knowledge_points"]
    )
    kp_id = str(_SECTION["knowledge_points"][0]["id"])

    # 知识点级 catalog id 解析为该节点的 display_name（题标签命名惯例）。
    assert resolve_scope_knowledge_points((kp_id,), catalog=_CATALOG) == (_KP_A1,)
    assert (
        resolve_scope_knowledge_points((_SECTION["id"],), catalog=_CATALOG)
        == _SECTION_KPS
    )
    assert (
        resolve_scope_knowledge_points((_CHAPTER["id"],), catalog=_CATALOG)
        == chapter_kps
    )
    # 裸知识点名原样保留。
    assert resolve_scope_knowledge_points(
        ("目录外的知识点",), catalog=_CATALOG
    ) == ("目录外的知识点",)


def test_select_questions_with_knowledge_point_id_scope(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    in_scope = {
        _add_question(store, number=str(index), knowledge_points=(_KP_A1,))
        for index in range(1, 3)
    }
    _add_question(store, number="9", knowledge_points=(_OTHER_SECTION_KP,))

    kp_id = str(_SECTION["knowledge_points"][0]["id"])
    spec = AssemblySpec(
        title="知识点 id 范围",
        rows=(SpecRow(question_type="选择题", count=2),),
        scope_knowledge_points=resolve_scope_knowledge_points(
            (kp_id,), catalog=_CATALOG
        ),
    )
    result = select_questions(spec, store.reader)

    assert set(result.rows[0].question_ids) == in_scope
    assert not result.gaps


# ---------------------------------------------------------------------------
# 选题器
# ---------------------------------------------------------------------------


def test_select_questions_enforces_knowledge_scope(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    in_scope = {
        _add_question(store, number=str(index), knowledge_points=(_KP_A1,))
        for index in range(1, 4)
    }
    out_of_scope = {
        _add_question(
            store,
            number=str(index),
            knowledge_points=(_OTHER_SECTION_KP,),
        )
        for index in range(4, 6)
    }

    spec = AssemblySpec(
        title="范围过滤",
        rows=(
            SpecRow(
                question_type="选择题",
                count=5,
                knowledge_points=(_KP_A1,),
            ),
        ),
    )
    result = select_questions(spec, store.reader)
    picked = set(result.rows[0].question_ids)
    assert picked == in_scope
    assert picked.isdisjoint(out_of_scope)
    assert result.gaps[0].candidates == 3

    # 全卷硬边界：行内不带知识点时以 scope_knowledge_points 为边界。
    scoped = AssemblySpec(
        title="全卷边界",
        rows=(SpecRow(question_type="选择题", count=5),),
        scope_knowledge_points=(_OTHER_SECTION_KP,),
    )
    scoped_result = select_questions(scoped, store.reader)
    assert set(scoped_result.rows[0].question_ids) == out_of_scope


def test_select_questions_difficulty_tolerance_band(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    ids_by_difficulty = {
        difficulty: _add_question(
            store,
            number=str(difficulty),
            difficulty=str(difficulty),
            knowledge_points=(_KP_A1,),
        )
        for difficulty in (3, 4, 5, 6, 7)
    }

    spec = AssemblySpec(
        title="难度带",
        rows=(
            SpecRow(
                question_type="选择题",
                count=10,
                knowledge_points=(_KP_A1,),
                difficulty=5,
            ),
        ),
    )
    result = select_questions(spec, store.reader)

    picked = set(result.rows[0].question_ids)
    assert picked == {
        ids_by_difficulty[4],
        ids_by_difficulty[5],
        ids_by_difficulty[6],
    }
    assert ids_by_difficulty[3] not in picked
    assert ids_by_difficulty[7] not in picked
    assert result.gaps[0].missing == 7
    assert result.gaps[0].candidates == 3


def test_select_questions_frequency_weight_changes_ranking(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    low = _add_question(store, number="1", knowledge_points=(_KP_A1,))
    high = _add_question(store, number="2", knowledge_points=(_KP_A1,))
    spec = AssemblySpec(
        title="频度",
        rows=(
            SpecRow(
                question_type="选择题",
                count=1,
                knowledge_points=(_KP_A1,),
                difficulty=5,
            ),
        ),
    )

    result = select_questions(
        spec,
        store.reader,
        frequency_service=_StubFrequencyService({low: 0.0, high: 5.0}),
    )
    assert result.rows[0].question_ids == (high,)

    reversed_result = select_questions(
        spec,
        store.reader,
        frequency_service=_StubFrequencyService({low: 5.0, high: 0.0}),
    )
    assert reversed_result.rows[0].question_ids == (low,)


def test_select_questions_diversity_limit_per_knowledge_point(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    crowded = [
        _add_question(
            store, number=str(index), knowledge_points=(_KP_A1,)
        )
        for index in range(1, 4)
    ]
    sparse = _add_question(store, number="4", knowledge_points=(_KP_A2,))
    # 让拥挤知识点的题目频度全面领先，验证多样性上限仍然生效。
    stub = _StubFrequencyService(
        {question_id: 9.0 for question_id in crowded} | {sparse: 0.0}
    )
    spec = AssemblySpec(
        title="多样性",
        rows=(
            SpecRow(
                question_type="选择题",
                count=2,
                knowledge_points=(_KP_A1, _KP_A2),
                difficulty=5,
            ),
        ),
    )

    result = select_questions(spec, store.reader, frequency_service=stub)

    picked = result.rows[0].question_ids
    assert len(picked) == 2
    assert sparse in picked
    assert len(set(picked).intersection(crowded)) == 1


def test_select_questions_dedupe_excludes_recent_records(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    used = _add_question(store, number="1", knowledge_points=(_KP_A1,))
    workspace = _workspace_with_record(tmp_path, [used])
    spec = AssemblySpec(
        title="去重",
        rows=(
            SpecRow(
                question_type="选择题",
                count=1,
                knowledge_points=(_KP_A1,),
                difficulty=5,
            ),
        ),
    )

    result = select_questions(spec, store.reader, workspace=workspace)
    assert result.rows[0].question_ids == ()
    gap = result.gaps[0]
    assert gap.missing == 1
    assert gap.candidates == 1
    assert gap.excluded_by_dedupe == 1

    without_dedupe = select_questions(
        spec,
        store.reader,
        workspace=workspace,
        dedupe_enabled=False,
    )
    assert without_dedupe.rows[0].question_ids == (used,)
    assert not without_dedupe.gaps


def test_gap_suggestions_follow_relaxation_ladder(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    used = _add_question(store, number="1", knowledge_points=(_KP_A1,))
    _add_question(store, number="2", knowledge_points=(_KP_A1,))
    workspace = _workspace_with_record(tmp_path, [used])
    row = SpecRow(
        question_type="选择题",
        count=3,
        knowledge_points=(_KP_A1,),
        difficulty=5,
    )
    spec = AssemblySpec(
        title="缺口",
        rows=(row,),
        scope_knowledge_points=(_KP_A1,),
    )

    result = select_questions(spec, store.reader, workspace=workspace)

    assert len(result.rows[0].question_ids) == 1
    gap = result.gaps[0]
    assert gap.missing == 2
    assert gap.candidates == 2
    assert gap.excluded_by_dedupe == 1
    assert [suggestion.step for suggestion in gap.suggestions] == [
        "disable_dedupe",
        "relax_difficulty",
        "neighbor_knowledge",
        "relax_scope",
    ]
    assert all(suggestion.sacrifice for suggestion in gap.suggestions)
    # 无 knowledge_relations 数据时退化为同 curriculum_section 知识点。
    neighbor = gap.suggestions[2]
    assert set(neighbor.knowledge_points) == set(_SECTION_KPS) - {_KP_A1}


def test_suggest_relaxations_skips_inapplicable_steps(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    row = SpecRow(question_type="选择题", count=2)
    suggestions = suggest_relaxations(
        row,
        dedupe_enabled=True,
        excluded_by_dedupe=0,
        read_service=store.reader,
    )
    assert suggestions == ()
