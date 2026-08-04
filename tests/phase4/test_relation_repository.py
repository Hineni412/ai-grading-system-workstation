from __future__ import annotations

import shutil
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from backend.schema_migrations import ensure_schema_current
from question_bank.current_knowledge import CurrentKnowledgeUnavailable
from question_bank.database.schema import initialize_database
from question_bank.knowledge_graph_release import load_release
from question_bank.relations.contracts import (
    KnowledgeRelation,
    RelationConflict,
    RelationStatus,
    RelationType,
)
from question_bank.relations.repository import (
    KnowledgeRelationConfirmationConflict,
    KnowledgeRelationDuplicate,
    KnowledgeRelationRepository,
    KnowledgeRelationRevisionConflict,
    KnowledgeRelationTransitionError,
)
from question_bank.taxonomy.registry import CANONICAL_KNOWLEDGE
from update_tools.migrate_db import run_migrations
from tests.current_knowledge_support import install_current_knowledge


QUESTION_BANK_MIGRATIONS = (
    Path(__file__).resolve().parents[2] / "migrations" / "question_bank"
)


@pytest.fixture
def relation_store(
    tmp_path: Path,
) -> tuple[Path, KnowledgeRelationRepository]:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    install_current_knowledge(database)
    return database, KnowledgeRelationRepository(database)


def test_relation_suggestion_requires_a_current_standard(tmp_path: Path) -> None:
    database = tmp_path / "question-bank.db"
    initialize_database(database)
    repository = KnowledgeRelationRepository(database)

    with pytest.raises(CurrentKnowledgeUnavailable):
        _suggest(
            repository,
            "kp_alg_real_numbers",
            "kp_alg_equation_properties",
        )


def _relation(
    source_key: str,
    target_key: str,
    relation_type: RelationType = RelationType.PARENT,
) -> KnowledgeRelation:
    return KnowledgeRelation(
        source_key=source_key,
        target_key=target_key,
        relation_type=relation_type,
    )


def _suggest(
    repository: KnowledgeRelationRepository,
    source_key: str,
    target_key: str,
    relation_type: RelationType = RelationType.PARENT,
    *,
    operation_id: str | None = None,
):
    return repository.create_suggestion(
        _relation(source_key, target_key, relation_type),
        source_kind="model",
        rationale="合成关系建议",
        source_operation_id=operation_id,
        model_name="fake-relation-model",
        model_version="v1",
    )


def _confirm(repository: KnowledgeRelationRepository, relation_id: str, revision: int):
    return repository.transition(
        relation_id,
        expected_revision=revision,
        to_status=RelationStatus.CONFIRMED,
        actor_ref="teacher-synthetic",
        reason="合成审核确认",
    )


def test_empty_database_seeds_governed_identities_without_relations(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    database, repository = relation_store

    identities = repository.list_identities()

    canonical_keys = {
        item.canonical_id.casefold() for item in CANONICAL_KNOWLEDGE
    }
    release_keys = {
        str(item["stable_key"])
        for item in load_release().payload["core_nodes"]
    }
    by_key = {identity.stable_key: identity for identity in identities}
    assert set(by_key) == canonical_keys | release_keys
    assert all(by_key[key].origin == "builtin" for key in canonical_keys)
    assert repository.list_active_relations() == ()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_tag_aliases"
            ).fetchone()[0]
            >= len(CANONICAL_KNOWLEDGE) * 2
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_tag_identity_mappings"
            ).fetchone()[0]
            == 0
        )


def test_historical_database_keeps_tags_and_maps_only_exact_governed_values(
    tmp_path: Path,
) -> None:
    database = tmp_path / "historical.db"
    old_migrations = tmp_path / "old-migrations"
    old_migrations.mkdir()
    for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 11:
            shutil.copy2(source, old_migrations / source.name)
    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=old_migrations,
        backup_dir_override=tmp_path / "old-backups",
    )
    assert report.error is None

    with sqlite3.connect(database) as connection:
        question_id = connection.execute(
            """
            INSERT INTO questions (question_number, question_text)
            VALUES ('1', '合成历史题')
            """
        ).lastrowid
        values = ("一次函数", "待定系数法", "未治理合成知识")
        connection.executemany(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', ?, 'synthetic')
            """,
            ((question_id, value) for value in values),
        )
        connection.commit()

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        stored_values = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT tag_value
                FROM question_tags
                WHERE question_id = ?
                ORDER BY id
                """,
                (question_id,),
            )
        ]
        mappings = connection.execute(
            """
            SELECT mapping.source_value_snapshot, mapping.stable_key
            FROM knowledge_tag_identity_mappings mapping
            ORDER BY mapping.question_tag_id
            """
        ).fetchall()
    assert stored_values == list(values)
    assert mappings == [
        ("一次函数", "kp_fun_linear"),
        ("待定系数法", "kp_fun_linear"),
    ]


def test_release_scoped_relation_migration_preserves_history(
    tmp_path: Path,
) -> None:
    database = tmp_path / "relation-history.db"
    old_migrations = tmp_path / "migrations-through-027"
    old_migrations.mkdir()
    for source in sorted(QUESTION_BANK_MIGRATIONS.glob("*.sql")):
        if int(source.name.split("_", 1)[0]) <= 27:
            shutil.copy2(source, old_migrations / source.name)
    report = run_migrations(
        "question_bank",
        db_path=database,
        migrations_dir=old_migrations,
        backup_dir_override=tmp_path / "old-backups",
    )
    assert report.error is None

    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO knowledge_tag_identities (
                stable_key, display_name, origin
            ) VALUES (?, ?, 'local')
            """,
            (
                ("kp_history_source", "历史起点"),
                ("kp_history_target", "历史终点"),
            ),
        )
        connection.execute(
            """
            INSERT INTO knowledge_relations (
                relation_id, source_key, target_key, relation_type,
                status, source_kind, rationale, decision_by, decided_at
            ) VALUES (
                'kr_history', 'kp_history_source', 'kp_history_target',
                'parent', 'confirmed', 'teacher', '历史关系',
                'teacher-history', datetime('now','localtime')
            )
            """
        )
        connection.execute(
            """
            INSERT INTO knowledge_relation_audit_events (
                relation_id, event_type, from_status, to_status,
                actor_kind, actor_ref, reason,
                expected_revision, resulting_revision
            ) VALUES (
                'kr_history', 'confirmed', NULL, 'confirmed',
                'teacher', 'teacher-history', '历史确认', 0, 1
            )
            """
        )
        connection.commit()

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        relation = connection.execute(
            """
            SELECT relation_id, status, rationale, graph_release_id
            FROM knowledge_relations
            WHERE relation_id = 'kr_history'
            """
        ).fetchone()
        events = connection.execute(
            """
            SELECT event_type, actor_ref, reason
            FROM knowledge_relation_audit_events
            WHERE relation_id = 'kr_history'
            """
        ).fetchall()
        foreign_key_issues = connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()
    assert relation == ("kr_history", "confirmed", "历史关系", None)
    assert events == [("confirmed", "teacher-history", "历史确认")]
    assert foreign_key_issues == []


def test_bootstrap_keeps_stable_identity_when_visible_tag_text_changes(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    database, _repository = relation_store
    with sqlite3.connect(database) as connection:
        question_id = connection.execute(
            """
            INSERT INTO questions (question_number, question_text)
            VALUES ('1', '合成标签改名题')
            """
        ).lastrowid
        tag_id = connection.execute(
            """
            INSERT INTO question_tags (
                question_id, tag_type, tag_value, source
            ) VALUES (?, 'knowledge_point', '三角形全等', 'synthetic')
            """,
            (question_id,),
        ).lastrowid
        connection.commit()

    initialize_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE question_tags SET tag_value = '轴对称' WHERE id = ?",
            (tag_id,),
        )
        connection.commit()

    initialize_database(database)

    with sqlite3.connect(database) as connection:
        mapping = connection.execute(
            """
            SELECT stable_key, source_value_snapshot
            FROM knowledge_tag_identity_mappings
            WHERE question_tag_id = ?
            """,
            (tag_id,),
        ).fetchone()
    assert mapping == ("kp_geo_triangle_congruence", "三角形全等")


def test_bootstrap_conflict_rolls_back_all_new_identity_rows(
    tmp_path: Path,
) -> None:
    database = tmp_path / "conflict.db"
    ensure_schema_current(
        "question_bank",
        database,
        backup_dir=tmp_path / "backups",
    )
    conflicting = CANONICAL_KNOWLEDGE[-1]
    stable_key = conflicting.canonical_id.casefold()
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            INSERT INTO knowledge_tag_identities (
                stable_key, display_name, origin
            ) VALUES (?, '冲突名称', 'local')
            """,
            (stable_key,),
        )
        connection.commit()

    with pytest.raises(ValueError, match="governed knowledge identity conflicts"):
        initialize_database(database)

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            """
            SELECT stable_key, display_name, origin
            FROM knowledge_tag_identities
            ORDER BY stable_key
            """
        ).fetchall()
    assert rows == [(stable_key, "冲突名称", "local")]


def test_model_suggestion_requires_version_and_is_operation_idempotent(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    _database, repository = relation_store
    relation = _relation("kp_alg_real_numbers", "kp_alg_equation_properties")

    with pytest.raises(ValueError, match="model_name and model_version"):
        repository.create_suggestion(
            relation,
            source_kind="model",
            rationale="缺少模型版本",
        )

    first = _suggest(
        repository,
        relation.source_key,
        relation.target_key,
        operation_id="synthetic-operation-1",
    )
    repeated = _suggest(
        repository,
        relation.source_key,
        relation.target_key,
        operation_id="synthetic-operation-1",
    )
    assert repeated == first

    with pytest.raises(KnowledgeRelationDuplicate) as caught:
        _suggest(
            repository,
            relation.source_key,
            relation.target_key,
            operation_id="synthetic-operation-2",
        )
    assert caught.value.relation_id == first.relation_id
    assert caught.value.status is RelationStatus.SUGGESTED


def test_legal_relation_lifecycle_and_audit_are_revisioned(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    _database, repository = relation_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )

    confirmed = _confirm(repository, suggested.relation_id, suggested.revision)
    retired = repository.transition(
        confirmed.relation_id,
        expected_revision=confirmed.revision,
        to_status=RelationStatus.RETIRED,
        actor_ref="teacher-synthetic",
        reason="合成退役",
    )
    restored = repository.transition(
        retired.relation_id,
        expected_revision=retired.revision,
        to_status=RelationStatus.CONFIRMED,
        actor_ref="teacher-synthetic",
        reason="合成恢复",
    )

    assert [confirmed.revision, retired.revision, restored.revision] == [2, 3, 4]
    assert restored.status is RelationStatus.CONFIRMED
    assert [event["event_type"] for event in repository.audit_events(restored.relation_id)] == [
        "suggested",
        "confirmed",
        "retired",
        "restored",
    ]
    active = repository.list_active_relations()
    assert len(active) == 1
    assert active[0].relation_id == restored.relation_id


@pytest.mark.parametrize(
    ("initial_target", "invalid_target"),
    [
        (RelationStatus.REJECTED, RelationStatus.CONFIRMED),
        (RelationStatus.CONFIRMED, RelationStatus.REJECTED),
    ],
)
def test_illegal_relation_transitions_are_rejected(
    relation_store: tuple[Path, KnowledgeRelationRepository],
    initial_target: RelationStatus,
    invalid_target: RelationStatus,
) -> None:
    _database, repository = relation_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    current = repository.transition(
        suggested.relation_id,
        expected_revision=suggested.revision,
        to_status=initial_target,
        actor_ref="teacher-synthetic",
        reason="合成首轮决定",
    )

    with pytest.raises(KnowledgeRelationTransitionError):
        repository.transition(
            current.relation_id,
            expected_revision=current.revision,
            to_status=invalid_target,
            actor_ref="teacher-synthetic",
            reason="非法状态变化",
        )


def test_stale_revision_cannot_overwrite_teacher_decision(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    _database, repository = relation_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    confirmed = _confirm(repository, suggested.relation_id, suggested.revision)

    with pytest.raises(KnowledgeRelationRevisionConflict) as caught:
        repository.transition(
            confirmed.relation_id,
            expected_revision=suggested.revision,
            to_status=RelationStatus.RETIRED,
            actor_ref="teacher-stale",
            reason="过期页面操作",
        )
    assert caught.value.current_revision == confirmed.revision
    assert repository.get_relation(confirmed.relation_id).status is RelationStatus.CONFIRMED


def test_parallel_confirm_allows_only_one_revision_winner(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    database, repository = relation_store
    suggested = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    barrier = Barrier(2)

    def attempt(actor: str) -> str:
        worker_repository = KnowledgeRelationRepository(database)
        barrier.wait()
        try:
            worker_repository.transition(
                suggested.relation_id,
                expected_revision=suggested.revision,
                to_status=RelationStatus.CONFIRMED,
                actor_ref=actor,
                reason="并发合成确认",
            )
            return "confirmed"
        except KnowledgeRelationRevisionConflict:
            return "stale"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, ("teacher-a", "teacher-b")))

    assert sorted(outcomes) == ["confirmed", "stale"]
    assert repository.get_relation(suggested.relation_id).revision == 2
    assert len(repository.list_active_relations()) == 1


def test_active_pair_type_conflict_is_rejected_until_old_relation_retires(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    _database, repository = relation_store
    first = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
        RelationType.PARENT,
    )
    confirmed = _confirm(repository, first.relation_id, first.revision)
    competing = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
        RelationType.RELATED,
    )

    with pytest.raises(KnowledgeRelationConfirmationConflict) as caught:
        _confirm(repository, competing.relation_id, competing.revision)
    assert caught.value.conflicts == (RelationConflict.TYPE_CONFLICT,)

    repository.transition(
        confirmed.relation_id,
        expected_revision=confirmed.revision,
        to_status=RelationStatus.RETIRED,
        actor_ref="teacher-synthetic",
        reason="切换关系语义",
    )
    now_confirmed = _confirm(
        repository,
        competing.relation_id,
        competing.revision,
    )
    assert now_confirmed.status is RelationStatus.CONFIRMED


def test_parent_cycle_never_enters_active_projection(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    _database, repository = relation_store
    first = _suggest(
        repository,
        "kp_alg_real_numbers",
        "kp_alg_equation_properties",
    )
    second = _suggest(
        repository,
        "kp_alg_equation_properties",
        "kp_alg_linear_equation",
    )
    closing = _suggest(
        repository,
        "kp_alg_linear_equation",
        "kp_alg_real_numbers",
    )
    _confirm(repository, first.relation_id, first.revision)
    _confirm(repository, second.relation_id, second.revision)

    with pytest.raises(KnowledgeRelationConfirmationConflict) as caught:
        _confirm(repository, closing.relation_id, closing.revision)
    assert caught.value.conflicts == (RelationConflict.PARENT_CYCLE,)
    assert len(repository.list_active_relations()) == 2
    assert repository.get_relation(closing.relation_id).status is RelationStatus.SUGGESTED


def test_database_constraints_reject_missing_identity_and_active_reverse_pair(
    relation_store: tuple[Path, KnowledgeRelationRepository],
) -> None:
    database, repository = relation_store
    confirmed = _confirm(
        repository,
        _suggest(
            repository,
            "kp_alg_real_numbers",
            "kp_alg_equation_properties",
        ).relation_id,
        1,
    )
    assert confirmed.status is RelationStatus.CONFIRMED
    current_release_id = repository.current_release_id()

    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO knowledge_relations (
                    relation_id, source_key, target_key, relation_type,
                    status, source_kind, rationale,
                    decision_by, decided_at, graph_release_id
                ) VALUES (
                    'kr_missing_identity',
                    'kp_missing',
                    'kp_alg_real_numbers',
                    'parent',
                    'confirmed',
                    'teacher',
                    '非法外键',
                    'teacher-synthetic',
                    datetime('now','localtime'),
                    ?
                )
                """,
                (current_release_id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO knowledge_relations (
                    relation_id, source_key, target_key, relation_type,
                    status, source_kind, rationale,
                    decision_by, decided_at, graph_release_id
                ) VALUES (
                    'kr_reverse_active',
                    'kp_alg_equation_properties',
                    'kp_alg_real_numbers',
                    'parent',
                    'confirmed',
                    'teacher',
                    '非法反向活动边',
                    'teacher-synthetic',
                    datetime('now','localtime'),
                    ?
                )
                """,
                (current_release_id,),
            )
