"""AI 组卷确定性编排服务测试。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace


from question_bank.database.schema import initialize_database
from question_bank.models.question import QuestionCreate, TagCreate
from question_bank.services.ai_assembly_service import (
    AssemblySpec,
    SpecRow,
    select_questions,
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
_SECTION_KPS = tuple(point["display_name"] for point in _SECTION["knowledge_points"])
_KP_A1 = _SECTION_KPS[0]
_KP_A2 = _SECTION_KPS[1]
_OTHER_SECTION_KP = _CHAPTER["sections"][1]["knowledge_points"][0]["display_name"]


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
    text: str | None = None,
    paper_id: int | None = None,
    special_types: tuple[str, ...] = (),
) -> int:
    return store.add_question(
        QuestionCreate(
            question_number=number,
            question_type=question_type,
            # Distinct candidates must not collapse into the same content identity.
            question_text=text if text is not None else f"匿名题干 {paper_id}:{number}",
            answer_text="A",
            difficulty=difficulty,
            paper_id=paper_id,
            tags=[
                *(
                    TagCreate(tag_type="knowledge_point", tag_value=name)
                    for name in knowledge_points
                ),
                *(
                    TagCreate(tag_type="special_type", tag_value=value)
                    for value in special_types
                ),
            ],
        )
    )


# ---------------------------------------------------------------------------
# 题库概况
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 模板结构提取
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 细目表校验
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 范围解析
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 选题器
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 解答题子类（W4）
# ---------------------------------------------------------------------------


def test_allocation_reassigns_a_broad_row_to_fill_a_scarce_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    scarce = _add_question(
        store, number="1", difficulty="5", knowledge_points=(_KP_A1,)
    )
    alternative = _add_question(
        store, number="2", difficulty="3", knowledge_points=(_KP_A1,)
    )
    spec = AssemblySpec(
        title="整卷分配",
        rows=(
            SpecRow(question_type="选择题", count=1, difficulty=4),
            SpecRow(question_type="选择题", count=1, difficulty=6),
        ),
        scope_knowledge_points=(_KP_A1,),
    )
    result = select_questions(spec, store.reader)
    assert result.rows[0].question_ids == (alternative,)
    assert result.rows[1].question_ids == (scarce,)
    assert not result.gaps


def test_overlapping_tags_can_fill_and_reserved_questions_are_not_moved(
    tmp_path: Path,
) -> None:
    store = _make_store(tmp_path)
    points = (_KP_A1, _KP_A2, _OTHER_SECTION_KP)
    reserved = _add_question(store, number="0", knowledge_points=points)
    ids = {
        _add_question(store, number="1", knowledge_points=points),
        _add_question(store, number="2", knowledge_points=points[:2]),
        _add_question(store, number="3", knowledge_points=points[1:]),
    }
    spec = AssemblySpec(
        title="重合标签补齐",
        rows=(SpecRow(question_type="选择题", count=3, knowledge_points=points),),
        scope_knowledge_points=points,
    )
    result = select_questions(
        spec, store.reader, exclude_ids=(qid for qid in [reserved])
    )
    assert set(result.rows[0].question_ids) == ids
    assert not result.gaps
