"""Explicit, scoped mastery preparation. Never starts a model or an application."""
from __future__ import annotations

import argparse
import dataclasses
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("BEGIN")
    return connection


def write_new(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)


def prepare(args: argparse.Namespace) -> dict[str, object]:
    from db_manager import DBManager
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.mastery.current import (
        CURRENT_MASTERY_PARAMETERS, CurrentMastery, CurrentMasteryCalculator,
    )
    from question_bank.mastery.v2 import compute_mastery_v2
    from session_manager import iter_effective_rubric_item_refs

    output = Path(args.output).resolve()
    backup = Path(args.backup).resolve()
    if (output / "exam_baseline.json").exists() or backup.exists():
        raise ValueError("Existing baseline/backup must be preserved; choose a new output")
    data = Path(args.data_root).resolve()
    if not output.is_relative_to(data / "reports"):
        raise ValueError("Comparison output must use the existing reports root")
    if not backup.is_relative_to(data / "backups"):
        raise ValueError("Backup must use the existing backups root")
    output.mkdir(parents=True, exist_ok=True)
    with read_only(data / "databases/question_bank.db") as qb, read_only(
        data / "databases/grading_system.db"
    ) as grading:
        sessions = grading.execute(
            "SELECT id,session_name,created_at FROM grading_sessions "
            "WHERE is_deleted=0 AND session_name=?", (args.exam_title,),
        ).fetchall()
        if len(sessions) != 1:
            raise ValueError("Exam title must identify exactly one active exam")
        session = dict(sessions[0])
        session_id = int(session["id"])
        db = DBManager(data / "databases/grading_system.db", external_connection=grading)
        rubric = db._load_session_rubric(session_id)
        questions = [dict(row) for row in qb.execute(
            "SELECT q.* FROM questions q WHERE q.is_deleted=0 AND EXISTS("
            "SELECT 1 FROM question_tags t WHERE t.question_id=q.id "
            "AND t.tag_type='exam_scope' AND t.tag_value=?) ORDER BY q.id",
            (args.chapter,),
        )]
        chapter_ids = {row["id"] for row in questions}
        essays = [row for row in questions if row["question_type"] == "解答题"]
        if len(essays) != args.expected_essays:
            raise ValueError("Essay scope changed; inventory must be reconciled first")
        links = {row["source_question_id"]: row["bank_question_id"] for row in qb.execute(
            "SELECT source_question_id,bank_question_id FROM grading_question_links "
            "WHERE grading_session_id=? AND status='confirmed'", (str(session_id),),
        ) if row["bank_question_id"] in chapter_ids}
        selected_rubric = {**rubric, "questions": [
            {k: v for k, v in question.items() if k != "question_image_base64"}
            for question in rubric["questions"] if question["question_id"] in links
        ]}
        items = list(iter_effective_rubric_item_refs(selected_rubric))
        parent = {item_ref: parent_ref for item_ref, parent_ref, _, _ in items}
        maximum = {ref: float(item.get("part_score", question.get("max_score", 0)))
                   for ref, _, question, item in items}
        marks = ",".join("?" for _ in parent)
        rows = [dict(row) for row in grading.execute(
            f"""SELECT sr.session_id,sr.id result_id,sr.student_id,s.class_name,
                       sd.id detail_id,sd.question_id,sd.score_awarded,sd.deduction_reason,
                       sd.error_category,sd.error_summary,sd.ai_score_awarded,
                       sr.needs_human_review,sr.graded_at
                FROM session_details sd JOIN session_results sr ON sr.id=sd.result_id
                JOIN students s ON s.id=sr.student_id
                WHERE sr.session_id=? AND sd.question_id IN ({marks})
                ORDER BY sr.student_id,sd.question_id""", [session_id, *parent],
        )]
        ids = sorted(set(links.values()))
        qm = ",".join("?" for _ in ids)
        tags: dict[int, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
        for row in qb.execute(
            f"SELECT question_id,tag_type,tag_value FROM question_tags WHERE question_id IN ({qm}) ORDER BY id", ids,
        ):
            tags[row[0]][row[1]].append(row[2])
        metadata = {}
        for row in grading.execute("SELECT id,raw_json FROM session_results WHERE session_id=?", (session_id,)):
            raw = json.loads(row["raw_json"] or "{}")
            metadata[row["id"]] = {k: v for k, v in raw.get("detail_metadata", {}).items() if k in parent}
        locks = [dict(row) for row in grading.execute(
            f"SELECT student_id,question_id,score_awarded,max_score,revision FROM teacher_score_locks "
            f"WHERE session_id=? AND question_id IN ({marks})", [session_id, *parent],
        )]
        by_student: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
        for row in rows:
            row["bank_question_id"] = links[parent[row["question_id"]]]
            row["full_score"] = maximum[row["question_id"]]
            row["metadata"] = metadata.get(row["result_id"], {}).get(row["question_id"], {})
            reference = {k: row[k] for k in ("session_id", "question_id", "bank_question_id", "score_awarded", "full_score")}
            for point in tags[row["bank_question_id"]]["knowledge_point"]:
                by_student[str(row["student_id"])][point].append(reference)
        profile = {"_mastery_session_times": {str(session_id): session["created_at"]}, "students": [
            {"student_id": student, "weak_points": [
                {"knowledge_point": point, "source_question_refs": references}
                for point, references in points.items()
            ]} for student, points in by_student.items()
        ]}
        as_of = datetime.now(UTC)
        resolver = CurrentKnowledgeResolver.from_connection(qb)
        calculator = CurrentMasteryCalculator(data / "databases/question_bank.db", resolver, clock=lambda: as_of)
        baseline = {}
        for (student, key), evidence in calculator._exam_evidence(profile).items():
            value = compute_mastery_v2(stable_key=key, as_of=as_of, exam_evidence=tuple(evidence), parameters=CURRENT_MASTERY_PARAMETERS)
            node = resolver.node(key)
            baseline[student, key] = CurrentMastery(
                key, node.display_name, value.status.value, value.value, value.direct_evidence_count,
                value.effective_sample_weight, value.parameter_version,
                exam_evidence_count=value.direct_evidence_count,
                evidence_contributions=tuple((v.evidence_id, v.effective_weight, v.weighted_value)
                                             for v in value.contributions if v.included),
                direct_evidence_count=value.direct_evidence_count,
            )
        baseline = calculator._with_parent_rollups(baseline)
        snapshot = {
            "schema": "mastery-refinement-exam-input-v1", "as_of": as_of.isoformat(),
            "session": session, "question_links": links, "rubric": selected_rubric,
            "rows": rows, "teacher_locks": locks, "old_profile": profile,
            "old_parameters": dataclasses.asdict(CURRENT_MASTERY_PARAMETERS),
            "old_mastery": [{"student_id": student, **dataclasses.asdict(value)}
                            for (student, _), value in baseline.items()],
            "essay_scope": [{k: row[k] for k in ("id", "question_type", "difficulty")} for row in essays],
        }
        write_new(output / "exam_baseline.json", snapshot)
        corpus = []
        for question in essays:
            versions = [dict(row) for row in qb.execute(
                "SELECT * FROM question_solution_evidence_versions WHERE question_id=? "
                "ORDER BY created_at DESC,evidence_version_id DESC", (question["id"],),
            )]
            corpus.append({"question": question, "evidence_versions": [
                {**{k: v for k, v in version.items() if k != "evidence_json"}, "evidence": json.loads(version["evidence_json"])} for version in versions
            ]})
        write_new(output / "question_inputs.json", corpus)
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as destination:
            qb.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Question-bank backup did not pass SQLite quick_check")
        return {"session_id": session_id, "students": len(by_student), "score_items": len(parent),
                "score_rows": len(rows), "essays": len(essays), "backup_bytes": backup.stat().st_size,
                "baseline_saved": True, "as_of": as_of.isoformat()}


def prepare_bank(args: argparse.Namespace) -> dict[str, object]:
    """Prepare explicitly inventoried bank questions without opening student data."""
    from question_bank.solution_evidence.part_assessments import current_inputs, source_alias, load_profiles
    data, output, backup = (Path(p).resolve() for p in (args.data_root, args.output, args.backup))
    if not output.is_relative_to(data / "reports") or not backup.is_relative_to(data / "backups"):
        raise ValueError("Use the existing reports and backups roots")
    if backup.exists() or (output / "bank_scope.json").exists() or (output / "question_inputs.json").exists():
        raise ValueError("Existing preparation must be preserved")
    path = data / "databases/question_bank.db"
    with read_only(path) as connection:
        questions = [dict(r) for r in connection.execute(
            "SELECT q.* FROM questions q WHERE q.is_deleted=0 AND q.question_type='解答题' AND EXISTS("
            "SELECT 1 FROM question_tags t WHERE t.question_id=q.id AND t.tag_type='exam_scope' "
            "AND t.tag_value LIKE ? AND t.tag_value!=?) ORDER BY q.id", (args.chapter_prefix + "%", args.exclude_chapter or ""))]
        profiles = load_profiles(path, [q["id"] for q in questions], connection=connection)
        retained = [q["id"] for q in questions if profiles.get(q["id"], {}).get("available")]
        selected = [q for q in questions if q["id"] not in retained]
        if len(selected) != args.expected_essays:
            raise ValueError("Bank scope changed; reconcile the inventory")
        inputs = current_inputs(path, [q["id"] for q in selected], connection)
        corpus, incompatible = [], []
        for question in selected:
            versions = [dict(r) for r in connection.execute(
                "SELECT * FROM question_solution_evidence_versions WHERE question_id=? ORDER BY created_at DESC,evidence_version_id DESC", (question["id"],))]
            compatible = next((v for v in versions if v["status"] in {"proposed", "approved"}
                               and source_alias(inputs[question["id"]], v["source_content_hash"]) is not None), None)
            if compatible:
                versions.remove(compatible)
                versions.insert(0, compatible)
            else:
                incompatible.append(question["id"])
            corpus.append({"question": question, "evidence_versions": [
                {**{k: v for k, v in version.items() if k != "evidence_json"}, "evidence": json.loads(version["evidence_json"])} for version in versions]})
        output.mkdir(parents=True, exist_ok=True)
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(backup) as destination:
            connection.backup(destination)
            if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Backup integrity check failed")
        write_new(output / "bank_scope.json", {
            "schema": "mastery-refinement-bank-input-v1", "chapter_prefix": args.chapter_prefix,
            "exclude_chapter": args.exclude_chapter, "retained_profile_ids": retained,
            "essay_scope": [{k: q[k] for k in ("id", "question_type", "difficulty")} for q in selected],
            "incompatible_source_ids": incompatible, "backup": str(backup),
        })
        write_new(output / "question_inputs.json", corpus)
    return {"questions": len(selected), "retained": len(retained), "incompatible_sources": len(incompatible),
            "backup_bytes": backup.stat().st_size}


def compare(data: Path, output: Path, *, report_name: str = "exam_comparison.json") -> dict[str, object]:
    from integration.question_tag_projection_service import QuestionTagProjectionService
    from question_bank.current_knowledge import CurrentKnowledgeResolver
    from question_bank.mastery.current import CURRENT_MASTERY_PARAMETERS, CurrentMastery, CurrentMasteryCalculator
    from question_bank.mastery.v2 import compute_mastery_v2
    snapshot = json.loads((output / "exam_baseline.json").read_text(encoding="utf-8"))
    integrity = verify_exam_snapshot(data, snapshot)
    as_of = datetime.fromisoformat(snapshot["as_of"])
    path = data / "databases/question_bank.db"
    with read_only(path) as connection:
        projection = QuestionTagProjectionService(path, external_connection=connection).project_session(
            grading_session_id=snapshot["session"]["id"], rubric=snapshot["rubric"])
        by_item = {p.item_ref: p for p in projection.items}
        locks = {(str(p["student_id"]), p["question_id"]): p for p in snapshot["teacher_locks"]}
        by_student = defaultdict(lambda: defaultdict(list))
        for row in snapshot["rows"]:
            part = by_item[row["question_id"]]
            if not part.is_graph_eligible:
                continue
            reference = {k: row[k] for k in ("session_id", "question_id", "bank_question_id", "score_awarded", "full_score")}
            lock = locks.get((str(row["student_id"]), row["question_id"]))
            if lock:
                reference["score_awarded"] = lock["score_awarded"]
                reference["full_score"] = lock["max_score"]
            from question_bank.solution_evidence.part_assessments import exam_assessment_state
            reference["assessment"] = exam_assessment_state(part.assessment, row.get("metadata") or {},
                teacher_final=lock is not None, teacher_score=float(lock["score_awarded"]) if lock else None)
            for target in part.tags.get("knowledge_point", []):
                by_student[str(row["student_id"])][target].append(reference)
        profile = {"_mastery_session_times": snapshot["old_profile"]["_mastery_session_times"], "students": [
            {"student_id": student, "weak_points": [{"knowledge_point": target, "source_question_refs": refs} for target, refs in values.items()]}
            for student, values in by_student.items()]}
        resolver = CurrentKnowledgeResolver.from_connection(connection)
        calculator = CurrentMasteryCalculator(path, resolver, clock=lambda: as_of)
        values = {}
        for (student, key), evidence in calculator._exam_evidence(profile).items():
            result = compute_mastery_v2(stable_key=key, as_of=as_of, exam_evidence=tuple(evidence), parameters=CURRENT_MASTERY_PARAMETERS)
            values[student, key] = CurrentMastery(
                key, resolver.node(key).display_name, result.status.value, result.value, result.direct_evidence_count,
                result.effective_sample_weight, result.parameter_version, exam_evidence_count=result.direct_evidence_count,
                evidence_contributions=tuple((v.evidence_id, v.effective_weight, v.weighted_value) for v in result.contributions if v.included),
                direct_evidence_count=result.direct_evidence_count)
        values = calculator._with_parent_rollups(values)
        old = {(v["student_id"], v["stable_key"]): v for v in snapshot["old_mastery"]}
        differences = []
        for student, key in sorted(set(old) | set(values)):
            before = old.get((student, key), {}).get("value")
            current = values.get((student, key))
            after = current.value if current else None
            if before != after:
                differences.append({"student_id": student, "stable_key": key, "knowledge": resolver.node(key).display_name,
                                    "before": before, "after": after, "change": None if before is None or after is None else round(after-before,6)})
        report = {"as_of": snapshot["as_of"], "integrity": integrity, "parameters": dataclasses.asdict(CURRENT_MASTERY_PARAMETERS),
                  "projection": [dataclasses.asdict(p) for p in projection.items], "profile": profile,
                  "mastery": [{"student_id": student, **dataclasses.asdict(v)} for (student, _), v in values.items()],
                  "differences": differences}
        write_new(output / report_name, report)
        return {"students": len(by_student), "refined_items": sum(p.assessment.get("granularity")=="part" and p.is_graph_eligible for p in projection.items),
                "missing_items": projection.missing_items, "changed_student_knowledge_pairs": len(differences),
                "changed_students": len({d["student_id"] for d in differences}),
                "newly_observed_pairs": sum(d["before"] is None for d in differences),
                "no_longer_attributable_pairs": sum(d["after"] is None for d in differences)}


def verify_exam_snapshot(data: Path, snapshot: dict) -> dict[str, object]:
    """Compare only authorized exam rows, locks and selected rubric with the baseline."""
    from db_manager import DBManager
    ids = sorted({r["question_id"] for r in snapshot["rows"]})
    marks = ",".join("?" for _ in ids)
    sid = snapshot["session"]["id"]
    with read_only(data / "databases/grading_system.db") as connection:
        rows = [dict(r) for r in connection.execute(f"""SELECT sd.id detail_id,sd.question_id,sd.score_awarded,
            sd.ai_score_awarded,sd.deduction_reason,sd.error_category,sd.error_summary,sr.student_id,sr.needs_human_review,sr.graded_at
            FROM session_details sd JOIN session_results sr ON sr.id=sd.result_id
            WHERE sr.session_id=? AND sd.question_id IN ({marks}) ORDER BY sd.id""", [sid,*ids])]
        fields = tuple(rows[0]) if rows else ()
        expected = [{k:r[k] for k in fields} for r in sorted(snapshot["rows"],key=lambda r:r["detail_id"])]
        if rows != expected:
            raise ValueError("Exam facts changed since baseline; fixed-input comparison requires reconciliation")
        locks = [dict(r) for r in connection.execute(f"SELECT student_id,question_id,score_awarded,max_score,revision FROM teacher_score_locks WHERE session_id=? AND question_id IN ({marks}) ORDER BY student_id,question_id", [sid,*ids])]
        if locks != sorted(snapshot["teacher_locks"],key=lambda r:(r["student_id"],r["question_id"])):
            raise ValueError("Teacher final decisions changed since baseline")
        rubric = DBManager(data / "databases/grading_system.db",external_connection=connection)._load_session_rubric(sid)
        selected = [{k:v for k,v in q.items() if k!="question_image_base64"} for q in rubric["questions"] if q["question_id"] in snapshot["question_links"]]
        if selected != snapshot["rubric"]["questions"]:
            raise ValueError("Exam marking standard changed since baseline")
    return {"score_rows_unchanged":len(rows),"teacher_locks_unchanged":len(locks),"selected_rubric_unchanged":True}


def import_profiles(data: Path, output: Path) -> dict[str, object]:
    """Apply a reviewed scoped manifest; each immutable write can be resumed."""
    from question_bank.solution_evidence.part_assessments import save_profile, current_inputs, source_alias
    from question_bank.solution_evidence.contracts import QuestionSolutionEvidence, CoreResolution
    from question_bank.solution_evidence.repository import SolutionEvidenceRepository, _model_evidence_payload
    from question_bank.training_criteria.analysis import solution_evidence_source_content_hash
    from question_bank.solution_evidence.part_assessments import model_part_estimates
    scope_file = output / "bank_scope.json" if (output / "bank_scope.json").exists() else output / "exam_baseline.json"
    snapshot = json.loads(scope_file.read_text(encoding="utf-8"))
    corpus = json.loads((output / "question_inputs.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "reviewed_part_profiles.json").read_text(encoding="utf-8"))
    expected = {int(q["id"]) for q in snapshot["essay_scope"]}
    if {int(q) for q in manifest} != expected or {q["question"]["id"] for q in corpus} != expected:
        raise ValueError("Reviewed manifest must match the authorized baseline scope")
    path = data / "databases/question_bank.db"
    prepared = []
    with read_only(path) as connection:
        inputs = current_inputs(path, sorted(expected), connection)
        class ExistingIdentityResolver:
            def resolve(self, key):
                row = connection.execute("SELECT status FROM knowledge_tag_identities WHERE stable_key=?", (key,)).fetchone()
                if row is None or row["status"] != "active":
                    raise ValueError("Backfill requires an existing active knowledge identity")
                return CoreResolution(status="resolved", stable_keys=(key,), reason="reviewed_existing_identity")
        for item in corpus:
            question_id = item["question"]["id"]
            source = item["evidence_versions"][0]
            version = connection.execute("SELECT * FROM question_solution_evidence_versions WHERE evidence_version_id=? AND question_id=?", (source["evidence_version_id"], question_id)).fetchone()
            entry = manifest[str(question_id)]
            current_hash = solution_evidence_source_content_hash(inputs[question_id])
            replacement = entry.get("replacement_evidence")
            if version is None or json.loads(version["evidence_json"]) != source["evidence"]:
                raise ValueError("Prepared evidence changed since inventory")
            if replacement is not None:
                if entry.get("reviewed_source_hash") != current_hash or not entry.get("replacement_reason"):
                    raise ValueError("Replacement requires a reviewed current source and reason")
            elif source_alias(inputs[question_id], source["source_content_hash"]) is None:
                raise ValueError("Question content no longer matches the authorized input")
            evidence = None
            if replacement is not None or entry.get("link_additions"):
                payload = _model_evidence_payload(replacement or source["evidence"])
                payload = {k: payload[k] for k in ("schema_version", "question_id", "parts", "auxiliary_rules", "rationale", "confidence")}
                points = {p["evidence_point_id"]: p for part in payload["parts"] for p in part["evidence_points"]}
                for addition in entry.get("link_additions", []):
                    point = points[addition["point_id"]]
                    link = {k: addition[k] for k in ("fine_term_id", "fine_term_name", "role")}
                    if link not in point["fine_term_links"]:
                        point["fine_term_links"].append(link)
                evidence = QuestionSolutionEvidence.from_model_dict(payload, question_id=question_id,
                    source_content_hash=current_hash if replacement is not None else source["source_content_hash"], resolver=ExistingIdentityResolver())
            model_part_estimates(entry["parts"], evidence.to_dict() if evidence is not None else source["evidence"])
            prepared.append((question_id, source, entry, evidence))
    receipts = []
    for question_id, source, entry, evidence in prepared:
        version_id = source["evidence_version_id"]
        if evidence is not None:
            version_id = SolutionEvidenceRepository(path).save(evidence, source_kind="backfill",
                source_reference=f"part-refinement:{manifest[str(question_id)]['review_id']}:{question_id}",
                created_by="codex_self", graph_release_id=source.get("graph_release_id"))
        receipts.append(save_profile(path, question_id=question_id, evidence_version_id=version_id,
                                     parts=entry["parts"], created_by="codex_self"))
    receipt_path = output / "profile_import_receipts.json"
    if not receipt_path.exists():
        write_new(receipt_path, receipts)
    return {"questions": len(receipts), "parts": sum(len(e[2]["parts"]) for e in prepared),
            "knowledge_backfilled_questions": sum(e[3] is not None for e in prepared),
            "unchanged": sum(r["unchanged"] for r in receipts)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "prepare-bank", "import-profiles", "compare"))
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--chapter")
    parser.add_argument("--chapter-prefix")
    parser.add_argument("--exclude-chapter")
    parser.add_argument("--exam-title")
    parser.add_argument("--expected-essays", type=int)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--report-name", default="exam_comparison.json")
    args = parser.parse_args()
    if args.command == "prepare":
        if not all((args.chapter, args.exam_title, args.expected_essays, args.backup)):
            parser.error("prepare requires --chapter, --exam-title, --expected-essays and --backup")
        result = prepare(args)
    elif args.command == "prepare-bank":
        if not all((args.chapter_prefix, args.expected_essays, args.backup)):
            parser.error("prepare-bank requires --chapter-prefix, --expected-essays and --backup")
        result = prepare_bank(args)
    elif args.command == "import-profiles":
        result = import_profiles(args.data_root, args.output)
    else:
        result = compare(args.data_root, args.output, report_name=args.report_name)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
