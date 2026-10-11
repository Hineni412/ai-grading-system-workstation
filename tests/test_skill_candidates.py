from __future__ import annotations

import json
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
    yield
    read_module._READ_RESULT_CACHE.clear()


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
        original_factory = conn.row_factory
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
        assert conn.row_factory is original_factory
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
    accepted = service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
    )
    assert accepted["suggestion"]["status"] == "accepted"
    # Repeating a handled decision is rejected; only pending candidates review.
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    with pytest.raises(SkillCandidateInvalid):
        service.review(
            str(keep_suggestion["suggestion_id"]),
            decision="reject",
            expected_revision=revision,
        )
    dismissed_key = keep_suggestion["gap_refs"][0]["gap_key"]
    preview = service.gap_preview(VOLUME_ID)
    assert preview["counts"]["dismissed"] == 1
    assert dismissed_key not in _ready_keys(preview)

    reopened = service.review(
        str(keep_suggestion["suggestion_id"]),
        decision="reopen",
        expected_revision=revision,
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
        )
    rejected = service.review(
        str(suggestion["suggestion_id"]),
        decision="reject",
        expected_revision=revision,
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
    )
    skill_id = str(accepted["result"]["approved_skill_id"])
    revision = int(service.list_candidates(VOLUME_ID)["revision"])
    # Same-volume merge is allowed.
    merged = service.review(
        str(pending[1]["suggestion_id"]),
        decision="accept",
        expected_revision=revision,
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
            },
        )
        assert review.status_code == 200, review.text
        assert review.json()["suggestion"]["status"] == "accepted"

        bad_revision = client.post(
            f"/api/question-bank/skill-candidates/{suggestions[1]['suggestion_id']}/review",
            json={
                "decision": "accept",
                "expected_revision": revision + 99,
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

        # Volumes without an active key-step vocabulary refuse candidate work
        # before any paid request can be planned.
        free_preview = client.get(
            "/api/question-bank/skill-gaps",
            params={"curriculum_volume_id": "bnu24-math-g9-upper"},
        )
        assert free_preview.status_code == 422, free_preview.text
        free_run = client.post(
            "/api/question-bank/skill-candidate-runs",
            json={
                "curriculum_volume_id": "bnu24-math-g9-upper",
                "fingerprint": "e" * 64,
                "client_request_token": "e" * 32,
            },
        )
        assert free_run.status_code == 422, free_run.text
        with pytest.raises(SkillCandidateInvalid, match="本册不使用关键步骤"):
            service.create_run(
                curriculum_volume_id="bnu24-math-g9-upper",
                fingerprint="e" * 64,
                request_token="f" * 32,
            )
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


def _automatic_type_bank(tmp_path):
    from tests.training.test_combined_question_analysis import _seed_questions
    db = tmp_path / "TEST-automatic-types.db"
    _seed_questions(db, 30)
    release = install_current_knowledge(db, taxonomy_revision=11)
    section = "kp_bnu24_math_g8_lower_1_1"
    with connect(db) as conn:
        conn.executemany("INSERT INTO papers(id,title,import_status) VALUES(?,?,'completed')",
                         [(2, 'TEST-paper-2'), (3, 'TEST-paper-3')])
        for qid in range(1, 31):
            conn.execute("UPDATE questions SET paper_id=? WHERE id=?", ((qid % 3) + 1, qid))
            conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(?,'curriculum_section',?,'ai')", (qid, section))
    _approve_automatic_type_evidence(db, tmp_path, tuple(range(1, 31)), release)
    return db, release


def _approve_automatic_type_evidence(db, data_root, question_ids, release):
    from tests.training.test_combined_question_analysis import _type_result
    from question_bank.training_criteria import QuestionAnalysisInputLoader, solution_evidence_source_content_hash
    inputs = QuestionAnalysisInputLoader(db_path=db, data_root=data_root).load(question_ids, curriculum_volume_id="bnu24-math-g8-lower")
    with connect(db) as conn:
        for question in inputs:
            evidence = _type_result()["solution_evidence"]
            evidence["question_id"] = question.question_id
            conn.execute("INSERT INTO question_solution_evidence_versions(evidence_version_id,question_id,source_content_hash,schema_version,content_hash,evidence_json,status,source_kind,source_reference,created_by,graph_release_id) VALUES(?,?,?,'question-solution-evidence-v2',?,?,'approved','combined_model','TEST','TEST',?)",
                (f"{question.question_id:064x}", question.question_id, solution_evidence_source_content_hash(question), "b"*64, json.dumps(evidence), release))


def _add_automatic_type_questions(db, data_root, questions, release, *, available=True):
    with connect(db) as conn:
        for qid, paper_id, section in questions:
            conn.execute("INSERT INTO questions(id,paper_id,question_number,question_type,question_text,answer_text) VALUES(?,?,?,'计算题',?,?)",
                (qid, paper_id, str(qid), f"TEST-并行新增题目 {qid}", f"TEST-并行新增答案 {qid}"))
            if section:
                conn.execute("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(?,'curriculum_section',?,'ai')", (qid, section))
    if available:
        _approve_automatic_type_evidence(db, data_root, tuple(row[0] for row in questions), release)


def _automatic_response(payload):
    refs = [row["question_ref"] for row in payload["questions"]]
    return {"types": [{"type_id": "new_sum", "name": "TEST-角关系推理", "definition": "TEST-用角关系推出结论",
        "include_scope": "TEST-内角与外角", "exclude_scope": "TEST-仅量角", "section_id": "kp_bnu24_math_g8_lower_1_1", "anchors": refs[:2]}],
        "assignments": [{"question_ref": ref, "primary_type_id": "new_sum", "secondary_type_ids": []} for ref in refs]}


def test_automatic_chapter_types_publish_without_teacher_steps_and_replay_without_requests(tmp_path):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.taxonomy.governance import TaxonomyGovernance
    from question_bank.question_types import chapter_target_kind
    db, old = _automatic_type_bank(tmp_path)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    assert plan["planned_requests"] == 1
    chapter = next(row for row in plan["chapters"] if row["planned_requests"])
    assert (chapter["question_count"], chapter["paper_count"]) == (30, 3)
    calls = []
    gateway = SimpleNamespace(organize_chapter_types=lambda payload: calls.append(payload) or _automatic_response(payload))
    context = SimpleNamespace(job_id=901, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    guards = []
    @contextmanager
    def guard():
        guards.append("entered")
        yield
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=lambda: gateway, publication_guard=guard)
    assert result["outcome"] == "published", result
    assert result["published_count"] == 30
    assert result["model_calls"] == 1
    assert len(calls) == 1 and guards == ["entered", "entered"]
    assert all("point_knowledge" in row for row in calls[0]["questions"])
    resolver = CurrentKnowledgeResolver.from_active_database(db)
    assert resolver.release_id != old
    assert chapter_target_kind(resolver, "bnu24-math-g8-lower", "kp_bnu24_math_g8_lower_1") == "type"
    assert chapter_target_kind(resolver, "bnu24-math-g8-lower", "kp_bnu24_math_g8_lower_2") == "knowledge"
    governance = TaxonomyGovernance(state_path=tmp_path / "TEST-auto-taxonomy.json", knowledge_graph_db_path=db)
    contract = governance.prompt_contract({"curriculum_volume_id": "bnu24-math-g8-lower", "question_text": "TEST-角关系"})
    assert contract["question_type_mode"] is True
    from question_bank.training_criteria.adapters import _prompt_candidate_contract
    from question_bank.knowledge_graph_release.loader import load_taxonomy_catalog_for_release
    from question_bank.knowledge_graph_release.repository import load_active_release
    active_catalog = load_taxonomy_catalog_for_release(load_active_release(db))
    type_key = "kp_bnu24_math_g8_lower_1_1_t01"
    anchors = ["题干：合成题目 1\n答案：合成答案 1", "题干：合成题目 2\n答案：合成答案 2"]
    assert next(row for row in active_catalog["terms"] if row["id"] == type_key)["anchors"] == anchors
    new_type = next(row for row in _prompt_candidate_contract(contract)["candidates"]["knowledge"] if row["id"] == type_key)
    assert all(anchor in new_type["observable_evidence"] for anchor in anchors)
    assert "q1" not in new_type["observable_evidence"] and "q2" not in new_type["observable_evidence"]
    assert all(row["definition"] for row in contract["candidates"]["ability"])
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE tag_type='knowledge_point' AND tag_value='kp_bnu24_math_g8_lower_1_1_t01'").fetchone()[0] == 30
        assert conn.execute("SELECT COUNT(DISTINCT question_id) FROM evidence_point_knowledge_links WHERE graph_release_id=? AND stable_key='kp_bnu24_math_g8_lower_1_1_t01'", (resolver.release_id,)).fetchone()[0] == 30
        assert conn.execute("SELECT COUNT(*) FROM question_solution_evidence_versions").fetchone()[0] == 30
    assert run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=lambda: gateway, publication_guard=guard) == result
    assert len(calls) == 1


@pytest.mark.parametrize("problem", ["missing", "small_type", "duplicate", "unknown"])
def test_automatic_chapter_type_rejection_keeps_active_release_and_never_retries(tmp_path, problem):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    from question_bank.knowledge_graph_release.repository import active_release_id
    db, old = _automatic_type_bank(tmp_path)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    calls = []
    def organize(payload):
        calls.append(payload)
        if problem == "unknown":
            raise TimeoutError("TEST-request-outcome-unknown")
        raw = _automatic_response(payload)
        if problem == "missing":
            raw["assignments"].pop()
        elif problem == "duplicate":
            raw["assignments"][1] = dict(raw["assignments"][0])
        else:
            raw["types"].append({**raw["types"][0], "type_id": "new_small", "name": "TEST-small", "anchors": [raw["assignments"][0]["question_ref"]]})
            raw["assignments"][0]["primary_type_id"] = "new_small"
        return raw
    context = SimpleNamespace(job_id=902, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    args = dict(context=context, question_bank_db_path=db, data_root=tmp_path,
                ai_service_factory=lambda: SimpleNamespace(organize_chapter_types=organize), publication_guard=nullcontext)
    first = run_chapter_type_job(**args)
    assert first["published_count"] == 0
    assert active_release_id(db) == old
    run_chapter_type_job(**args)
    assert len(calls) == 1


def test_automatic_publish_failure_rolls_back_standard_labels_and_copies(tmp_path, monkeypatch):
    from question_bank.services.chapter_type_service import (
        _chapter_inputs, preview_chapter_type_plan, chapter_model_payload, validate_chapter_result,
        build_chapter_release, publish_chapter_release)
    from question_bank.knowledge_graph_release.repository import load_active_release, active_release_id
    from question_bank.services import question_write_service
    db, old = _automatic_type_bank(tmp_path)
    base = load_active_release(db)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    volume, groups = _chapter_inputs(db, tmp_path, "bnu24-math-g8-lower")
    chapter = next(row for row in plan["chapters"] if row["planned_requests"])
    rows = groups[chapter["chapter_id"]]
    payload = chapter_model_payload(base, volume, chapter, rows)
    response = validate_chapter_result(_automatic_response(payload), payload)
    candidate, catalog, labels = build_chapter_release(base, [(chapter, rows, response)])
    def fail(*args, **kwargs):
        raise RuntimeError("TEST-failure-after-standard-activation")
    monkeypatch.setattr(question_write_service, "sync_question_type_labels", fail)
    with pytest.raises(RuntimeError, match="after-standard-activation"):
        publish_chapter_release(db_path=db, data_root=tmp_path, base_release_id=old,
            candidate=candidate, catalog=catalog, labels=labels)
    assert active_release_id(db) == old
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM knowledge_graph_releases WHERE release_id=?", (candidate.release_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM question_tags WHERE tag_value='kp_bnu24_math_g8_lower_1_1_t01'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM evidence_point_knowledge_links WHERE graph_release_id=?", (candidate.release_id,)).fetchone()[0] == 0

def test_automatic_chapter_plan_counts_distinct_questions_and_all_live_source_papers(tmp_path):
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    db, _ = _automatic_type_bank(tmp_path)
    with connect(db) as conn:
        conn.execute("UPDATE questions SET paper_id=1")
        conn.executemany("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES(?,1,'1')", [(2,), (3,)])
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    chapter = next(row for row in plan["chapters"] if row["chapter_id"] == "kp_bnu24_math_g8_lower_1")
    assert (chapter["question_count"], chapter["paper_count"], chapter["planned_requests"]) == (30, 3, 1)
    with connect(db) as conn:
        conn.execute("UPDATE papers SET deleted_at=datetime('now') WHERE id=3")
    after = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    assert after["planned_requests"] == 0
    chapter = next(row for row in after["chapters"] if row["chapter_id"] == "kp_bnu24_math_g8_lower_1")
    assert (chapter["question_count"], chapter["paper_count"]) == (30, 2)


def test_automatic_authorized_upper_bound_skips_request_if_import_has_no_usable_new_question(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    db, _ = _automatic_type_bank(tmp_path)
    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id=30")
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower", pending_question_count=1)
    assert plan["planned_requests"] == 1
    def forbidden():
        pytest.fail("upper-bound confirmation must not issue a request below actual threshold")
    context = SimpleNamespace(job_id=903, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=forbidden, publication_guard=nullcontext)
    assert result["outcome"] == "skipped" and result["model_calls"] == 0


def test_automatic_authorization_changed_chapter_list_is_rejected_before_model(tmp_path):
    from question_bank.services.chapter_type_service import authorized_chapter_inputs, preview_chapter_type_plan, ChapterTypeInvalid
    db, _ = _automatic_type_bank(tmp_path)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower")
    plan["chapters"][0]["label"] = "TEST-altered-confirmation"
    with pytest.raises(ChapterTypeInvalid, match="快照"):
        authorized_chapter_inputs(db_path=db, data_root=tmp_path, authorization={**plan, "confirmed": True, "request_limit": 1})

def test_automatic_chapter_plan_keeps_historical_skill_standard_without_requests(tmp_path, monkeypatch):
    from question_bank.services import chapter_type_service as service
    from question_bank.knowledge_graph_release.loader import load_release_for_taxonomy_revision
    monkeypatch.setattr(service, "load_active_release", lambda db: load_release_for_taxonomy_revision(10))
    def forbidden(*args, **kwargs):
        pytest.fail("historical skill-only standards must not enter automatic type grouping")
    monkeypatch.setattr(service, "_chapter_inputs", forbidden)
    plan = service.preview_chapter_type_plan(db_path=tmp_path / "TEST-historical.db", data_root=tmp_path,
        volume_id="bnu24-math-g8-lower", pending_question_count=40)
    assert plan["planned_requests"] == 0 and plan["model_calls"] == 0 and plan["chapters"] == []
    with pytest.raises(service.ChapterTypeInvalid, match="历史技能标准"):
        service.authorized_chapter_inputs(db_path=tmp_path / "TEST-historical.db", data_root=tmp_path,
            authorization={**plan, "confirmed": True, "request_limit": 0})

@pytest.mark.parametrize("change,pending_count,message", [
    ("extra_question", 0, "待导入题数"),
    ("extra_source_paper", 0, "来源试卷"),
    ("chapter_question_overflow", 1, "可用题目"),
    ("chapter_source_overflow", 1, "来源试卷"),
    ("whole_volume_budget", 1, "待导入题数"),
    ("known_content", 0, "内容已变化"),
    ("legacy_source_paper", 0, "来源试卷"),
])
def test_automatic_authorization_rejects_parallel_scope_growth_before_model(tmp_path, change, pending_count, message):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan, ChapterTypeInvalid, _hash
    from question_bank.knowledge_graph_release.repository import active_release_id
    db, old = _automatic_type_bank(tmp_path)
    section = "kp_bnu24_math_g8_lower_1_1"
    pending_ids = (31,) if change == "chapter_question_overflow" else ()
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower",
        question_ids=pending_ids, pending_question_count=pending_count)
    if change == "legacy_source_paper":
        for chapter in plan["chapters"]:
            chapter.pop("possible_question_count")
            chapter.pop("possible_paper_count")
        plan.pop("input_fingerprint")
        plan["input_fingerprint"] = _hash(plan)
        from question_bank.services.chapter_type_service import authorized_chapter_inputs
        _, _, ready = authorized_chapter_inputs(db_path=db, data_root=tmp_path,
            authorization={**plan, "confirmed": True, "request_limit": 1})
        assert len(ready) == 1 and len(ready[0][1]) == 30
    with connect(db) as conn:
        if change in {"extra_source_paper", "chapter_source_overflow", "legacy_source_paper"}:
            conn.execute("INSERT INTO papers(id,title,import_status) VALUES(4,'TEST-parallel-paper-4','completed')")
        if change in {"extra_source_paper", "legacy_source_paper"}:
            conn.execute("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES(4,1,'TEST-1')")
        if change == "chapter_source_overflow":
            conn.execute("INSERT INTO papers(id,title,import_status) VALUES(5,'TEST-parallel-paper-5','completed')")
        if change == "known_content":
            conn.execute("UPDATE questions SET question_text='TEST-changed-confirmed-content' WHERE id=1")
    if change == "extra_question":
        _add_automatic_type_questions(db, tmp_path, [(31, 1, section)], old)
    elif change == "chapter_question_overflow":
        _add_automatic_type_questions(db, tmp_path, [(31, 1, section), (32, 1, section)], old)
    elif change == "chapter_source_overflow":
        _add_automatic_type_questions(db, tmp_path, [(31, 4, section)], old)
        with connect(db) as conn:
            conn.execute("INSERT INTO paper_question_occurrences(paper_id,question_id,question_number) VALUES(5,31,'TEST-31')")
    elif change == "whole_volume_budget":
        _add_automatic_type_questions(db, tmp_path, [(31, 1, section), (32, 1, "kp_bnu24_math_g8_lower_2_1")], old)
    calls = []
    gateway = SimpleNamespace(organize_chapter_types=lambda payload: calls.append(payload) or _automatic_response(payload))
    context = SimpleNamespace(job_id=904, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    with pytest.raises(ChapterTypeInvalid, match=message):
        run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
            ai_service_factory=lambda: gateway, publication_guard=nullcontext)
    assert calls == []
    assert active_release_id(db) == old


def test_automatic_authorization_allows_only_confirmed_unknown_question_budget(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    db, old = _automatic_type_bank(tmp_path)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower", pending_question_count=1)
    with connect(db) as conn:
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(4,'TEST-confirmed-new-paper','completed')")
    _add_automatic_type_questions(db, tmp_path, [(31, 4, "kp_bnu24_math_g8_lower_1_1")], old)
    calls = []
    gateway = SimpleNamespace(organize_chapter_types=lambda payload: calls.append(payload) or _automatic_response(payload))
    context = SimpleNamespace(job_id=905, payload={"authorization": {**plan, "confirmed": True, "request_limit": 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=lambda: gateway, publication_guard=nullcontext)
    assert result["outcome"] == "published" and result["model_calls"] == 1
    assert len(calls) == 1 and len(calls[0]["questions"]) == 31


@pytest.mark.parametrize("pending_paper,expected_requests", [(1, 0), (4, 1)])
def test_automatic_preview_counts_unlocated_pending_questions_and_distinct_source_papers(tmp_path, pending_paper, expected_requests):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.services.chapter_type_service import preview_chapter_type_plan
    from question_bank.knowledge_graph_release.repository import active_release_id
    db, old = _automatic_type_bank(tmp_path)
    with connect(db) as conn:
        conn.execute("UPDATE questions SET is_deleted=1 WHERE id>=28")
        conn.execute("UPDATE questions SET paper_id=(id % 2)+1 WHERE id<=27")
        conn.execute("INSERT INTO papers(id,title,import_status) VALUES(4,'TEST-pending-third-paper','completed')")
    pending_ids = (31, 32, 33)
    _add_automatic_type_questions(db, tmp_path, [(qid, pending_paper, None) for qid in pending_ids], old, available=False)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower", question_ids=pending_ids)
    chapter = next(row for row in plan["chapters"] if row["chapter_id"] == "kp_bnu24_math_g8_lower_1")
    assert (chapter["question_count"], chapter["paper_count"]) == (27, 2)
    assert (chapter["possible_question_count"], chapter["possible_paper_count"]) == (30, 2 + expected_requests)
    assert plan["planned_requests"] == expected_requests
    assert all(str(qid) in plan["source_question_hashes"] for qid in pending_ids)
    with connect(db) as conn:
        conn.executemany("INSERT INTO question_tags(question_id,tag_type,tag_value,source) VALUES(?,'curriculum_section','kp_bnu24_math_g8_lower_1_1','ai')", [(qid,) for qid in pending_ids])
    _approve_automatic_type_evidence(db, tmp_path, pending_ids, old)
    after = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower", question_ids=pending_ids)
    after_chapter = next(row for row in after["chapters"] if row["chapter_id"] == chapter["chapter_id"])
    assert after_chapter["possible_question_count"] == 30
    calls = []
    gateway = SimpleNamespace(organize_chapter_types=lambda payload: calls.append(payload) or _automatic_response(payload))
    context = SimpleNamespace(job_id=906, payload={"authorization": {**plan, "confirmed": True, "request_limit": expected_requests}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=lambda: gateway, publication_guard=nullcontext)
    assert result["model_calls"] == expected_requests and len(calls) == expected_requests
    if expected_requests:
        assert result["outcome"] == "published" and result["published_count"] == 30
        assert {row["question_ref"] for row in calls[0]["questions"]} == {f"q{qid}" for qid in (*range(1, 28), *pending_ids)}
        assert active_release_id(db) != old
    else:
        assert result["outcome"] == "skipped" and active_release_id(db) == old


def test_automatic_authorization_rejects_changed_unlocated_pending_content(tmp_path):
    from question_bank.services.chapter_type_service import preview_chapter_type_plan, authorized_chapter_inputs, ChapterTypeInvalid
    db, old = _automatic_type_bank(tmp_path)
    _add_automatic_type_questions(db, tmp_path, [(31, 1, None)], old, available=False)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id="bnu24-math-g8-lower", question_ids=(31,))
    with connect(db) as conn:
        conn.execute("UPDATE questions SET question_text='TEST-changed-unlocated-pending' WHERE id=31")
    with pytest.raises(ChapterTypeInvalid, match="内容已变化"):
        authorized_chapter_inputs(db_path=db, data_root=tmp_path, authorization={**plan, "confirmed": True, "request_limit": 1})


def test_automatic_publish_rejects_newly_usable_chapter_question_after_model(tmp_path):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from backend.jobs.chapter_types import run_chapter_type_job
    from question_bank.knowledge_graph_release.repository import active_release_id
    from question_bank.services.chapter_type_service import preview_chapter_type_plan

    db, old = _automatic_type_bank(tmp_path)
    _add_automatic_type_questions(db, tmp_path, [(31, 3, 'kp_bnu24_math_g8_lower_1_1')], old, available=False)
    plan = preview_chapter_type_plan(db_path=db, data_root=tmp_path, volume_id='bnu24-math-g8-lower')
    calls = []
    def organize(payload):
        calls.append(payload)
        _approve_automatic_type_evidence(db, tmp_path, [31], old)
        return _automatic_response(payload)
    context = SimpleNamespace(job_id=1909,
        payload={'authorization': {**plan, 'confirmed': True, 'request_limit': 1}},
        raise_if_cancelled=lambda: None, report=lambda *args: None)
    result = run_chapter_type_job(context=context, question_bank_db_path=db, data_root=tmp_path,
        ai_service_factory=lambda: SimpleNamespace(organize_chapter_types=organize), publication_guard=nullcontext)
    assert result['outcome'] == 'failed' and result['published_count'] == 0
    assert len(calls) == 1 and result['model_calls'] == 1
    assert '可用待归类题已变化' in result['reason']
    assert active_release_id(db) == old
    with connect(db) as connection:
        from question_bank.question_types import is_type_key
        tags = connection.execute("SELECT tag_value FROM question_tags WHERE tag_type='knowledge_point'").fetchall()
        assert not any(is_type_key(row[0]) for row in tags)
        assert connection.execute("SELECT COUNT(*) FROM knowledge_graph_releases WHERE status='candidate'").fetchone()[0] == 0


