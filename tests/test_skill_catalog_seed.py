from __future__ import annotations

import re
from pathlib import Path

from question_bank.database.schema import connect, initialize_database
from question_bank.taxonomy.registry import canonical_knowledge_seed_rows


STABLE_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:[._][a-z0-9]+)+$")
BROAD_NAMES = {"性质", "计算", "作图", "综合", "应用", "概念", "方法"}
CRITICAL_SKILLS = {
    "角平分线性质",
    "三角形外心作图",
    "最短路径作图",
    "轴对称作图",
    "一次函数图像应用",
    "全等三角形判定",
}


def test_builtin_catalog_is_concrete_complete_and_unique() -> None:
    from question_bank.taxonomy.skill_catalog_seed import (
        load_builtin_catalog,
        validate_builtin_catalog,
    )

    catalog = load_builtin_catalog()

    assert catalog.version == "junior_math_v1"
    assert catalog.subject == "math"
    assert len(catalog.topics) >= 20
    assert len(catalog.skills) >= 120
    assert validate_builtin_catalog(catalog) == ()

    topic_keys = {topic.stable_key for topic in catalog.topics}
    skill_keys = {skill.stable_key for skill in catalog.skills}
    assert len(topic_keys) == len(catalog.topics)
    assert len(skill_keys) == len(catalog.skills)
    assert all(STABLE_KEY_PATTERN.fullmatch(key) for key in topic_keys | skill_keys)
    assert all(skill.topic_key in topic_keys for skill in catalog.skills)
    assert all(skill.name not in BROAD_NAMES for skill in catalog.skills)
    assert CRITICAL_SKILLS.issubset({skill.name for skill in catalog.skills})


def test_builtin_catalog_covers_every_legacy_canonical_key() -> None:
    from question_bank.taxonomy.skill_catalog_seed import load_builtin_catalog

    catalog = load_builtin_catalog()
    covered = {
        key.casefold()
        for skill in catalog.skills
        for key in skill.legacy_keys
    }
    expected = {
        str(row["canonical_key"]).casefold()
        for row in canonical_knowledge_seed_rows()
    }

    assert expected <= covered


def test_builtin_catalog_seed_is_idempotent(tmp_path: Path) -> None:
    from question_bank.services.skill_catalog_service import SkillCatalogService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillCatalogService(db_path)

    with connect(db_path) as conn:
        first_counts = {
            "topics": conn.execute("SELECT COUNT(*) FROM skill_topics").fetchone()[0],
            "skills": conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
            "neighbors": conn.execute("SELECT COUNT(*) FROM skill_neighbors").fetchone()[0],
        }
    first = service.seed_builtin_catalog()
    second = service.seed_builtin_catalog()
    with connect(db_path) as conn:
        second_counts = {
            "topics": conn.execute("SELECT COUNT(*) FROM skill_topics").fetchone()[0],
            "skills": conn.execute("SELECT COUNT(*) FROM skills").fetchone()[0],
            "neighbors": conn.execute("SELECT COUNT(*) FROM skill_neighbors").fetchone()[0],
        }

    assert first_counts["skills"] >= 120
    assert first["skills_created"] == 0
    assert second["skills_created"] == 0
    assert second_counts == first_counts


def test_seed_does_not_overwrite_local_skill(tmp_path: Path) -> None:
    from question_bank.services.skill_catalog_service import SkillCatalogService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillCatalogService(db_path)
    service.seed_builtin_catalog()

    with connect(db_path) as conn:
        topic_id = conn.execute(
            "SELECT id FROM skill_topics WHERE stable_key = 'math.geometry.triangle'"
        ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO skills (stable_key, topic_id, name, origin)
            VALUES ('local.math.geometry.triangle.school_case', ?, '本校三角形模型', 'local')
            """,
            (topic_id,),
        )

    service.seed_builtin_catalog()

    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT name, origin FROM skills WHERE stable_key = 'local.math.geometry.triangle.school_case'"
        ).fetchone()
    assert dict(row) == {"name": "本校三角形模型", "origin": "local"}


def test_catalog_service_lists_teacher_labels_and_aliases(tmp_path: Path) -> None:
    from question_bank.services.skill_catalog_service import SkillCatalogService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillCatalogService(db_path)

    topics = service.list_topics()
    skills = service.list_skills()
    angle_bisector = next(item for item in skills if item["name"] == "角平分线性质")

    assert len(topics) >= 20
    assert angle_bisector["topic_name"] == "线与角"
    assert "角平分线的性质" in angle_bisector["aliases"]
    assert service.find_by_stable_key(angle_bisector["stable_key"])["id"] == angle_bisector["id"]


def test_merge_skill_redirects_reads_without_seed_reset(tmp_path: Path) -> None:
    from question_bank.services.skill_catalog_service import SkillCatalogService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillCatalogService(db_path)
    source = service.find_by_stable_key("math.geometry.construction.fold_path")
    target = service.find_by_stable_key("math.geometry.construction.shortest_path")

    service.merge_skill(source["id"], target["id"], actor="admin")
    service.seed_builtin_catalog()

    assert service.get_skill(source["id"])["id"] == target["id"]
    merged = next(item for item in service.list_skills(include_merged=True) if item["id"] == source["id"])
    assert merged["status"] == "merged"
    assert merged["redirect_skill_id"] == target["id"]
    assert all(item["id"] != source["id"] for item in service.list_skills())


def test_merge_skill_rejects_redirect_cycle(tmp_path: Path) -> None:
    from question_bank.services.skill_catalog_service import SkillCatalogService

    db_path = tmp_path / "question_bank.db"
    initialize_database(db_path)
    service = SkillCatalogService(db_path)
    source = service.find_by_stable_key("math.geometry.construction.fold_path")
    target = service.find_by_stable_key("math.geometry.construction.shortest_path")
    service.merge_skill(source["id"], target["id"], actor="admin")

    import pytest

    with pytest.raises(ValueError, match="cycle"):
        service.merge_skill(target["id"], source["id"], actor="admin")
