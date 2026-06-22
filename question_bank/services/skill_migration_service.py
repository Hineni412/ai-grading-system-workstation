from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from question_bank.database.schema import connect, initialize_database
from question_bank.models.skill_catalog import (
    ResolvedSkillLink,
    ResolutionOutcome,
    SkillResolutionRequest,
    SkillRole,
    SkillSourceType,
)
from question_bank.services.skill_catalog_service import SkillCatalogService
from question_bank.services.skill_link_service import SkillLinkService
from question_bank.services.skill_resolution_service import SkillResolutionService
from update_tools.backup_core import (
    create_skill_migration_backup,
    restore_skill_migration_backup,
)


RESULT_CLASSES = (
    "resolved_existing",
    "created_local",
    "conflict",
    "insufficient_evidence",
)


@dataclass(frozen=True, slots=True)
class SkillMigrationConfig:
    grading_db: Path
    question_bank_db: Path
    report_dir: Path
    batch_id: str
    gold_file: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "grading_db", Path(self.grading_db).resolve())
        object.__setattr__(self, "question_bank_db", Path(self.question_bank_db).resolve())
        object.__setattr__(self, "report_dir", Path(self.report_dir).resolve())
        if self.gold_file is not None:
            object.__setattr__(self, "gold_file", Path(self.gold_file).resolve())
        batch_id = str(self.batch_id or "").strip()
        if not batch_id:
            raise ValueError("batch_id is required")
        object.__setattr__(self, "batch_id", batch_id)


class SkillMigrationService:
    def dry_run(self, config: SkillMigrationConfig) -> dict[str, Any]:
        config = _as_config(config)
        original_hashes = _source_hashes(config)
        before = _invariants(config.question_bank_db, config.grading_db)
        with tempfile.TemporaryDirectory(prefix="skill-migration-dry-") as temp_dir:
            temp_root = Path(temp_dir)
            question_bank_copy = temp_root / "question_bank.db"
            grading_copy = temp_root / "grading_system.db"
            _copy_sqlite(config.question_bank_db, question_bank_copy)
            _copy_sqlite(config.grading_db, grading_copy)
            rubric_hashes = self._copy_rubrics(grading_copy, temp_root / "rubrics")
            results = self._migrate_working_copy(question_bank_copy, grading_copy)
            after = _invariants(question_bank_copy, grading_copy)
            gold = _evaluate_gold(question_bank_copy, config.gold_file)

        current_hashes = _source_hashes(config)
        if current_hashes != original_hashes:
            raise RuntimeError("dry-run modified a source database or rubric")
        report = _build_report(
            config,
            mode="dry_run",
            results=results,
            before=before,
            after=after,
            gold=gold,
            source_hashes=original_hashes,
            copied_rubric_hashes=rubric_hashes,
        )
        report_path = _write_report(config, report, "dry-run")
        report["report_path"] = str(report_path)
        _rewrite_report(report_path, report)
        return report

    def apply(self, config: SkillMigrationConfig) -> dict[str, Any]:
        config = _as_config(config)
        initialize_database(config.question_bank_db)
        existing = _migration_run(config.question_bank_db, config.batch_id)
        if existing is not None and str(existing["status"]) == "succeeded":
            backups = _json_object(existing.get("backup_json"))
            return {
                "status": "succeeded",
                "batch_id": config.batch_id,
                "idempotent": True,
                "backups": backups,
                "report_path": existing.get("report_path"),
            }

        with self._lock(config.question_bank_db, config.batch_id):
            dry_report = self.dry_run(config)
            if not dry_report["ready"]:
                return {
                    **dry_report,
                    "status": "blocked",
                    "idempotent": False,
                }
            backups: dict[str, str] = {}
            try:
                backups = create_skill_migration_backup(
                    config.question_bank_db,
                    config.grading_db,
                    config.batch_id,
                )
                if not backups or not all(Path(path).is_file() for path in backups.values()):
                    raise RuntimeError("database backup did not complete")
                before = _invariants(config.question_bank_db, config.grading_db)
                with tempfile.TemporaryDirectory(prefix="skill-migration-apply-") as temp_dir:
                    working_db = Path(temp_dir) / "question_bank.db"
                    _copy_sqlite(config.question_bank_db, working_db)
                    results = self._migrate_working_copy(working_db, config.grading_db)
                    after = _invariants(working_db, config.grading_db)
                    _assert_invariants_unchanged(before, after)
                    report = _build_report(
                        config,
                        mode="apply",
                        results=results,
                        before=before,
                        after=after,
                        gold=dry_report["gold"],
                        source_hashes=_source_hashes(config),
                        copied_rubric_hashes={},
                    )
                    report["status"] = "succeeded"
                    report["backups"] = backups
                    report["backup_created_before_write"] = True
                    report["idempotent"] = False
                    report_path = _report_path(config, "apply")
                    report["report_path"] = str(report_path)
                    _record_run(
                        working_db,
                        batch_id=config.batch_id,
                        mode="apply",
                        status="succeeded",
                        report=report,
                        backups=backups,
                    )
                    self._replace_database(working_db, config.question_bank_db)
                _rewrite_report(report_path, report)
                return report
            except Exception as exc:
                failure = {
                    "status": "failed",
                    "batch_id": config.batch_id,
                    "idempotent": False,
                    "backups": backups,
                    "backup_created_before_write": bool(backups),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                failure_path = _report_path(config, "failed")
                failure["report_path"] = str(failure_path)
                _rewrite_report(failure_path, failure)
                return failure

    def rollback(self, question_bank_db: Path, batch_id: str) -> dict[str, Any]:
        question_bank = Path(question_bank_db).resolve()
        restored = restore_skill_migration_backup(question_bank, batch_id)
        initialize_database(question_bank)
        report = {
            "status": "rolled_back",
            "batch_id": str(batch_id),
            "restored_from": restored,
        }
        _record_run(
            question_bank,
            batch_id=str(batch_id),
            mode="rollback",
            status="rolled_back",
            report=report,
            backups=restored,
        )
        return report

    def _migrate_working_copy(self, question_bank_db: Path, grading_db: Path) -> list[dict[str, Any]]:
        initialize_database(question_bank_db)
        results = self._migrate_questions(question_bank_db)
        results.extend(self._migrate_assessments(question_bank_db, grading_db))
        return results

    def _migrate_questions(self, question_bank_db: Path) -> list[dict[str, Any]]:
        resolver = SkillResolutionService(question_bank_db)
        link_service = SkillLinkService(question_bank_db)
        with connect(question_bank_db) as conn:
            rows = conn.execute(
                """
                SELECT q.id, q.question_text, q.answer_text, p.grade
                FROM questions q LEFT JOIN papers p ON p.id = q.paper_id
                WHERE COALESCE(q.is_deleted, 0) = 0
                  AND COALESCE(p.import_status, '') <> 'deleted'
                ORDER BY q.id
                """
            ).fetchall()
            tag_rows = conn.execute(
                """
                SELECT question_id, tag_type, tag_value
                FROM question_tags
                ORDER BY question_id, id
                """
            ).fetchall()
            existing_rows = conn.execute(
                """
                SELECT question_id, COUNT(*) AS count
                FROM question_skill_links
                WHERE status = 'resolved' AND role = 'measured'
                GROUP BY question_id
                """
            ).fetchall()
        tags: dict[int, list[tuple[str, str]]] = {}
        for row in tag_rows:
            tags.setdefault(int(row["question_id"]), []).append(
                (str(row["tag_type"]), str(row["tag_value"]))
            )
        existing = {int(row["question_id"]) for row in existing_rows if int(row["count"]) > 0}
        results: list[dict[str, Any]] = []
        for row in rows:
            question_id = int(row["id"])
            source_ref = f"question:{question_id}"
            if question_id in existing:
                results.append(_source_result("question_bank", source_ref, "resolved_existing", "existing measured link"))
                continue
            candidates = _question_skill_candidates(tags.get(question_id, []))
            context_hint = _context_stable_key_hint(
                f"{row['question_text'] or ''} {row['answer_text'] or ''}"
            )
            if context_hint:
                candidates.insert(0, ("context_keyword", context_hint))
            if not candidates:
                results.append(_source_result("question_bank", source_ref, "insufficient_evidence", "no usable tags"))
                continue
            resolution = None
            used_label = ""
            used_type = ""
            for tag_type, label in candidates:
                stable_hint = (
                    label
                    if tag_type in {"canonical_knowledge_id", "context_keyword"}
                    and label.startswith("math.")
                    else ""
                )
                request = SkillResolutionRequest(
                    source_type=SkillSourceType.QUESTION_BANK_ITEM,
                    source_ref=source_ref,
                    raw_label=label,
                    stable_key_hint=stable_hint,
                    grade=str(row["grade"] or ""),
                    question_text=str(row["question_text"] or ""),
                    answer_text=str(row["answer_text"] or ""),
                    existing_tags=tuple(value for _, value in tags.get(question_id, [])),
                )
                resolution = resolver.resolve(request)
                used_label = label
                used_type = tag_type
                if resolution.outcome is not ResolutionOutcome.CONFLICT:
                    break
            if resolution is None:
                results.append(_source_result("question_bank", source_ref, "insufficient_evidence", "no resolution request"))
            elif resolution.outcome is ResolutionOutcome.CONFLICT:
                results.append(_source_result("question_bank", source_ref, "conflict", resolution.reason, raw_label=used_label))
            else:
                link_service.replace_question_links(
                    question_id,
                    [
                        ResolvedSkillLink(
                            int(resolution.skill_id),
                            SkillRole.MEASURED,
                            raw_knowledge_id=used_label if used_type == "canonical_knowledge_id" else "",
                            raw_knowledge_label=used_label,
                            source="migration",
                            confidence=resolution.confidence,
                            evidence={"tag_type": used_type, "batch_migration": True},
                        )
                    ],
                )
                results.append(
                    _source_result(
                        "question_bank",
                        source_ref,
                        resolution.outcome.value,
                        resolution.reason,
                        raw_label=used_label,
                        skill_id=int(resolution.skill_id),
                    )
                )
        return results

    def _migrate_assessments(self, question_bank_db: Path, grading_db: Path) -> list[dict[str, Any]]:
        if not Path(grading_db).is_file():
            return []
        with closing(sqlite3.connect(grading_db)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "grading_sessions"):
                return []
            sessions = conn.execute(
                """
                SELECT id, rubric_path
                FROM grading_sessions
                WHERE COALESCE(is_deleted, 0) = 0
                ORDER BY id
                """
            ).fetchall()
        link_service = SkillLinkService(question_bank_db)
        results: list[dict[str, Any]] = []
        from session_manager import iter_rubric_skill_requests

        for session in sessions:
            session_id = str(session["id"])
            rubric_path = Path(str(session["rubric_path"] or ""))
            if not rubric_path.is_file():
                results.append(
                    _source_result(
                        "assessment",
                        f"assessment:{session_id}:rubric",
                        "insufficient_evidence",
                        "rubric file is missing",
                    )
                )
                continue
            try:
                payload = json.loads(rubric_path.read_text(encoding="utf-8"))
                requests = list(iter_rubric_skill_requests(payload, grading_session_id=session_id))
            except Exception as exc:
                results.append(
                    _source_result(
                        "assessment",
                        f"assessment:{session_id}:rubric",
                        "insufficient_evidence",
                        f"rubric could not be read: {type(exc).__name__}",
                    )
                )
                continue
            measured_refs = sorted({source_ref for source_ref, role, _ in requests if role is SkillRole.MEASURED})
            if not measured_refs:
                results.append(
                    _source_result(
                        "assessment",
                        f"assessment:{session_id}:rubric",
                        "insufficient_evidence",
                        "rubric has no measured skill request",
                    )
                )
                continue
            link_service.resolve_rubric(session_id, payload)
            with connect(question_bank_db) as conn:
                linked_refs = {
                    str(row["source_question_id"])
                    for row in conn.execute(
                        """
                        SELECT source_question_id
                        FROM assessment_item_skills
                        WHERE grading_session_id = ? AND role = 'measured' AND status = 'resolved'
                        """,
                        (session_id,),
                    ).fetchall()
                }
            for source_ref in measured_refs:
                outcome = "resolved_existing" if source_ref in linked_refs else "conflict"
                results.append(
                    _source_result(
                        "assessment",
                        f"assessment:{session_id}:{source_ref}",
                        outcome,
                        "measured link resolved" if outcome == "resolved_existing" else "measured skill needs review",
                    )
                )
        return results

    def _copy_rubrics(self, grading_copy: Path, destination: Path) -> dict[str, str]:
        hashes: dict[str, str] = {}
        with closing(sqlite3.connect(grading_copy)) as conn:
            conn.row_factory = sqlite3.Row
            if not _table_exists(conn, "grading_sessions"):
                return hashes
            rows = conn.execute("SELECT id, rubric_path FROM grading_sessions ORDER BY id").fetchall()
            destination.mkdir(parents=True, exist_ok=True)
            for row in rows:
                source = Path(str(row["rubric_path"] or ""))
                if not source.is_file():
                    continue
                target = destination / f"session-{row['id']}{source.suffix or '.json'}"
                shutil.copy2(source, target)
                hashes[str(source.resolve())] = _hash_file(source)
                conn.execute(
                    "UPDATE grading_sessions SET rubric_path = ? WHERE id = ?",
                    (str(target), int(row["id"])),
                )
            conn.commit()
        return hashes

    def _replace_database(self, source: Path, destination: Path) -> None:
        destination = Path(destination).resolve()
        with tempfile.NamedTemporaryFile(
            prefix=f".{destination.name}.",
            suffix=".migrated",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
        try:
            shutil.copy2(source, temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _lock(self, question_bank_db: Path, batch_id: str) -> Iterator[None]:
        lock_path = Path(question_bank_db).with_suffix(f".{batch_id}.skill-migration.lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise RuntimeError("another skill migration run is active") from exc
        try:
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.close(descriptor)
            yield
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
            if lock_path.exists():
                lock_path.unlink()


def _as_config(config: SkillMigrationConfig) -> SkillMigrationConfig:
    if not isinstance(config, SkillMigrationConfig):
        raise TypeError("config must be SkillMigrationConfig")
    if not config.question_bank_db.is_file():
        raise FileNotFoundError(f"question bank database not found: {config.question_bank_db}")
    if not config.grading_db.is_file():
        raise FileNotFoundError(f"grading database not found: {config.grading_db}")
    return config


def _question_skill_candidates(tags: list[tuple[str, str]]) -> list[tuple[str, str]]:
    priorities = {
        "measured_skill": 0,
        "sub_skill": 1,
        "canonical_knowledge_id": 2,
        "knowledge_point": 3,
        "prerequisite": 4,
        "method": 5,
        "model": 6,
    }
    return sorted(
        [(kind, value.strip()) for kind, value in tags if kind in priorities and value.strip()],
        key=lambda item: (priorities[item[0]], item[1]),
    )


def _context_stable_key_hint(text: object) -> str:
    normalized = str(text or "")
    if "角平分线" in normalized and "面积" in normalized:
        return "math.geometry.triangle.bisector_area"
    if "三线合一" in normalized:
        return "math.geometry.special_triangle.isosceles_property"
    if "垂直平分线" in normalized:
        return "math.geometry.construction.perpendicular"
    if "两直线平行" in normalized or "平行线的性质" in normalized:
        return "math.geometry.line_angle.parallel_property"
    if "角平分线" in normalized:
        return "math.geometry.line_angle.bisector"
    if ("最短路径" in normalized or "值最小" in normalized) and "轴对称" in normalized:
        return "math.geometry.construction.shortest_path"
    if "轴对称" in normalized and any(word in normalized for word in ("画出", "作出")):
        return "math.geometry.transformation.axis_draw"
    if "轴对称" in normalized or "折叠" in normalized:
        return "math.geometry.transformation.axis_property"
    if "三角形外心" in normalized or "外接圆圆心" in normalized:
        return "math.geometry.construction.circumcenter"
    if "全等三角形" in normalized:
        return "math.geometry.congruence.judge"
    if "一次函数" in normalized and any(word in normalized for word in ("实际", "估计", "应用")):
        return "math.function.linear.graph_application"
    rules = (
        (("科学记数法",), "math.algebra.expression.scientific"),
        (("负整数指数幂", "零指数幂"), "math.algebra.expression.power"),
        (("一元一次方程",), "math.algebra.equation.linear_solve"),
        (("一次函数",), "math.function.linear.graph"),
    )
    for keywords, stable_key in rules:
        if any(keyword in normalized for keyword in keywords):
            return stable_key
    return ""


def _source_result(source: str, source_ref: str, outcome: str, reason: str, **evidence: Any) -> dict[str, Any]:
    if outcome not in RESULT_CLASSES:
        raise ValueError(f"unsupported migration result: {outcome}")
    return {
        "source": source,
        "source_ref": source_ref,
        "outcome": outcome,
        "reason": str(reason),
        "evidence": {key: value for key, value in evidence.items() if value not in (None, "")},
    }


def _evaluate_gold(question_bank_db: Path, gold_file: Path | None) -> dict[str, Any]:
    if gold_file is None or not gold_file.is_file():
        return {"reviewed_count": 0, "correct_count": 0, "precision": 0.0, "passed": False}
    payload = json.loads(gold_file.read_text(encoding="utf-8"))
    items = payload.get("items", []) if isinstance(payload, Mapping) else []
    if isinstance(payload, Mapping) and not items:
        items = [
            {**dict(case), "case_id": f"{case.get('case_id') or case_index}-{occurrence_index}"}
            for case_index, case in enumerate(payload.get("cases", []))
            if isinstance(case, Mapping)
            for occurrence_index in range(max(0, int(case.get("occurrences") or 1)))
        ]
    resolver = SkillResolutionService(question_bank_db)
    catalog = SkillCatalogService(question_bank_db)
    correct = 0
    reviewed = 0
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            continue
        raw_label = str(item.get("raw_label") or "").strip()
        expected = str(item.get("expected_stable_key") or "").strip()
        if not raw_label or not expected:
            continue
        reviewed += 1
        resolution = resolver.resolve(
            SkillResolutionRequest(
                source_type=SkillSourceType.LEGACY_TERM,
                source_ref=f"gold:{item.get('case_id') or index}",
                raw_label=raw_label,
            ),
            persist_conflict=False,
        )
        if resolution.skill_id is None:
            continue
        skill = catalog.get_skill(int(resolution.skill_id))
        if skill is not None and str(skill["stable_key"]) == expected:
            correct += 1
    precision = round(correct / reviewed, 4) if reviewed else 0.0
    return {
        "reviewed_count": reviewed,
        "correct_count": correct,
        "precision": precision,
        "passed": reviewed >= 100 and precision >= 0.98,
    }


def _build_report(
    config: SkillMigrationConfig,
    *,
    mode: str,
    results: list[dict[str, Any]],
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    gold: Mapping[str, Any],
    source_hashes: Mapping[str, str],
    copied_rubric_hashes: Mapping[str, str],
) -> dict[str, Any]:
    source_counts = Counter(item["source"] for item in results)
    result_counts = Counter(item["outcome"] for item in results)
    total = len(results)
    resolved = result_counts["resolved_existing"] + result_counts["created_local"]
    coverage = round(resolved / total, 4) if total else 1.0
    reasons: list[str] = []
    if coverage < 0.95:
        reasons.append("coverage_below_95_percent")
    if int(gold.get("reviewed_count") or 0) < 100:
        reasons.append("gold_review_count_below_100")
    if float(gold.get("precision") or 0.0) < 0.98:
        reasons.append("gold_precision_below_98_percent")
    if before != after:
        reasons.append("business_invariants_changed")
    ready = not reasons
    return {
        "batch_id": config.batch_id,
        "mode": mode,
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "readiness_reasons": reasons,
        "source_counts": {
            "question_bank": source_counts["question_bank"],
            "assessment": source_counts["assessment"],
            "total": total,
        },
        "result_counts": {name: result_counts[name] for name in RESULT_CLASSES},
        "coverage": {"value": coverage, "threshold": 0.95, "passed": coverage >= 0.95},
        "gold": dict(gold),
        "invariants": {"before": dict(before), "after": dict(after), "unchanged": before == after},
        "source_hashes": dict(source_hashes),
        "copied_rubric_hashes": dict(copied_rubric_hashes),
        "sources": results,
    }


def _invariants(question_bank_db: Path, grading_db: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with closing(sqlite3.connect(question_bank_db)) as conn:
        result["question_count"] = _table_count(conn, "questions")
        result["question_tag_count"] = _table_count(conn, "question_tags")
        result["training_task_count"] = _table_count(conn, "training_tasks")
        result["question_tag_digest"] = _table_digest(conn, "question_tags")
    with closing(sqlite3.connect(grading_db)) as conn:
        result["grading_result_count"] = _table_count(conn, "session_results")
        result["student_count"] = _table_count(conn, "students")
    return result


def _assert_invariants_unchanged(before: Mapping[str, Any], after: Mapping[str, Any]) -> None:
    if dict(before) != dict(after):
        raise RuntimeError("migration changed protected business records")


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]) if _table_exists(conn, table) else 0


def _table_digest(conn: sqlite3.Connection, table: str) -> str:
    if not _table_exists(conn, table):
        return hashlib.sha256(b"").hexdigest()
    rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid").fetchall()
    payload = json.dumps([list(row) for row in rows], ensure_ascii=False, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone() is not None


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    with closing(sqlite3.connect(source)) as source_conn:
        with closing(sqlite3.connect(destination)) as destination_conn:
            source_conn.backup(destination_conn)


def _source_hashes(config: SkillMigrationConfig) -> dict[str, str]:
    hashes = {
        "question_bank": _hash_file(config.question_bank_db),
        "grading": _hash_file(config.grading_db),
    }
    with closing(sqlite3.connect(config.grading_db)) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "grading_sessions"):
            for row in conn.execute("SELECT id, rubric_path FROM grading_sessions ORDER BY id").fetchall():
                path = Path(str(row["rubric_path"] or ""))
                if path.is_file():
                    hashes[f"rubric:{row['id']}"] = _hash_file(path)
    return hashes


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _report_path(config: SkillMigrationConfig, suffix: str) -> Path:
    config.report_dir.mkdir(parents=True, exist_ok=True)
    return config.report_dir / f"{config.batch_id}.{suffix}.json"


def _write_report(config: SkillMigrationConfig, report: Mapping[str, Any], suffix: str) -> Path:
    path = _report_path(config, suffix)
    _rewrite_report(path, report)
    return path


def _rewrite_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(report), ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _migration_run(question_bank_db: Path, batch_id: str) -> dict[str, Any] | None:
    with connect(question_bank_db) as conn:
        row = conn.execute(
            "SELECT * FROM skill_migration_runs WHERE batch_id = ?",
            (str(batch_id),),
        ).fetchone()
    return dict(row) if row is not None else None


def _record_run(
    question_bank_db: Path,
    *,
    batch_id: str,
    mode: str,
    status: str,
    report: Mapping[str, Any],
    backups: Mapping[str, str],
) -> None:
    with connect(question_bank_db) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO skill_migration_runs (
                batch_id, mode, status, source_counts_json, result_counts_json,
                invariants_json, report_path, backup_json, error_message,
                finished_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','localtime'), datetime('now','localtime'))
            """,
            (
                str(batch_id),
                mode,
                status,
                json.dumps(report.get("source_counts") or {}, ensure_ascii=False),
                json.dumps(report.get("result_counts") or {}, ensure_ascii=False),
                json.dumps(report.get("invariants") or {}, ensure_ascii=False),
                report.get("report_path"),
                json.dumps(dict(backups), ensure_ascii=False),
                report.get("error"),
            ),
        )


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, Mapping) else {}


__all__ = ["SkillMigrationConfig", "SkillMigrationService"]
