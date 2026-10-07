from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

import pytest

from question_bank.database.schema import connect, initialize_database
from question_bank.services import question_read_service as read_module
from question_bank.services.question_read_service import QuestionBankReadService
from question_bank.services.skill_candidates import (
    SkillCandidateInvalid,
    SkillCandidateRequestConflict,
    SkillCandidateRevisionConflict,
    SkillCandidateService,
    SkillCandidateStale,
)
from question_bank.solution_evidence.knowledge_links import (
    link_points_to_skill,
    load_point_links,
)
from tests.current_knowledge_support import install_current_knowledge

VOLUME_ID = "bnu24-math-g8-upper"
CHAPTER = "kp_bnu24_math_g8_upper_1"
SECTION = "kp_bnu24_math_g8_upper_1_1"
CHAPTER_B = "kp_bnu24_math_g8_upper_2"
SECTION_B = "kp_bnu24_math_g8_upper_2_1"
OLD_RELEASE = "kgr_TEST_old_v1"


@pytest.fixture(autouse=True)
def _clear_read_result_cache():
    read_module._READ_RESULT_CACHE.clear()
    read_module._SKILL_SOURCE_MEMO.clear()
    yield
    read_module._READ_RESULT_CACHE.clear()
    read_module._SKILL_SOURCE_MEMO.clear()


def _skill_keys(db: Path, prefix: str = "sk_bnu24_math_g8_upper_1_1_"):
    with sqlite3.connect(db) as conn:
        release = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT stable_key FROM knowledge_graph_node_profiles "
            "WHERE release_id=? AND status='active' AND stable_key LIKE ? "
            "ORDER BY stable_key",
            (release, f"{prefix}%"),
        ).fetchall()
    return str(release), [str(row[0]) for row in rows]


def _seed_gap_bank(tmp_path: Path):
    """One paper with questions whose links only exist under an old release."""
    from question_bank.taxonomy.curriculum_catalog import curriculum_volume
    from question_bank.training_criteria import (
        QuestionAnalysisInputLoader,
        solution_evidence_source_content_hash,
    )

    db = tmp_path / "TEST-skill-candidates.db"
    initialize_database(db)
    install_current_knowledge(db, taxonomy_revision=7)
    volume = curriculum_volume(volume_id=VOLUME_ID)
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO papers(id,title,grade,semester,textbook_version,import_status) "
            "VALUES(1,'TEST-合成卷',?,?,?,'success')",
            (volume["grade"], volume["semester"], volume["textbook_version"]),
        )
        conn.executemany(
            "INSERT INTO questions(id,paper_id,question_number,question_text,"
            "question_type,difficulty) VALUES(?,1,?,'TEST-合成题','解答题',5)",
            [(1, "1"), (2, "2"), (3, "3")],
        )
        conn.execute(
            "INSERT INTO knowledge_graph_releases(release_id,schema_version,"
            "taxonomy_revision,content_hash,payload_json,status,source_reference,"
            "created_by,activated_at) "
            "VALUES(?,'knowledge-graph-release-v1',6,?,'{}','retired','TEST','TEST',"
            "'2026-09-01 00:00:00')",
            (OLD_RELEASE, "a" * 64),
        )
        conn.execute(
            "INSERT INTO knowledge_graph_release_events(release_id,event_type,"
            "actor_ref,reason,resulting_revision) VALUES(?,'activated','TEST',?,1)",
            (OLD_RELEASE, "TEST-旧标准启用原因"),
        )
    release, skill_keys = _skill_keys(db)
    inputs = QuestionAnalysisInputLoader(db_path=db, data_root=tmp_path).load([1, 2, 3])
    hashes = {
        item.question_id: solution_evidence_source_content_hash(item)
        for item in inputs
    }
    versions: dict[int, str] = {}
    with connect(db) as conn:
        for qid, points in (
            (1, ["p1", "p2", "p3"]),
            (2, ["p1"]),
            (3, ["p1"]),
        ):
            version = f"{qid:064x}"
            versions[qid] = version
            payload = {
                "parts": [
                    {
                        "part_id": "part-1",
                        "evidence_points": [
                            {
                                "evidence_point_id": point,
                                "target": f"TEST-目标-{qid}-{point}",
                                "observable_evidence": "TEST-可观察表现",
                            }
                            for point in points
                        ],
                    }
                ]
            }
            conn.execute(
                "INSERT INTO question_solution_evidence_versions("
                "evidence_version_id,question_id,source_content_hash,schema_version,"
                "content_hash,evidence_json,status,source_kind,source_reference,"
                "created_by,graph_release_id) "
                "VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved',"
                "'combined_model','TEST','TEST',?)",
                (
                    version,
                    qid,
                    hashes[qid],
                    version,
                    json.dumps(payload),
                    release,
                ),
            )
        # Question 1: p1 already has a skill link, p2/p3 only sit on a section.
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,source_kind) "
            "VALUES(?,1,'part-1','p1',?,'direct',?,?,'resolved','link_job')",
            (versions[1], OLD_RELEASE, skill_keys[0], skill_keys[0]),
        )
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,source_kind) "
            "VALUES(?,1,'part-1','p2',?,'direct',?,?,'resolved','migrated_from_embedded')",
            (versions[1], OLD_RELEASE, SECTION, SECTION),
        )
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,weight,source_kind) "
            "VALUES(?,1,'part-1','p2',?,'supporting_prerequisite',?,?,'resolved',"
            "0.5,'migrated_from_embedded')",
            (versions[1], OLD_RELEASE, SECTION, SECTION),
        )
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,source_kind) "
            "VALUES(?,1,'part-1','p3',?,'direct',?,?,'resolved','migrated_from_embedded')",
            (versions[1], OLD_RELEASE, SECTION, SECTION),
        )
        # Question 2: a gap in a different chapter so a run gets two batches.
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,source_kind) "
            "VALUES(?,2,'part-1','p1',?,'direct',?,?,'resolved','migrated_from_embedded')",
            (versions[2], OLD_RELEASE, SECTION_B, SECTION_B),
        )
        # Question 3: teacher-protected version must never become a gap.
        conn.execute(
            "INSERT INTO evidence_point_knowledge_links(evidence_version_id,"
            "question_id,part_id,evidence_point_id,graph_release_id,role,term_id,"
            "stable_key,resolution_status,source_kind) "
            "VALUES(?,3,'part-1','p1',?,'direct',?,?,'resolved','teacher')",
            (versions[3], OLD_RELEASE, SECTION, SECTION),
        )
    read_service = QuestionBankReadService(db, data_root=tmp_path)
    service = SkillCandidateService(
        state_path=tmp_path / "taxonomy-state.skill_candidates.json",
        db_path=db,
        read_service=read_service,
    )
    return service, read_service, db, release, skill_keys, versions


class FakeGateway:
    def __init__(self, responses=None, *, fail_chapters=()):
        self.responses = dict(responses or {})
        self.fail_chapters = set(fail_chapters)
        self.calls: list[dict] = []

    def suggest_skill_candidates(self, payload):
        self.calls.append(dict(payload))
        chapter = str(payload["chapter"]["chapter_key"])
        if chapter in self.fail_chapters:
            raise RuntimeError("TEST-gateway-failure")
        return dict(self.responses.get(chapter, {"groups": []}))


def _ready_keys(preview: dict) -> list[str]:
    return [str(item["gap_key"]) for item in preview["items"]]


def test_gap_projection_uses_older_release_group_and_respects_teacher(
    tmp_path: Path,
) -> None:
    service, _read, _db, release, _keys, versions = _seed_gap_bank(tmp_path)
    preview = service.gap_preview(VOLUME_ID)
    assert preview["graph_release_id"] == release
    assert preview["model_calls"] == 0
    ready = _ready_keys(preview)
    # p1 carries a direct skill link under the older release group, so only
    # p2 and p3 are gaps; the teacher-protected version is fully excluded.
    assert ready == [
        f"{versions[1]}:p2",
        f"{versions[1]}:p3",
        f"{versions[2]}:p1",
    ]
    first = preview["items"][0]
    assert first["section_key"] == SECTION
    assert first["chapter_key"] == CHAPTER
    assert first["question_id"] == 1
    assert preview["counts"] == {
        "total": 3,
        "ready": 3,
        "pending_review": 0,
        "approved_new": 0,
        "dismissed": 0,
        "unlocated": 0,
    }
    assert preview["planned_requests"] == 2
    assert [batch["chapter_key"] for batch in preview["batches"]] == [
        CHAPTER,
        CHAPTER_B,
    ]
    assert preview["fingerprint"]

    summary = _read.standard_summary(VOLUME_ID)
    assert summary["curriculum_volume_id"] == VOLUME_ID
    assert summary["graph_release_id"] == release
    assert summary["active_release"]["release_id"] == release
    assert summary["active_release"]["label"] == "v5"
    assert summary["skill_count"] > 0
    assert summary["question_count"] == 3
    assert summary["unlinked_question_count"] == 2
    assert summary["no_usable_evidence_count"] == 0
    assert summary["gap_point_count"] == 3
    assert summary["unlocated_gap_point_count"] == 0
    assert summary["model_calls"] == 0
    labels = [item["label"] for item in summary["versions"]]
    assert labels[0] == "v5"
    assert "v1" in labels
    old = next(
        item for item in summary["versions"] if item["release_id"] == OLD_RELEASE
    )
    assert old["status"] == "retired"
    assert old["reason"] == "TEST-旧标准启用原因"

    with pytest.raises(ValueError):
        service.gap_preview("TEST-不存在的学期")


def test_link_points_to_skill_carries_forward_and_replaces_direct(
    tmp_path: Path,
) -> None:
    service, _read, db, release, skill_keys, versions = _seed_gap_bank(tmp_path)
    target = skill_keys[1]
    conn = sqlite3.connect(db)
    try:
        conn.execute("BEGIN IMMEDIATE")
        inserted = link_points_to_skill(
            conn,
            db_path=db,
            question_id=1,
            evidence_version_id=versions[1],
            graph_release_id=release,
            point_skills={"p2": target},
            part_ids={"p2": "part-1"},
            source_reference="skill_candidate:TEST-suggestion",
        )
        # The writer must not leave a custom row_factory on the connection.
        assert conn.row_factory is None
        assert isinstance(conn.execute("SELECT 1").fetchone(), tuple)
        conn.commit()
    finally:
        conn.close()
    assert inserted > 0
    grouped = load_point_links(db, [versions[1]], release)
    points = grouped[versions[1]]
    p1_direct = [
        link
        for link in points["p1"]
        if link.role == "direct" and link.resolution_status == "resolved"
    ]
    assert [link.stable_key for link in p1_direct] == [skill_keys[0]]
    assert all(link.graph_release_id == release for link in points["p1"])
    p2 = points["p2"]
    p2_direct = [
        link
        for link in p2
        if link.role == "direct" and link.resolution_status == "resolved"
    ]
    assert len(p2_direct) == 1
    assert p2_direct[0].stable_key == target
    assert p2_direct[0].weight == 1.0
    assert not any(link.stable_key == SECTION for link in p2_direct)
    assert any(link.role == "supporting_prerequisite" for link in p2)
    # p3 stays on its carried-forward section link, untouched.
    p3_direct = [
        link
        for link in points["p3"]
        if link.role == "direct" and link.resolution_status == "resolved"
    ]
    assert [link.stable_key for link in p3_direct] == [SECTION]

    # The linked gap leaves the preview; the other gaps stay ready.
    preview = service.gap_preview(VOLUME_ID)
    assert _ready_keys(preview) == [
        f"{versions[1]}:p3",
        f"{versions[2]}:p1",
    ]
    assert preview["counts"]["total"] == 2


def _create_run(service: SkillCandidateService, token: str = "a" * 32) -> dict:
    preview = service.gap_preview(VOLUME_ID)
    return service.create_run(
        curriculum_volume_id=VOLUME_ID,
        fingerprint=str(preview["fingerprint"]),
        request_token=token,
    )


def test_run_validates_groups_and_keeps_uncovered_ready(tmp_path: Path) -> None:
    service, _read, db, release, skill_keys, versions = _seed_gap_bank(tmp_path)
    preview = service.gap_preview(VOLUME_ID)
    fingerprint = str(preview["fingerprint"])
    with pytest.raises(SkillCandidateRevisionConflict):
        service.create_run(
            curriculum_volume_id=VOLUME_ID, fingerprint="0" * 64, request_token="b" * 32
        )
    run = service.create_run(
        curriculum_volume_id=VOLUME_ID, fingerprint=fingerprint, request_token="a" * 32
    )
    assert run["status"] == "queued"
    assert len(run["batches"]) == 2
    assert service.get_run(str(run["run_id"]))["run_id"] == run["run_id"]

    gateway = FakeGateway(
        {
            CHAPTER: {
                "groups": [
                    {
                        "gap_ids": ["g1", "g99"],
                        "decision": "link_existing",
                        "skill_key": skill_keys[0],
                        "new_skill": {
                            "section_key": "",
                            "name": "",
                            "include": "",
                            "exclude": "",
                            "examples": [],
                        },
                        "reason": "TEST-属于现有技能",
                    },
                    {
                        "gap_ids": ["g1"],
                        "decision": "new_skill",
                        "skill_key": "",
                        "new_skill": {
                            "section_key": SECTION,
                            "name": "重复占用",
                            "include": "x",
                            "exclude": "x",
                            "examples": ["x"],
                        },
                        "reason": "TEST-重复应丢弃",
                    },
                    {
                        "gap_ids": ["g2"],
                        "decision": "link_existing",
                        "skill_key": "sk_TEST_不存在的技能",
                        "new_skill": {
                            "section_key": "",
                            "name": "",
                            "include": "",
                            "exclude": "",
                            "examples": [],
                        },
                        "reason": "TEST-目标无效",
                    },
                    {
                        "gap_ids": ["g2"],
                        "decision": "new_skill",
                        "skill_key": "",
                        "new_skill": {
                            "section_key": "kp_TEST_不存在的小节",
                            "name": "无效小节",
                            "include": "x",
                            "exclude": "x",
                            "examples": ["x"],
                        },
                        "reason": "TEST-小节无效",
                    },
                ]
            },
            CHAPTER_B: {
                "groups": [
                    {
                        "gap_ids": ["g1"],
                        "decision": "keep_section",
                        "skill_key": "",
                        "new_skill": {
                            "section_key": "",
                            "name": "",
                            "include": "",
                            "exclude": "",
                            "examples": [],
                        },
                        "reason": "TEST-留在小节",
                    }
                ]
            },
        }
    )
    service.process_run(str(run["run_id"]), gateway)
    finished = service.get_run(str(run["run_id"]))
    assert finished["status"] == "completed"
    assert finished["progress"]["completed"] == 2
    # Batch 1: unknown id dropped, duplicate/invalid groups discarded, the
    # uncovered g3 counts. Batch 2: keep_section covers its single gap.
    assert finished["batches"][0]["uncovered_count"] == 1
    assert len(gateway.calls) == 2
    payload = next(
        call
        for call in gateway.calls
        if call["chapter"]["chapter_key"] == CHAPTER
    )
    assert payload["task"].startswith("Group the skill-gap")
    assert len(payload["existing_skills"]) > 0
    assert len(payload["sections"]) > 0
    assert len(payload["gap_points"]) == 2

    listing = service.list_candidates(VOLUME_ID)
    assert len(listing["suggestions"]) == 2
    decisions = {
        (item["chapter_key"], item["decision"])
        for item in listing["suggestions"]
    }
    assert (CHAPTER, "link_existing") in decisions
    assert (CHAPTER_B, "keep_section") in decisions
    covered = listing["suggestions"][0]["gap_refs"] + listing["suggestions"][1][
        "gap_refs"
    ]
    assert {item["gap_key"] for item in covered} == {
        f"{versions[1]}:p2",
        f"{versions[2]}:p1",
    }
    after = service.gap_preview(VOLUME_ID)
    assert after["counts"]["pending_review"] == 2
    assert after["counts"]["ready"] == 1
    # Request-token idempotency: same token + payload replays the stored run.
    replay = service.create_run(
        curriculum_volume_id=VOLUME_ID, fingerprint=fingerprint, request_token="a" * 32
    )
    assert replay["run_id"] == run["run_id"]
    with pytest.raises(SkillCandidateRequestConflict):
        service.create_run(
            curriculum_volume_id=VOLUME_ID,
            fingerprint="0" * 64,
            request_token="a" * 32,
        )


def test_failed_batch_retries_only_itself_and_run_can_be_cancelled(
    tmp_path: Path,
) -> None:
    service, _read, _db, _release, _keys, _versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    gateway = FakeGateway({CHAPTER_B: {"groups": []}}, fail_chapters={CHAPTER})
    service.process_run(str(run["run_id"]), gateway)
    finished = service.get_run(str(run["run_id"]))
    assert finished["status"] == "partial"
    assert finished["progress"]["failed"] == 1
    assert finished["batches"][0]["status"] == "failed"
    assert finished["batches"][0]["error"]["category"] == "model_gateway"

    gateway.calls.clear()
    gateway.fail_chapters.clear()
    service.retry_failed(str(run["run_id"]), gateway)
    retried = service.get_run(str(run["run_id"]))
    assert retried["status"] == "completed"
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["chapter"]["chapter_key"] == CHAPTER

    second = _create_run(service, token="c" * 32)
    assert service.cancel_run(str(second["run_id"]))["status"] == "cancelled"
    assert service.get_run(str(second["run_id"]))["status"] == "cancelled"


def test_release_change_makes_run_stale_without_calling_gateway(
    tmp_path: Path,
) -> None:
    service, _read, db, _release, _keys, _versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE knowledge_graph_releases SET status='retired' "
            "WHERE status='active'"
        )
        conn.execute(
            "INSERT INTO knowledge_graph_releases(release_id,schema_version,"
            "taxonomy_revision,content_hash,payload_json,status,source_reference,"
            "created_by,activated_by,activated_at) "
            "VALUES('kgr_TEST_new_v2','knowledge-graph-release-v1',7,?,'{}',"
            "'active','TEST','TEST','TEST','2026-10-01 00:00:00')",
            ("b" * 64,),
        )
    gateway = FakeGateway()
    service.process_run(str(run["run_id"]), gateway)
    assert service.get_run(str(run["run_id"]))["status"] == "stale"
    assert gateway.calls == []


def _process_with_groups(service, run_id, groups_by_chapter):
    gateway = FakeGateway(
        {chapter: {"groups": groups} for chapter, groups in groups_by_chapter.items()}
    )
    service.process_run(run_id, gateway)
    return service.list_candidates(VOLUME_ID)


def _new_skill_group(gap_ids, *, section=SECTION, name="计算平方根"):
    return {
        "gap_ids": list(gap_ids),
        "decision": "new_skill",
        "skill_key": "",
        "new_skill": {
            "section_key": section,
            "name": name,
            "include": "TEST-纳入条件",
            "exclude": "TEST-排除条件",
            "examples": ["TEST-表现一", "TEST-表现二"],
        },
        "reason": "TEST-新技能",
    }


def _suggestion(listing, decision):
    return next(
        item for item in listing["suggestions"] if item["decision"] == decision
    )


def test_accept_new_skill_assigns_sequential_ids(tmp_path: Path) -> None:
    service, _read, db, release, _keys, _versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    listing = _process_with_groups(
        service,
        str(run["run_id"]),
        {
            CHAPTER: [_new_skill_group(["g1"]), _new_skill_group(["g2"])],
            CHAPTER_B: [_new_skill_group(["g1"], section=SECTION_B)],
        },
    )
    skills = {
        item["suggestion_id"]: item
        for item in listing["suggestions"]
        if item["decision"] == "new_skill"
    }
    assert len(skills) == 3
    revision = int(listing["revision"])
    accepted: list[str] = []
    skill_by_suggestion: dict[str, str] = {}
    for index, suggestion_id in enumerate(skills):
        result = service.review(
            suggestion_id,
            decision="accept",
            expected_revision=revision,
            request_token=f"{index + 1:032d}",
        )
        revision = int(service.list_candidates(VOLUME_ID)["revision"])
        skill_id = str(result["result"]["approved_skill_id"])
        accepted.append(skill_id)
        skill_by_suggestion[suggestion_id] = skill_id

    prefix = SECTION.removeprefix("kp_")
    existing = _skill_keys(db, prefix=f"sk_{prefix}_")[1]
    base = max(
        [100]
        + [
            int(key.rsplit("_", 1)[1])
            for key in existing
            if key.rsplit("_", 1)[1].isdigit()
        ]
    )
    same_section = sorted(
        skill_id for skill_id in accepted if skill_id.startswith(f"sk_{prefix}_")
    )
    assert len(same_section) == 2
    assert same_section[0] == f"sk_{prefix}_{base + 1}"
    assert same_section[1] == f"sk_{prefix}_{base + 2}"
    other = [s for s in accepted if s.startswith("sk_bnu24_math_g8_upper_2_1_")]
    assert len(other) == 1

    approved = service.list_candidates(VOLUME_ID)["approved_skills"]
    by_id = {item["skill_id"]: item for item in approved}
    assert all(not item["published"] for item in approved)
    assert by_id[same_section[0]]["name"] == "计算平方根"
    # Each accepted skill is unpublished; gaps move out of the ready list.
    preview = service.gap_preview(VOLUME_ID)
    assert preview["counts"]["approved_new"] == 3
    assert preview["counts"]["ready"] == 0

    # Reopen removes this suggestion's gaps from the approved skill; a skill
    # left with no gaps is deleted.
    first_suggestion = next(iter(skills))
    removed_skill = skill_by_suggestion[first_suggestion]
    reopened = service.review(
        first_suggestion,
        decision="reopen",
        expected_revision=revision,
        request_token="f" * 32,
    )
    assert reopened["suggestion"]["status"] == "pending"
    approved_after = service.list_candidates(VOLUME_ID)["approved_skills"]
    assert all(
        item["skill_id"] != removed_skill for item in approved_after
    )
    preview_after = service.gap_preview(VOLUME_ID)
    assert preview_after["counts"]["approved_new"] == 2
    assert preview_after["counts"]["pending_review"] == 1
    assert preview_after["counts"]["ready"] == 0


def test_review_link_existing_keep_section_and_subset(tmp_path: Path) -> None:
    service, _read, db, release, skill_keys, versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    keep_group = {
        "gap_ids": ["g1"],
        "decision": "keep_section",
        "skill_key": "",
        "new_skill": {
            "section_key": "",
            "name": "",
            "include": "",
            "exclude": "",
            "examples": [],
        },
        "reason": "TEST-留小节",
    }
    link_group = {
        "gap_ids": ["g1", "g2"],
        "decision": "link_existing",
        "skill_key": skill_keys[0],
        "new_skill": {
            "section_key": "",
            "name": "",
            "include": "",
            "exclude": "",
            "examples": [],
        },
        "reason": "TEST-挂现有",
    }
    listing = _process_with_groups(
        service,
        str(run["run_id"]),
        {CHAPTER: [link_group], CHAPTER_B: [keep_group]},
    )
    revision = int(listing["revision"])

    with pytest.raises(SkillCandidateRevisionConflict):
        service.review(
            listing["suggestions"][0]["suggestion_id"],
            decision="accept",
            expected_revision=revision + 99,
            request_token="9" * 32,
        )

    link_suggestion = _suggestion(
        service.list_candidates(VOLUME_ID), "link_existing"
    )
    link_gap_keys = [item["gap_key"] for item in link_suggestion["gap_refs"]]
    assert len(link_gap_keys) == 2
    first_ref = link_suggestion["gap_refs"][0]
    assert first_ref["question_number"] == "1"
    assert first_ref["paper_title"] == "TEST-合成卷"
    # Accept only a subset; the remainder returns to ready.
    subset = service.review(
        str(link_suggestion["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
        request_token="e" * 32,
        gap_keys=[link_gap_keys[0]],
    )
    assert subset["suggestion"]["status"] == "accepted"
    assert subset["result"]["linked"] == [link_gap_keys[0]]
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    preview = service.gap_preview(VOLUME_ID)
    assert link_gap_keys[0] not in _ready_keys(preview)
    assert link_gap_keys[1] in _ready_keys(preview)
    grouped = load_point_links(db, [versions[1]], release)
    linked_point = grouped[versions[1]][link_gap_keys[0].split(":", 1)[1]]
    direct = [
        link
        for link in linked_point
        if link.role == "direct" and link.resolution_status == "resolved"
    ]
    assert [link.stable_key for link in direct] == [skill_keys[0]]

    keep_suggestion = next(
        item
        for item in service.list_candidates(VOLUME_ID)["suggestions"]
        if item["decision"] == "keep_section"
        and item["chapter_key"] == CHAPTER_B
    )
    token = "d" * 32
    accepted = service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
        request_token=token,
    )
    assert accepted["suggestion"]["status"] == "accepted"
    # Same token + identical payload replays the stored result.
    replay = service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
        request_token=token,
    )
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    assert replay["suggestion"]["status"] == "accepted"
    with pytest.raises(SkillCandidateRequestConflict):
        service.review(
            str(keep_suggestion["suggestion_id"]),
            decision="reject",
            expected_revision=revision,
            request_token=token,
        )
    dismissed_key = keep_suggestion["gap_refs"][0]["gap_key"]
    preview = service.gap_preview(VOLUME_ID)
    assert preview["counts"]["dismissed"] == 1
    assert dismissed_key not in _ready_keys(preview)

    reopened = service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="reopen",
        expected_revision=revision,
        request_token="b" * 32,
    )
    assert reopened["suggestion"]["status"] == "pending"
    preview = service.gap_preview(VOLUME_ID)
    assert preview["counts"]["dismissed"] == 0
    assert preview["counts"]["pending_review"] == 1
    # Rejecting the reopened suggestion returns its gap to ready.
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="reject",
        expected_revision=revision,
        request_token="1" * 32,
    )
    preview = service.gap_preview(VOLUME_ID)
    assert dismissed_key in _ready_keys(preview)


def test_stale_suggestion_cannot_accept_new_skill(tmp_path: Path) -> None:
    service, _read, db, _release, _keys, _versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    listing = _process_with_groups(
        service,
        str(run["run_id"]),
        {CHAPTER: [_new_skill_group(["g1", "g2", "g3"])], CHAPTER_B: []},
    )
    suggestion = _suggestion(listing, "new_skill")
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE knowledge_graph_releases SET status='retired' "
            "WHERE status='active'"
        )
        conn.execute(
            "INSERT INTO knowledge_graph_releases(release_id,schema_version,"
            "taxonomy_revision,content_hash,payload_json,status,source_reference,"
            "created_by,activated_by,activated_at) "
            "VALUES('kgr_TEST_new_v2','knowledge-graph-release-v1',8,?,'{}',"
            "'active','TEST','TEST','TEST','2026-10-02 00:00:00')",
            ("c" * 64,),
        )
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    with pytest.raises(SkillCandidateStale):
        service.review(
            str(suggestion["suggestion_id"]),
            decision="accept",
            expected_revision=revision,
            request_token="8" * 32,
        )
    rejected = service.review(
        str(suggestion["suggestion_id"]),
        decision="reject",
        expected_revision=revision,
        request_token="7" * 32,
    )
    assert rejected["suggestion"]["status"] == "rejected"


def test_merge_into_approved_rejects_other_volumes(tmp_path: Path) -> None:
    service, _read, _db, _release, _keys, _versions = _seed_gap_bank(tmp_path)
    run = _create_run(service)
    listing = _process_with_groups(
        service,
        str(run["run_id"]),
        {
            CHAPTER: [_new_skill_group(["g1"]), _new_skill_group(["g2"])],
            CHAPTER_B: [],
        },
    )
    pending = [
        item
        for item in listing["suggestions"]
        if item["decision"] == "new_skill"
    ]
    assert len(pending) == 2
    revision = int(listing["revision"])
    accepted = service.review(
        str(pending[0]["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
        request_token="6" * 32,
    )
    skill_id = str(accepted["result"]["approved_skill_id"])
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    # Same-volume merge is allowed.
    merged = service.review(
        str(pending[1]["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
        request_token="5" * 32,
        edits={
            "kind": "merge_into_approved",
            "approved_skill_id": skill_id,
        },
    )
    assert merged["suggestion"]["status"] == "accepted"
    skill = service.list_candidates(VOLUME_ID)["approved_skills"][0]
    assert len(skill["gap_refs"]) == 2

    # A skill approved for another volume can never be a merge target.
    state = json.loads(service.state_path.read_text(encoding="utf-8"))
    state["approved_skills"][skill_id]["curriculum_volume_id"] = (
        "bnu24-math-g8-lower"
    )
    service.state_path.write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )
    third = _create_run(service, token="4" * 32)
    listing = _process_with_groups(
        service,
        str(third["run_id"]),
        {CHAPTER: [_new_skill_group(["g1"])], CHAPTER_B: []},
    )
    revision = int(listing["revision"])
    suggestion = _suggestion(listing, "new_skill")
    with pytest.raises(SkillCandidateInvalid):
        service.review(
            str(suggestion["suggestion_id"]),
            decision="accept",
            expected_revision=revision,
            request_token="3" * 32,
            edits={
                "kind": "merge_into_approved",
                "approved_skill_id": skill_id,
            },
        )


def test_api_skill_candidate_flow_and_job_idempotency(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.api.app import create_app
    from backend.api.dependencies import (
        get_job_manager,
        get_skill_candidate_service,
    )
    from backend.jobs.manager import JobManager
    from backend.jobs.store import JobStore

    service, _read, _db, _release, skill_keys, _versions = _seed_gap_bank(
        tmp_path
    )
    manager = JobManager(
        JobStore(tmp_path / "TEST-jobs.db"),
        max_workers=1,
        cleanup_interrupted=False,
    )
    keep = {
        "gap_ids": ["g1"],
        "decision": "keep_section",
        "skill_key": "",
        "new_skill": {
            "section_key": "",
            "name": "",
            "include": "",
            "exclude": "",
            "examples": [],
        },
        "reason": "TEST-留小节",
    }
    gateway = FakeGateway(
        {CHAPTER: {"groups": [keep]}, CHAPTER_B: {"groups": [keep]}}
    )

    def handler(context):
        def report(snapshot):
            progress = snapshot["progress"]
            context.report(
                int(progress["processed"]) / max(1, int(progress["total"])),
                "skill_candidate",
                "正在整理技能候选",
            )

        if context.payload["operation"] == "retry":
            run = service.retry_failed(
                context.payload["run_id"],
                gateway,
                progress_callback=report,
                cancel_requested=context.is_cancel_requested,
            )
        else:
            run = service.process_run(
                context.payload["run_id"],
                gateway,
                progress_callback=report,
                cancel_requested=context.is_cancel_requested,
            )
        return {
            "run_id": run["run_id"],
            "status": run["status"],
            "progress": run["progress"],
            "stale": run.get("stale", False),
        }

    manager.register("skill_candidate", handler)
    app = create_app()
    app.dependency_overrides[get_job_manager] = lambda: manager
    app.dependency_overrides[get_skill_candidate_service] = lambda: service
    client = TestClient(app)
    try:
        preview_response = client.get(
            "/api/question-bank/skill-gaps",
            params={"curriculum_volume_id": VOLUME_ID},
        )
        assert preview_response.status_code == 200
        preview = preview_response.json()
        assert preview["counts"]["ready"] == 3

        create_body = {
            "curriculum_volume_id": VOLUME_ID,
            "fingerprint": preview["fingerprint"],
            "client_request_token": "a" * 32,
        }
        first = client.post(
            "/api/question-bank/skill-candidate-runs", json=create_body
        )
        assert first.status_code == 202, first.text
        second = client.post(
            "/api/question-bank/skill-candidate-runs", json=create_body
        )
        assert second.status_code == 202, second.text
        assert second.json()["job"]["id"] == first.json()["job"]["id"]
        job_id = int(first.json()["job"]["id"])
        run_id = str(first.json()["run"]["run_id"])
        manager.wait(job_id, timeout=10)

        run_response = client.get(
            f"/api/question-bank/skill-candidate-runs/{run_id}"
        )
        assert run_response.status_code == 200, run_response.text
        assert run_response.json()["status"] == "completed"

        listing_response = client.get(
            "/api/question-bank/skill-candidates",
            params={"curriculum_volume_id": VOLUME_ID},
        )
        assert listing_response.status_code == 200, listing_response.text
        suggestions = listing_response.json()["suggestions"]
        assert len(suggestions) == 2

        summary_response = client.get(
            "/api/question-bank/skill-candidates/summary",
            params={"curriculum_volume_id": VOLUME_ID},
        )
        assert summary_response.status_code == 200
        assert summary_response.json()["pending_suggestion_count"] == 2
        # 3 个待整理判定点分布在 2 道题上。
        assert summary_response.json()["gap_question_count"] == 2

        revision = int(listing_response.json()["revision"])
        review = client.post(
            f"/api/question-bank/skill-candidates/{suggestions[0]['suggestion_id']}/review",
            json={
                "decision": "accept",
                "expected_revision": revision,
                "request_token": "b" * 32,
            },
        )
        assert review.status_code == 200, review.text
        assert review.json()["suggestion"]["status"] == "accepted"

        bad_revision = client.post(
            f"/api/question-bank/skill-candidates/{suggestions[1]['suggestion_id']}/review",
            json={
                "decision": "accept",
                "expected_revision": revision + 99,
                "request_token": "c" * 32,
            },
        )
        assert bad_revision.status_code == 409

        missing = client.get(
            "/api/question-bank/skill-candidate-runs/TEST-不存在的任务"
        )
        assert missing.status_code == 404

        # The generic jobs endpoint must refuse dedicated submissions.
        generic = client.post(
            "/api/jobs/skill_candidate",
            json={
                "payload": {
                    "run_id": "TEST-x",
                    "operation": "process",
                    "client_request_token": "d" * 32,
                }
            },
        )
        assert generic.status_code in {400, 404, 409, 422}
    finally:
        manager.shutdown()


def test_backup_members_include_skill_candidates() -> None:
    from types import SimpleNamespace

    from update_tools.backup_core import (
        taxonomy_backup_members,
        taxonomy_backup_target,
    )

    paths = SimpleNamespace(
        taxonomy_state_path=Path("config") / "taxonomy-governance" / "state.json"
    )
    members = taxonomy_backup_members(paths)
    key = "config/taxonomy-governance/skill_candidates.json"
    assert key in members
    assert members[key].name == "state.skill_candidates.json"
    assert taxonomy_backup_target(paths, key) == members[key]


NEW_SKILL_ID = "sk_bnu24_math_g8_upper_1_1_900"


def _release_skill(gap_refs: list[dict]) -> dict:
    return {
        "skill_id": NEW_SKILL_ID,
        "curriculum_volume_id": VOLUME_ID,
        "chapter_key": CHAPTER,
        "section_key": SECTION,
        "name": "TEST-新技能",
        "include": "TEST-纳入",
        "exclude": "TEST-排除",
        "examples": ["TEST-例一"],
        "gap_refs": gap_refs,
        "approved_at": "2026-01-01T00:00:00+00:00",
        "source_suggestion_ids": ["s1"],
    }


def _gap_ref(versions: dict, question: int = 1, point: str = "p2") -> dict:
    return {
        "gap_key": f"{question}:{point}",
        "question_id": question,
        "evidence_version_id": versions[question],
        "point_id": point,
        "part_id": "part-1",
        "target": f"TEST-目标-{question}-{point}",
    }


def _write_candidate_state(tmp_path: Path, skill: dict) -> Path:
    path = tmp_path / "release-state.skill_candidates.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "revision": 0,
                "approved_skills": {skill["skill_id"]: skill},
                "suggestions": {},
                "runs": {},
                "requests": {},
                "dismissed": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _candidate_payload(db: Path, state: Path, tmp_path: Path):
    from tools import build_skill_release as tool

    skills = tool.approved_unpublished_skills(db, state, VOLUME_ID)
    base_payload, base_vocab, bundled = tool.base_release(db)
    payload, vocab = tool.build_candidate_release(
        base_payload,
        base_vocab,
        skills,
        release_id="kgr_TEST_candidates_v6",
        taxonomy_revision=int(bundled.taxonomy_revision) + 1,
        source_id="teaching_skill_candidates_test",
        standard_ref="TEST-catalog",
    )
    return tool, skills, bundled, payload, vocab


def test_skill_release_dry_run_builds_release_with_parent_and_predecessor(
    tmp_path: Path,
) -> None:
    from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
    from question_bank.knowledge_graph_release.validation import validate_release

    _service, _read, db, release, _keys, versions = _seed_gap_bank(tmp_path)
    state = _write_candidate_state(
        tmp_path, _release_skill([_gap_ref(versions)])
    )
    tool, skills, bundled, payload, vocab = _candidate_payload(db, state, tmp_path)
    assert [s["skill_id"] for s in skills] == [NEW_SKILL_ID]
    report = validate_release(KnowledgeGraphRelease.from_mapping(payload), vocab)
    assert report.valid, [f"{i.path}: {i.message}" for i in report.errors]
    node = next(
        n for n in payload["core_nodes"] if n["stable_key"] == NEW_SKILL_ID
    )
    assert node["node_kind"] == "skill"
    assert payload["predecessor_release_id"] == release
    assert payload["taxonomy_revision"] == int(bundled.taxonomy_revision) + 1
    relation = next(
        r for r in payload["relations"] if r["source_key"] == NEW_SKILL_ID
    )
    assert relation["relation_type"] == "parent"
    assert relation["target_key"] == SECTION
    # Dry-run built nothing on disk and left the source DB untouched.
    assert not (tmp_path / "knowledge_graph_release_v6.json").exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0] == release


def _test_type_activation_candidate(tmp_path: Path):
    from tools import build_type_release as tool

    _service, _read, db, old_release, _skills, versions = _seed_gap_bank(tmp_path)
    base, vocabulary, bundled = tool.base_release(db)
    types = [
        {"type_id": f"T1-{number:02d}", "stable_key": f"{SECTION}_t{number:02d}",
         "section_key": SECTION, "name": f"TEST-题型{number}",
         "definition": "TEST-定义", "include": "TEST-纳入", "exclude": "TEST-排除",
         "rationale": "TEST-理由", "anchors": []}
        for number in (1, 2)
    ]
    payload, vocabulary, key_map = tool.build_type_release(
        base, vocabulary, types, release_id="kgr_TEST_type_activation_v9",
        taxonomy_revision=bundled.taxonomy_revision + 1,
    )
    labels = {1: {"primary_type": "T1-01", "secondary_types": ["T1-02"]},
              2: {"primary_type": "NONE", "secondary_types": []}}
    return tool, db, old_release, versions, payload, vocabulary, labels, key_map, types


def test_type_activation_rehearses_and_preserves_pre_activation_backup(tmp_path):
    tool, db, old, versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    backup_dir = tmp_path / "TEST-backups"
    result = tool.activate_type_release(db, backup_dir, payload, vocab, labels, keys)
    assert result["rehearsal_passed"] and result["applied"]
    assert result["integrity_ok"] and result["foreign_keys_ok"]
    assert result["model_calls"] == 0
    assert result["questions_with_type_link"] == 1
    assert result["type_links_written"] == 3
    assert result["secondary_tags_written"] == 1
    with sqlite3.connect(backup_dir / result["backup_name"]) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE tag_type='secondary_type'").fetchone()[0] == 0
    with sqlite3.connect(db) as conn:
        links = load_point_links(db, [versions[1]], payload["release_id"], connection=conn)
        assert set(links[versions[1]]) == {"p1", "p2", "p3"}
        for point in links[versions[1]].values():
            assert sum(row.role == "direct" and row.stable_key == keys["T1-01"] for row in point) == 1
            assert not any(row.stable_key == keys["T1-02"] for row in point)
        assert conn.execute("SELECT tag_value FROM question_tags WHERE question_id=1 AND tag_type='secondary_type'").fetchone()[0] == keys["T1-02"]
    assert not list(backup_dir.glob("TEST-type-release-*"))


def test_type_activation_link_failure_returns_to_old_release(tmp_path, monkeypatch):
    from question_bank.solution_evidence import knowledge_links

    tool, db, old, _versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    original = knowledge_links.replace_point_links

    def fail_real_write(conn, **kwargs):
        database = Path(conn.execute("PRAGMA database_list").fetchone()[2]).resolve()
        if database == db.resolve():
            raise RuntimeError("TEST-link-write-failed")
        return original(conn, **kwargs)

    monkeypatch.setattr(knowledge_links, "replace_point_links", fail_real_write)
    backup_dir = tmp_path / "TEST-backups"
    with pytest.raises(RuntimeError, match="TEST-link-write-failed"):
        tool.activate_type_release(db, backup_dir, payload, vocab, labels, keys)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old
        assert conn.execute("SELECT COUNT(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?", (payload["release_id"],)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE tag_type='secondary_type'").fetchone()[0] == 0
    assert len(list(backup_dir.glob("*.db"))) == 1


def test_type_activation_rejects_missing_version_during_rehearsal(tmp_path):
    tool, db, old, _versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    labels[999] = {"primary_type": "T1-01", "secondary_types": []}
    with pytest.raises(ValueError, match="缺少当前有效判定资料"):
        tool.activate_type_release(db, tmp_path / "TEST-backups", payload, vocab, labels, keys)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old
        assert conn.execute("SELECT COUNT(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?", (payload["release_id"],)).fetchone()[0] == 0


def test_type_activation_rejects_stale_source_before_creating_backup(tmp_path):
    tool, db, old, _versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-changed-source' WHERE id=1")
    backup_dir = tmp_path / "TEST-backups"
    with pytest.raises(ValueError, match="处理来源变化"):
        tool.activate_type_release(db, backup_dir, payload, vocab, labels, keys)
    assert not backup_dir.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old
        assert conn.execute("SELECT question_text FROM questions WHERE id=1").fetchone()[0] == "TEST-changed-source"


def test_type_activation_can_preserve_invalid_sources_without_type_annotations(tmp_path):
    from question_bank.solution_evidence.part_assessments import load_profiles

    tool, db, old, versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-stale-source' WHERE id=1")
        before = conn.execute("SELECT evidence_json, source_content_hash FROM question_solution_evidence_versions WHERE evidence_version_id=?", (versions[1],)).fetchone()
        old_links = load_point_links(db, [versions[1]], old, connection=conn)[versions[1]]
    result = tool.activate_type_release(db, tmp_path / "TEST-backups", payload, vocab, labels, keys,
                                        skip_unavailable=True)
    assert result["skipped_unavailable_questions"] == 1
    assert result["questions_with_type_link"] == result["type_links_written"] == 0
    assert result["secondary_tags_written"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT evidence_json, source_content_hash FROM question_solution_evidence_versions WHERE evidence_version_id=?", (versions[1],)).fetchone() == before
        assert conn.execute("SELECT question_text FROM questions WHERE id=1").fetchone()[0] == "TEST-stale-source"
        carried = load_point_links(db, [versions[1]], payload["release_id"], connection=conn)[versions[1]]
        assert {pid: {(row.role, row.stable_key) for row in rows} for pid, rows in carried.items()} == {pid: {(row.role, row.stable_key) for row in rows} for pid, rows in old_links.items()}
    assert not load_profiles(db, [1], verify_source=True, data_root=tmp_path)[1]["available"]


def test_type_activation_selects_latest_version_when_timestamps_tie(tmp_path):
    tool, db, old, versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    # A lower hash sorted lexically would select the older row. Insertion order
    # identifies the latest usable result, as in the shared profile reader.
    latest = "0" * 64
    evidence = json.dumps({"parts": [{"part_id": "part-1", "evidence_points": [{"evidence_point_id": "latest-point", "target": "TEST-current", "observable_evidence": "TEST-current"}]}]})
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO question_solution_evidence_versions
            (evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,
             status,source_kind,source_reference,created_by,graph_release_id,created_at)
            SELECT ?,question_id,source_content_hash,schema_version,?,?,status,source_kind,'TEST-newer-result',
                   created_by,graph_release_id,created_at FROM question_solution_evidence_versions
            WHERE evidence_version_id=?""", (latest, latest, evidence, versions[1]))
    result = tool.activate_type_release(db, tmp_path / "TEST-backups", payload, vocab, labels, keys)
    assert result["current_versions"][1] == latest
    with sqlite3.connect(db) as conn:
        links = load_point_links(db, [latest], payload["release_id"], connection=conn)[latest]
        assert set(links) == {"latest-point"}
        assert any(row.stable_key == keys["T1-01"] for row in links["latest-point"])


def test_type_activation_refuses_source_changed_during_rehearsal(tmp_path, monkeypatch):
    tool, db, old, _versions, payload, vocab, labels, keys, _types = _test_type_activation_candidate(tmp_path)
    original = tool.apply_type_release

    def concurrent_change(path, *args):
        result = original(path, *args)
        assert Path(path).resolve() != db.resolve()
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE questions SET question_text='TEST-concurrent-change' WHERE id=2")
        return result

    monkeypatch.setattr(tool, "apply_type_release", concurrent_change)
    with pytest.raises(ValueError, match="预演期间题库已变化"):
        tool.activate_type_release(db, tmp_path / "TEST-backups", payload, vocab, labels, keys)
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old
        assert conn.execute("SELECT question_text FROM questions WHERE id=2").fetchone()[0] == "TEST-concurrent-change"


@pytest.mark.parametrize("primary,secondary", [
    ("", ""), ("T1-01", "T1-02;T1-03;T1-04"),
    ("T1-01", "T1-02;T1-02"), ("T1-01", "T1-01"), ("NONE", "T1-02"),
])
def test_type_label_loader_rejects_invalid_primary_or_secondary(tmp_path, primary, secondary):
    from tools import build_type_release as tool

    (tmp_path / "labels_ch1_p01.tsv").write_text(
        f"question_id\tprimary_type\tsecondary_types\n1\t{primary}\t{secondary}\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        tool.load_labels(tmp_path)


@pytest.mark.parametrize("suffix", ["", "-wal", "-shm"])
def test_type_database_copy_refuses_existing_target_files(tmp_path, suffix):
    from tools import build_type_release as tool

    target = tmp_path / "TEST-existing.db"
    existing = Path(str(target) + suffix)
    existing.write_bytes(b"TEST-existing-content")
    with pytest.raises(ValueError, match="拒绝覆盖"):
        tool.copy_database(tmp_path / "TEST-source.db", target)
    assert existing.read_bytes() == b"TEST-existing-content"


def test_type_activation_cli_requires_backup_and_registered_release(tmp_path, monkeypatch):
    tool, db, old, _versions, _payload, _vocab, labels, _keys, types = _test_type_activation_candidate(tmp_path)
    with pytest.raises(SystemExit):
        tool.main(["--db", str(db), "--activate-and-carry-db", str(db)])
    monkeypatch.setattr(tool, "load_type_vocabulary", lambda _path: types)
    monkeypatch.setattr(tool, "load_labels", lambda _path: labels)

    def missing(_revision):
        raise FileNotFoundError("TEST-unregistered-release")

    monkeypatch.setattr(tool, "registered_release", missing)
    backup_dir = tmp_path / "TEST-backups"
    with pytest.raises(FileNotFoundError, match="TEST-unregistered-release"):
        tool.main(["--db", str(db), "--activate-and-carry-db", str(db), "--backup-dir", str(backup_dir)])
    assert not backup_dir.exists()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT release_id FROM knowledge_graph_releases WHERE status='active'").fetchone()[0] == old


def test_skill_release_apply_on_copy_links_gap_and_preserves_others(
    tmp_path: Path,
) -> None:
    _service, _read, db, release, _keys, versions = _seed_gap_bank(tmp_path)
    state = _write_candidate_state(
        tmp_path, _release_skill([_gap_ref(versions, point="p3")])
    )
    tool, skills, _bundled, payload, vocab = _candidate_payload(
        db, state, tmp_path
    )
    copy_path = tmp_path / "preview-copy.db"
    with sqlite3.connect(db) as src, sqlite3.connect(str(copy_path)) as dst:
        src.backup(dst)
    result = tool.apply_candidate_release(copy_path, payload, vocab, skills)
    assert result["new_release"] == "kgr_TEST_candidates_v6"
    assert result["gap_refs_linked"] == 1
    assert result["model_calls"] == 0
    with sqlite3.connect(copy_path) as conn:
        active = conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0]
        assert active == "kgr_TEST_candidates_v6"
        links = load_point_links(copy_path, [versions[1]], active, connection=conn)
    p3 = links[versions[1]]["p3"]
    assert any(
        link.role == "direct"
        and link.stable_key == NEW_SKILL_ID
        and link.resolution_status == "resolved"
        for link in p3
    )
    # p2's carried links survive under the new release group.
    assert any(
        link.stable_key == SECTION and link.resolution_status == "resolved"
        for link in links[versions[1]]["p2"]
    )
    # The source database is untouched: still on the old active release,
    # and no new-skill link rows exist in it.
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0] == release
        assert conn.execute(
            "SELECT COUNT(*) FROM evidence_point_knowledge_links "
            "WHERE stable_key=?",
            (NEW_SKILL_ID,),
        ).fetchone()[0] == 0


def test_skill_release_activation_refuses_without_registered_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _service, _read, db, release, _keys, versions = _seed_gap_bank(tmp_path)
    state = _write_candidate_state(tmp_path, _release_skill([_gap_ref(versions)]))
    tool, _skills, _bundled, _payload, _vocab = _candidate_payload(
        db, state, tmp_path
    )

    def _missing(_revision: int):
        raise FileNotFoundError("no catalog mapping for revision")

    monkeypatch.setattr(tool, "registered_release", _missing)
    backup_dir = tmp_path / "backups"
    with pytest.raises(FileNotFoundError):
        tool.main(
            [
                "--db", str(db),
                "--candidates-state", str(state),
                "--catalog-dir", str(tmp_path),
                "--activate-and-carry-db", str(db),
                "--backup-dir", str(backup_dir),
            ]
        )
    assert not list(backup_dir.glob("*.db")) if backup_dir.exists() else True
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0] == release


def test_type_release_builds_core_nodes_and_rejects_name_collision(
    tmp_path: Path,
) -> None:
    from question_bank.knowledge_graph_release.contracts import KnowledgeGraphRelease
    from question_bank.knowledge_graph_release.validation import validate_release
    from tools import build_type_release as tool

    _service, _read, db, release, _keys, _versions = _seed_gap_bank(tmp_path)
    base_payload, base_vocab, bundled = tool.base_release(db)
    vocab_dir = tmp_path / "vocab"
    vocab_dir.mkdir()
    header = (
        "type_id\tname\tsection_key\tdefinition\tinclude\texclude\tanchors\t"
        "source_leaves\treference_models\taction\tbasic_entry\test_count\t"
        "rationale\n"
    )
    (vocab_dir / "vocab_ch1.tsv").write_text(
        header
        + f"T1-01\tTEST-题型甲\t{SECTION}\tTEST-定义甲\tTEST-纳入甲\t"
        f"TEST-排除甲\tQ1;Q2\t{SECTION}_1\tK01\tnew\tno\t3\tTEST-理由甲\n"
        + f"T1-02\tTEST-题型乙\t{SECTION}\tTEST-定义乙\tTEST-纳入乙\t"
        f"TEST-排除乙\tQ3\t{SECTION}_2\tK02\tnew\tno\t2\tTEST-理由乙\n",
        encoding="utf-8",
    )
    types = tool.load_type_vocabulary(vocab_dir)
    payload, vocab, key_map = tool.build_type_release(
        base_payload,
        base_vocab,
        types,
        release_id="kgr_TEST_type_v9",
        taxonomy_revision=int(bundled.taxonomy_revision) + 1,
    )
    assert key_map == {
        "T1-01": f"{SECTION}_t01",
        "T1-02": f"{SECTION}_t02",
    }
    for key in key_map.values():
        assert re.fullmatch(r"kp_bnu24_math_g8_upper_1_1_t\d{2}", key)
    report = validate_release(KnowledgeGraphRelease.from_mapping(payload), vocab)
    assert report.valid, [f"{i.path}: {i.message}" for i in report.errors]
    section_name = next(
        n["display_name"]
        for n in base_payload["core_nodes"]
        if n["stable_key"] == SECTION
    )
    type_nodes = {
        n["stable_key"]: n
        for n in payload["core_nodes"]
        if n["stable_key"] in set(key_map.values())
    }
    assert len(type_nodes) == 2
    for key, node in type_nodes.items():
        assert node["node_kind"] == "core"
        assert node["status"] == "active"
        assert node["display_name"].startswith(f"{section_name}｜题型·")
        assert node["evidence_source_ids"] == ["question_type_vocab_2026_10"]
        relation = next(
            r
            for r in payload["relations"]
            if r["source_key"] == key and r["relation_type"] == "parent"
        )
        assert relation["target_key"] == SECTION
        assert relation["strength"] == "required"
        assert re.fullmatch(r"[0-9a-f]{64}", relation["relation_key"])
    # Nothing retired: every base node survives with its status unchanged.
    base_status = {
        n["stable_key"]: n["status"] for n in base_payload["core_nodes"]
    }
    assert len(payload["core_nodes"]) == len(base_status) + 2
    for node in payload["core_nodes"]:
        if node["stable_key"] in base_status:
            assert node["status"] == base_status[node["stable_key"]]
    # Vocabulary gained one approved knowledge term per type.
    new_terms = [t for t in vocab["terms"] if t["id"] in set(key_map.values())]
    assert len(new_terms) == 2
    assert vocab["revision"] == int(bundled.taxonomy_revision) + 1
    # Name collision (same type name in the same section) fails loudly.
    colliding = [dict(types[0]), dict(types[1], name=types[0]["name"])]
    with pytest.raises(ValueError, match="collides"):
        tool.build_type_release(
            base_payload,
            base_vocab,
            colliding,
            release_id="kgr_TEST_type_v9",
            taxonomy_revision=int(bundled.taxonomy_revision) + 1,
        )
    # The source database is untouched: still on the old active release.
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT release_id FROM knowledge_graph_releases WHERE status='active'"
        ).fetchone()[0] == release
