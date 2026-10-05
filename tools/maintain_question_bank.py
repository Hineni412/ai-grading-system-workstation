"""题库维护：只读预演，备份后执行，再核对。不调用模型。

errors --sessions 3 4 5：把仍匹配当前答卷的既有整理成果回挂题库。
standard --release PATH [--links PATH]：沿用未变化关联；变化部分须提供已整理的关联。
source-scores：只读预览题干分值的显示差异，不支持 --apply。
默认只预演；--apply 仅供已获得本次真实数据操作授权后使用。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import tempfile
from contextlib import closing
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def readonly(path: Path):
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def snapshot(source: Path, target: Path) -> None:
    with closing(readonly(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.backup(dst)


def verify_database(path: Path) -> dict[str, bool]:
    with closing(readonly(path)) as conn:
        result = {"integrity_ok": conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
                  "foreign_keys_ok": not conn.execute("PRAGMA foreign_key_check").fetchall()}
    if not all(result.values()):
        raise ValueError("数据库完整性或引用检查未通过，停止本次维护")
    return result


def pattern_snapshot(path: Path) -> dict[int, tuple]:
    with closing(readonly(path)) as conn:
        return {int(row[0]): tuple(row) for row in conn.execute("SELECT * FROM question_error_patterns")}


def reconcile_errors(bank: Path, grading: Path, data_root: Path, sessions: list[int]) -> dict:
    from backend.repositories.db_manager import DBManager
    from backend.class_analysis import (
        ClassAnalysisStateStore, assemble_cause_data, build_cause_inputs,
        cause_source_state, CAUSE_ANALYSIS_VERSION, CAUSE_PRE_STEP_VERSION,
    )
    from backend.error_patterns import session_bank_context, sync_session_patterns_to_bank

    before = pattern_snapshot(bank)
    store = ClassAnalysisStateStore(data_root / "reports")
    totals = []
    with closing(readonly(grading)) as conn:
        db = DBManager(grading, external_connection=conn)
        for sid in sessions:
            state = store.load(sid) or {}
            saved = ((state.get("cause_analysis") or {}).get("questions")) or {}
            data = assemble_cause_data(db, sid, data_root=data_root)
            sources = build_cause_inputs(data)
            valid = [source for source in sources if
                     (((saved.get(source["question_id"]) or {}).get("version") == CAUSE_ANALYSIS_VERSION
                       and cause_source_state(saved.get(source["question_id"]) or {}, source) == "fresh")
                      or ((saved.get(source["question_id"]) or {}).get("version") == CAUSE_PRE_STEP_VERSION
                          and cause_source_state(saved.get(source["question_id"]) or {}, source) in ("fresh", "pre_step")))]
            context = session_bank_context(bank, sid)
            written = sync_session_patterns_to_bank(store, sid, bank, context, current_sources=valid)
            totals.append({"session_id": sid, "saved_questions": len(saved),
                           "valid_questions": len(valid), "skipped_questions": len(saved) - len(valid),
                           "linked_parent_questions": len(context), "inserted_patterns": written})
    after = pattern_snapshot(bank)
    return {"sessions": totals, "inserted_patterns": len(after.keys() - before.keys()),
            "updated_patterns": sum(before[key] != after[key] for key in before.keys() & after.keys()),
            "model_calls": 0}


def standard_plan(bank: Path, release, *, data_root: Path | None = None) -> dict:
    """Compare existing immutable standards and authoritative point-link rows."""
    from question_bank.knowledge_graph_release.repository import preview_install
    from question_bank.solution_evidence.part_assessments import load_profiles
    from question_bank.solution_evidence.knowledge_links import load_point_links

    preview = preview_install(bank, release).to_dict()
    with closing(readonly(bank)) as conn:
        row = conn.execute("SELECT payload_json FROM knowledge_graph_releases WHERE status='active'").fetchone()
        old = json.loads(row[0]) if row else {}
        old_nodes = {item["stable_key"]: item for item in old.get("core_nodes", [])}
        new_nodes = {item["stable_key"]: item for item in release.payload.get("core_nodes", [])}
        changed = {key for key in old_nodes.keys() | new_nodes.keys() if old_nodes.get(key) != new_nodes.get(key)}
        # 新增或移动技能时，原来挂在该小节的题也需要重新判断。
        def relations(payload):
            return {(item["source_key"], item["target_key"], item["relation_type"]) for item in payload.get("relations", [])}
        for source, target, _kind in relations(old) ^ relations(release.payload):
            changed.update((source, target))
        def mappings(payload):
            return {(item["fine_term_id"], item["stable_key"]) for item in payload.get("mappings", [])}
        changed_terms = {term for term, _ in mappings(old) ^ mappings(release.payload)}
        old_terms = {item["fine_term_id"]: item for item in old.get("fine_term_dispositions", [])}
        new_terms = {item["fine_term_id"]: item for item in release.payload.get("fine_term_dispositions", [])}
        changed_terms.update(key for key in old_terms.keys() | new_terms.keys() if old_terms.get(key) != new_terms.get(key))
        ids = [int(row[0]) for row in conn.execute("SELECT id FROM questions WHERE is_deleted=0")]
        profiles = load_profiles(bank, ids, connection=conn, verify_source=data_root is not None, data_root=data_root)
        unavailable = sorted(qid for qid, profile in profiles.items() if not profile["available"])
        versions = {qid: p["evidence_version_id"] for qid, p in profiles.items()}
        links = load_point_links(bank, list(versions.values()), preview["current_release_id"], connection=conn)
        affected, reusable = [], []
        for qid, vid in versions.items():
            rows = [row for rows in links.get(vid, {}).values() for row in rows]
            if not rows or any(row.stable_key in changed or row.term_id in changed_terms for row in rows):
                affected.append(qid)
            else:
                reusable.append(qid)
        return {"preview": preview, "changed_key_count": len(changed),
                "affected_question_ids": sorted(affected), "reusable_question_ids": sorted(reusable),
                "versions": versions, "unavailable_question_ids": unavailable,
                "source_versions": {qid: p["current_source_content_hash"] for qid, p in profiles.items()},
                "model_calls": 0}


def install_standard(bank: Path, release, plan: dict, replacements: list[dict]) -> None:
    from question_bank.database.schema import connect
    from question_bank.knowledge_graph_release.repository import stage_release, activate_release
    from question_bank.solution_evidence.knowledge_links import load_point_links, replace_point_links
    from question_bank.services.question_write_service import refresh_derived_ownership_tags

    by_id = {int(item["question_id"]): item for item in replacements}
    affected = set(plan["affected_question_ids"])
    if set(by_id) != affected or len(by_id) != len(replacements):
        raise ValueError("关联文件必须恰好覆盖受影响题目，不能缺题、重复或改动无关题目")
    if not plan["preview"]["can_activate"]:
        raise ValueError("标准预演有阻断项，不能执行")
    if affected.intersection(plan.get("unavailable_question_ids", [])):
        raise ValueError("有判定点与当前题面不一致，请先处理这些题目后再换版")
    targets = {node["stable_key"] for node in release.payload["core_nodes"]}
    term_ids = {item["fine_term_id"] for item in release.payload.get("fine_term_dispositions", [])} | targets
    for qid, item in by_id.items():
        if item["evidence_version_id"] != plan["versions"][qid]:
            raise ValueError("判定点版本已变化，请重新整理关联")
        if not item.get("points") or any(not p.get("links") for p in item["points"]):
            raise ValueError("受影响题目的关联不能为空")
        if any(link["stable_key"] not in targets for p in item["points"] for link in p["links"]):
            raise ValueError("关联目标不属于候选标准")
        with closing(readonly(bank)) as conn:
            evidence = json.loads(conn.execute(
                "SELECT evidence_json FROM question_solution_evidence_versions WHERE evidence_version_id=?",
                (item["evidence_version_id"],)).fetchone()[0])
            expected = {(part["part_id"], point["evidence_point_id"]) for part in evidence["parts"]
                        for point in part["evidence_points"]}
            actual = [(point["part_id"], point["evidence_point_id"]) for point in item["points"]]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError("关联文件必须覆盖该题全部当前判定点，不能遗漏或借用别题判定点")
            if conn.execute("SELECT 1 FROM evidence_point_knowledge_links WHERE evidence_version_id=? AND source_kind='teacher' LIMIT 1",
                            (item["evidence_version_id"],)).fetchone():
                raise ValueError("受影响题目含教师指定关联，须单独核对后再修订，不能自动覆盖")
        for point in item["points"]:
            if not any(link.get("role") == "direct" for link in point["links"]):
                raise ValueError("每个判定点至少需要一个直接关联")
            for link in point["links"]:
                if link.get("term_id") not in term_ids or link.get("role") not in {"direct", "supporting_prerequisite"}:
                    raise ValueError("关联词条或角色不属于候选标准")
                if "weight" in link and not 0 < float(link["weight"]) <= 1:
                    raise ValueError("关联权重必须大于 0 且不超过 1")
    stage_release(bank, release, actor_ref="maintenance", source_reference="maintenance:standard")
    with connect(bank) as conn:
        # 重复执行不会复制第二份；先写候选版本，完成后才切换活动标准。
        for qid in plan["reusable_question_ids"]:
            vid = plan["versions"][qid]
            rows = load_point_links(bank, [vid], plan["preview"]["current_release_id"], connection=conn).get(vid, {})
            releases = {row.graph_release_id for group in rows.values() for row in group}
            for source_release in releases:
                conn.execute("""INSERT OR IGNORE INTO evidence_point_knowledge_links
                    (evidence_version_id,question_id,part_id,evidence_point_id,graph_release_id,role,
                     term_id,stable_key,resolution_status,weight,source_kind,source_reference)
                    SELECT evidence_version_id,question_id,part_id,evidence_point_id,?,role,
                           term_id,stable_key,resolution_status,weight,source_kind,source_reference
                    FROM evidence_point_knowledge_links WHERE evidence_version_id=? AND graph_release_id=?""",
                    (release.release_id, vid, source_release))
        for qid, item in by_id.items():
            replace_point_links(conn, evidence_version_id=item["evidence_version_id"], question_id=qid,
                                graph_release_id=release.release_id, points=item["points"],
                                source_reference="maintenance:standard")
    activate_release(bank, release.release_id,
                     expected_active_release_id=plan["preview"]["current_release_id"],
                     actor_ref="maintenance", reason="预演及关联核对完成后启用")
    with connect(bank) as conn:
        for qid in affected:
            refresh_derived_ownership_tags(conn, qid)


def preview_source_scores(bank: Path, data_root: Path) -> dict:
    """Read original data in place; emit counts and score-only differences."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import qn
    from lxml.etree import XMLSyntaxError
    from question_bank.document_pipeline.word_renderer import answer_space_lines
    from question_bank.services.rich_content_service import (
        strip_question_source_score, strip_question_source_score_blocks,
    )

    changed_ids = set()
    variants, types = Counter(), Counter()
    rich_changes = blocked = missing = preserved_layout = unsafe_xml = 0
    body_candidates = 0
    with closing(readonly(bank)) as conn:
        conn.execute("BEGIN")
        rows = conn.execute("""SELECT q.id,q.question_number,q.question_type,q.question_text
            FROM questions q LEFT JOIN papers p ON p.id=q.paper_id
            WHERE COALESCE(q.is_deleted,0)=0 AND COALESCE(p.import_status,'')<>'deleted'
            ORDER BY q.id""").fetchall()
        for row in rows:
            original = str(row["question_text"] or "")
            cleaned = strip_question_source_score(original)
            body_candidates += bool(re.search(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", cleaned))
            if original == cleaned:
                continue
            changed_ids.add(row["id"])
            types[str(row["question_type"] or "未分类")] += 1
            match = re.search(r"[（(]\s*\d+(?:\.\d+)?\s*分\s*[）)]", original)
            if match:
                variants[match.group(0)] += 1
            path = data_root / "question_bank" / "rich_content" / f"question_{row['id']}.json"
            if not path.is_file():
                missing += 1
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("富内容不是对象")
                blocks = payload.get("question_blocks") or []
                if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
                    raise ValueError("题干富内容不是段落列表")
                proposed = strip_question_source_score_blocks(blocks, question_number=str(row["question_number"]))
            except (OSError, ValueError, TypeError, XMLSyntaxError):
                blocked += 1
                continue
            rich_changes += blocks != proposed
            # Compare every XML node except its ordinary text; mathematical
            # text, attributes, relationships and node order must stay equal.
            for old, new in zip(blocks, proposed):
                if old.get("xml") == new.get("xml"):
                    continue
                def structure(xml):
                    return [(n.tag, dict(n.attrib), None if len(n) or n.tag == qn("w:t") else n.text)
                            for n in parse_xml(str(xml)).iter()]
                unsafe_xml += structure(old["xml"]) != structure(new["xml"])
            old_lines = answer_space_lines(row["question_type"], original)
            if old_lines != answer_space_lines(row["question_type"], cleaned):
                preserved_layout += 1
        saved_analysis = {}
        for table in ("training_criterion_heads", "question_solution_evidence_heads"):
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                saved_analysis[table] = len(changed_ids.intersection(
                    row[0] for row in conn.execute(f"SELECT question_id FROM {table}")))
    return {
        "mode": "readonly_display_preview", "active_questions": len(rows),
        "changed_stem_questions": len(changed_ids), "changed_stems_by_type": dict(types),
        "changed_rich_stem_questions": rich_changes, "missing_rich_files": missing,
        "blocked_rich_questions": blocked, "unexpected_xml_structure_changes": unsafe_xml,
        "questions_using_original_answer_space": preserved_layout,
        "remaining_nonprefix_bracket_candidates": body_candidates,
        "saved_analysis_heads_kept": saved_analysis,
        "score_only_differences": [
            {"before": prefix + "［其余题干保持原样］", "after": "［其余题干保持原样］", "question_count": count}
            for prefix, count in variants.most_common()
        ],
        "answers_changed": 0, "model_calls": 0, "applied": False,
        "source_content_unchanged": True, "analysis_unchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("errors", "standard", "source-scores"))
    parser.add_argument("--data-root", type=Path, default=ROOT / "user_data")
    parser.add_argument("--sessions", type=int, nargs="+", default=[])
    parser.add_argument("--release", type=Path)
    parser.add_argument("--links", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    root = args.data_root.resolve()
    bank, grading = root / "databases/question_bank.db", root / "databases/grading_system.db"
    if args.operation == "source-scores":
        if args.apply:
            parser.error("source-scores 只提供只读预览，不支持 --apply")
        print(json.dumps(preview_source_scores(bank, root), ensure_ascii=False, indent=2))
        return 0
    if args.operation == "errors" and not args.sessions:
        parser.error("errors 必须指定 --sessions")
    if args.operation == "standard" and not args.release:
        parser.error("standard 必须指定 --release")
    release, replacements = None, []
    if args.operation == "standard":
        from question_bank.knowledge_graph_release.loader import load_release
        release = load_release(args.release)
        replacements = json.loads(args.links.read_text(encoding="utf-8")) if args.links else []
    # 预演所有写入都发生在隔离副本，真实库只以 mode=ro 打开。
    with tempfile.TemporaryDirectory(prefix="qb-maintenance-") as directory:
        copy = Path(directory) / "question_bank.db"
        snapshot(bank, copy)
        if args.operation == "errors":
            report = reconcile_errors(copy, grading, root, sorted(set(args.sessions)))
        else:
            report = standard_plan(copy, release, data_root=root)
            if (report["preview"]["can_activate"]
                    and not set(report["unavailable_question_ids"]).intersection(report["affected_question_ids"])
                    and (args.links or not report["affected_question_ids"])):
                install_standard(copy, release, report, replacements)
                report["rehearsal_passed"] = True
            else:
                report["rehearsal_passed"] = False
        report.update(verify_database(copy))
        if args.apply:
            if args.operation == "standard" and not report.get("rehearsal_passed"):
                raise ValueError("请先为受影响题目提供 --links 并通过预演")
            backup_dir = root / "backups" / ("question_maintenance_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
            backup_dir.mkdir(parents=True)
            snapshot(bank, backup_dir / "question_bank_before.db")
            if args.operation == "errors":
                report = reconcile_errors(bank, grading, root, sorted(set(args.sessions)))
            else:
                current = standard_plan(bank, release, data_root=root)
                if current != {key: value for key, value in report.items()
                               if key not in {"rehearsal_passed", "integrity_ok", "foreign_keys_ok"}}:
                    raise ValueError("数据已变化，请重新预演")
                install_standard(bank, release, current, replacements)
            report["backup"] = backup_dir.name
            report["applied"] = True
        if args.apply:
            report.update(verify_database(bank))
        report.pop("versions", None)
        report.pop("source_versions", None)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
