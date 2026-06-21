from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable, Callable

from integration.knowledge_term_identity import build_grading_knowledge_term
from question_bank.database.schema import connect, initialize_database
from question_bank.models.knowledge_alignment import (
    AlignmentStatus,
    KnowledgeConcept,
    KnowledgeSourceMapping,
    normalize_source_value,
)
from question_bank.taxonomy.registry import (
    canonical_knowledge_seed_rows,
    canonicalize_knowledge,
)


@dataclass(frozen=True)
class AlignmentResolution:
    source_namespace: str
    source_value: str
    status: AlignmentStatus
    concept: KnowledgeConcept | None
    confidence: float
    sub_skill_tags: tuple[str, ...] = ()

    @property
    def eligible_for_recommendation(self) -> bool:
        return self.status is AlignmentStatus.CONFIRMED and self.concept is not None


@dataclass(frozen=True)
class LegacyAlignmentMigrationReport:
    copied: int = 0
    reused: int = 0
    conflicts: tuple[str, ...] = ()


class ConceptAlignmentService:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)

    def initialize_database(self) -> None:
        initialize_database(self.db_path)

    def create_concept(
        self,
        canonical_key: str,
        name: str,
        *,
        aliases: Iterable[str] = (),
        subject: str | None = None,
        grade: str | None = None,
    ) -> KnowledgeConcept:
        concept = KnowledgeConcept(
            id=0,
            canonical_key=canonical_key,
            name=name,
            aliases=tuple(aliases),
        )
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_concepts (
                    canonical_key, name, aliases_json, subject, grade
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    concept.canonical_key,
                    concept.name,
                    _json(list(concept.aliases)),
                    _optional(subject),
                    _optional(grade),
                ),
            )
            return KnowledgeConcept(
                id=int(cursor.lastrowid),
                canonical_key=concept.canonical_key,
                name=concept.name,
                aliases=concept.aliases,
            )

    def update_concept(
        self,
        concept_id: int,
        *,
        name: str | None = None,
        aliases: Iterable[str] | None = None,
        subject: str | None = None,
        grade: str | None = None,
        status: str | None = None,
    ) -> KnowledgeConcept:
        self.initialize_database()
        updates: list[str] = []
        params: list[Any] = []
        if name is not None:
            updates.append("name = ?")
            params.append(KnowledgeConcept(0, "temporary", name).name)
        if aliases is not None:
            normalized_aliases = KnowledgeConcept(0, "temporary", "temporary", tuple(aliases)).aliases
            updates.append("aliases_json = ?")
            params.append(_json(list(normalized_aliases)))
        if subject is not None:
            updates.append("subject = ?")
            params.append(_optional(subject))
        if grade is not None:
            updates.append("grade = ?")
            params.append(_optional(grade))
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if updates:
            updates.append("updated_at = datetime('now','localtime')")
            params.append(int(concept_id))
            with connect(self.db_path) as conn:
                conn.execute(
                    f"UPDATE knowledge_concepts SET {', '.join(updates)} WHERE id = ?",
                    params,
                )
        concept = self.get_concept(concept_id)
        if concept is None:
            raise KeyError(f"knowledge concept not found: {concept_id}")
        return concept

    def get_concept(self, concept_id: int) -> KnowledgeConcept | None:
        self.initialize_database()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM knowledge_concepts WHERE id = ?",
                (int(concept_id),),
            ).fetchone()
            return _concept_from_row(row) if row is not None else None

    def list_concepts(self, *, status: str | None = "active") -> list[KnowledgeConcept]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            if status is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_concepts ORDER BY name, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM knowledge_concepts WHERE status = ? ORDER BY name, id",
                    (status,),
                ).fetchall()
            return [_concept_from_row(row) for row in rows]

    def seed_registry_concepts(self) -> int:
        self.initialize_database()
        created = 0
        with connect(self.db_path) as conn:
            for row in canonical_knowledge_seed_rows():
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO knowledge_concepts (
                        canonical_key, name, aliases_json, subject
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        normalize_source_value(row["canonical_key"]),
                        row["name"],
                        _json(list(row["aliases"])),
                        "math",
                    ),
                )
                created += int(cursor.rowcount > 0)
        return created

    def create_relation(
        self,
        source_concept_id: int,
        target_concept_id: int,
        relation_type: str,
        *,
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO knowledge_relations (
                    source_concept_id, target_concept_id, relation_type, weight, metadata_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(source_concept_id),
                    int(target_concept_id),
                    relation_type,
                    float(weight),
                    _json(metadata or {}),
                ),
            )
            row = conn.execute(
                "SELECT * FROM knowledge_relations WHERE id = ?",
                (int(cursor.lastrowid),),
            ).fetchone()
            return dict(row)

    def list_relations(self, source_concept_id: int | None = None) -> list[dict[str, Any]]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            if source_concept_id is None:
                rows = conn.execute(
                    "SELECT * FROM knowledge_relations ORDER BY source_concept_id, id"
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM knowledge_relations
                    WHERE source_concept_id = ?
                    ORDER BY id
                    """,
                    (int(source_concept_id),),
                ).fetchall()
            return [dict(row) for row in rows]

    def confirm_mapping(
        self,
        source_namespace: str,
        source_value: str,
        concept_id: int,
        *,
        sub_skill_tags: Iterable[str] = (),
        reviewed_by: str | None = None,
        evidence: dict[str, Any] | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=int(concept_id),
                    status=AlignmentStatus.CONFIRMED,
                    confidence=1.0,
                    sub_skill_tags=tuple(sub_skill_tags),
                ),
                reviewed_by=reviewed_by,
                evidence=evidence,
            )

    def confirm_many(
        self,
        mappings: Iterable[tuple[str, str, int] | tuple[str, str, int, Iterable[str]]],
        *,
        reviewed_by: str | None = None,
    ) -> list[KnowledgeSourceMapping]:
        self.initialize_database()
        results: list[KnowledgeSourceMapping] = []
        with connect(self.db_path) as conn:
            for item in mappings:
                if len(item) == 4:
                    source_namespace, source_value, concept_id, sub_skill_tags = item
                else:
                    source_namespace, source_value, concept_id = item
                    sub_skill_tags = ()
                results.append(
                    _upsert_mapping(
                        conn,
                        KnowledgeSourceMapping(
                            source_namespace=source_namespace,
                            source_value=source_value,
                            concept_id=int(concept_id),
                            status=AlignmentStatus.CONFIRMED,
                            confidence=1.0,
                            sub_skill_tags=tuple(sub_skill_tags),
                        ),
                        reviewed_by=reviewed_by,
                    )
                )
        return results

    def suggest_mapping(
        self,
        source_namespace: str,
        source_value: str,
        concept_id: int,
        *,
        confidence: float,
        sub_skill_tags: Iterable[str] = (),
        evidence: dict[str, Any] | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=int(concept_id),
                    status=AlignmentStatus.SUGGESTED,
                    confidence=confidence,
                    sub_skill_tags=tuple(sub_skill_tags),
                ),
                evidence=evidence,
            )

    def reject_mapping(
        self,
        source_namespace: str,
        source_value: str,
        *,
        reviewed_by: str | None = None,
    ) -> KnowledgeSourceMapping:
        self.initialize_database()
        with connect(self.db_path) as conn:
            return _upsert_mapping(
                conn,
                KnowledgeSourceMapping(
                    source_namespace=source_namespace,
                    source_value=source_value,
                    concept_id=None,
                    status=AlignmentStatus.REJECTED,
                    confidence=0.0,
                ),
                reviewed_by=reviewed_by,
            )

    def list_mappings(
        self,
        *,
        source_namespace: str | None = None,
        status: AlignmentStatus | str | None = None,
    ) -> list[KnowledgeSourceMapping]:
        self.initialize_database()
        where: list[str] = []
        params: list[Any] = []
        if source_namespace:
            where.append("source_namespace = ?")
            params.append(normalize_source_value(source_namespace))
        if status:
            status_value = status.value if isinstance(status, AlignmentStatus) else str(status)
            where.append("status = ?")
            params.append(status_value)
        sql = "SELECT * FROM knowledge_source_mappings"
        if where:
            sql += f" WHERE {' AND '.join(where)}"
        sql += " ORDER BY source_namespace, source_value, id"
        with connect(self.db_path) as conn:
            return [_mapping_from_row(row) for row in conn.execute(sql, params).fetchall()]

    def coverage_metrics(self) -> dict[str, dict[str, int]]:
        self.initialize_database()
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source_namespace, status, COUNT(*) AS count
                FROM knowledge_source_mappings
                GROUP BY source_namespace, status
                ORDER BY source_namespace, status
                """
            ).fetchall()
        metrics: dict[str, dict[str, int]] = {}
        for row in rows:
            metrics.setdefault(str(row["source_namespace"]), {})[str(row["status"])] = int(row["count"])
        return metrics

    def ai_batch_align(
        self,
        source_namespace: str,
        source_terms: list[str],
        llm_client: Any,
        on_chunk_complete: Callable[[int, int, list[str], list[Any]], None] = None,
    ) -> list[KnowledgeSourceMapping]:
        if not source_terms:
            return []

        # 1. Delta check: 决定哪些词需要(重新)对齐
        #    设计意图：教师未确认过的词一律重新对齐，确保每次点"AI一键对齐"
        #    都基于当前最新的标准知识点库给出建议。
        #    - 无记录词 / suggested / unmapped：重新对齐
        #    - confirmed / rejected：跳过（教师已决策，不重复消耗 API）
        unmapped_terms = []
        with connect(self.db_path) as conn:
            for term in source_terms:
                row = conn.execute(
                    """
                    SELECT status FROM knowledge_source_mappings
                    WHERE source_namespace = ? AND source_value = ?
                    """,
                    (source_namespace, term)
                ).fetchone()
                if row is None or str(row["status"]) not in ("confirmed", "rejected"):
                    unmapped_terms.append(term)

        # 2. Perform alignment only if there are unmapped terms
        if unmapped_terms:
            concepts = self.list_concepts(status="active")

            def prune_candidates(chunk_terms):
                pruned = set()
                for term in chunk_terms:
                    scores = []
                    for c in concepts:
                        sim = max(SequenceMatcher(None, term, alias).ratio() for alias in [c.name] + list(c.aliases))
                        scores.append((sim, c))
                    scores.sort(key=lambda x: -x[0])
                    for _, c in scores[:5]:
                        pruned.add(c)
                return sorted(list(pruned), key=lambda x: x.canonical_key)

            def align_chunk(chunk_terms):
                chunk_concepts = prune_candidates(chunk_terms)
                candidate_concepts = [
                    {
                        "canonical_key": c.canonical_key,
                        "name": c.name,
                        "aliases": list(c.aliases)
                    }
                    for c in chunk_concepts
                ]

                prompt = f"""你是一个专业的数学教学与课标分析 AI 助手。你的任务是将一批从批改系统抽取的学生薄弱知识点（原始词）对齐到标准知识点，并从原始词字面中提取出细粒度的子技能标签。

【标准知识点候选列表】
{json.dumps(candidate_concepts, ensure_ascii=False, indent=2)}

【待对齐的原始词列表】
{json.dumps(chunk_terms, ensure_ascii=False, indent=2)}

【对齐与提取规则】
1. 对齐标准知识点：
   - 为每个原始词，从【标准知识点候选列表】中选出最匹配的一个。如果没有任何标准知识点匹配，返回 null。
   - 返回标准知识点的 `canonical_key`。

2. 提取子技能标签 (sub_skill_tags)：
   - **极其重要**：仅从原始词本身的字面和语义推断子技能，绝不能从对齐的标准知识点过度发散或补全！
   - 例如：如果原始词是 "等腰三角形的角度计算"，子技能标签应仅为 `["角度计算"]`。绝对不能因为对齐到了 "等腰三角形"，就凭空推断出 `["底角计算", "顶角计算"]` 等原始词中并未提及的具体概念。如果原始词没有包含细分技能，子技能标签应为空数组 `[]`。

3. 给出置信度 (confidence)：
   - 0.0 到 1.0 之间的浮点数。若匹配精准且无歧义，给出高置信度（如 0.9+）；否则给低置信度。

【输出格式】
直接返回一个 JSON 对象，其中包含 "alignments" 键，其值为无键名的二维数组。格式如下：
{{
  "alignments": [
    ["原始词", "匹配的 canonical_key/如无则填 null", ["子技能标签1", "子技能标签2"], 置信度]
  ]
}}

不要包含任何 Markdown 格式包裹（如 ```json），只输出 JSON 字符串。
"""
                try:
                    res = llm_client.json_from_text(prompt)
                    print("DEBUG align_chunk res:", repr(res))
                    if isinstance(res, dict) and "alignments" in res:
                        aligns = res["alignments"]
                        if isinstance(aligns, list):
                            ret = []
                            for item in aligns:
                                if isinstance(item, list):
                                    ret.append(item)
                                elif isinstance(item, dict):
                                    ret.append([
                                        item.get("source_value"),
                                        item.get("concept_key"),
                                        item.get("sub_skill_tags", []),
                                        item.get("confidence", 0.5)
                                    ])
                            print("DEBUG dict match ret:", repr(ret))
                            return {"alignments": ret, "error": None, "raw_response": res}
                    elif isinstance(res, list):
                        print("DEBUG list match res:", repr(res))
                        return {"alignments": res, "error": None, "raw_response": res}
                    else:
                        print("DEBUG no match res:", type(res))
                        return {"alignments": [], "error": f"返回值类型不匹配(预期 dict/list，实际 {type(res).__name__})", "raw_response": res}
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    return {"alignments": [], "error": str(e), "raw_response": None}

            # Threaded chunk concurrency
            chunk_size = 15
            chunks = [unmapped_terms[i : i + chunk_size] for i in range(0, len(unmapped_terms), chunk_size)]
            all_alignments = []
            total_chunks = len(chunks)
            completed_chunks = 0

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(align_chunk, chunk): chunk for chunk in chunks}
                for future in as_completed(futures):
                    chunk_terms = futures[future]
                    completed_chunks += 1
                    res_list = []
                    error_info = None
                    try:
                        chunk_res = future.result()
                        if isinstance(chunk_res, dict):
                            res_list = chunk_res.get("alignments", [])
                            error_info = {
                                "error": chunk_res.get("error"),
                                "raw_response": chunk_res.get("raw_response")
                            }
                        else:
                            res_list = chunk_res if isinstance(chunk_res, list) else []
                            error_info = {"error": "返回值格式错误", "raw_response": chunk_res}
                        
                        if isinstance(res_list, list):
                            all_alignments.extend(res_list)
                    except Exception as e:
                        import traceback
                        traceback.print_exc()
                        error_info = {"error": str(e), "raw_response": None}
                    
                    if on_chunk_complete:
                        try:
                            import inspect
                            sig = inspect.signature(on_chunk_complete)
                            accepts_error_info = False
                            params = list(sig.parameters.values())
                            if any(p.kind == p.VAR_KEYWORD for p in params):
                                accepts_error_info = True
                            elif len(params) >= 5:
                                accepts_error_info = True
                            elif 'error_info' in sig.parameters:
                                accepts_error_info = True

                            if accepts_error_info:
                                on_chunk_complete(completed_chunks, total_chunks, chunk_terms, res_list, error_info=error_info)
                            else:
                                on_chunk_complete(completed_chunks, total_chunks, chunk_terms, res_list)
                        except Exception:
                            try:
                                on_chunk_complete(completed_chunks, total_chunks, chunk_terms, res_list)
                            except Exception:
                                pass

            # Write-back in a single database transaction
            key_to_concept = {c.canonical_key: c for c in concepts}
            with connect(self.db_path) as conn:
                for item in all_alignments:
                    if not isinstance(item, list) or len(item) < 2:
                        continue
                    val = item[0]
                    if not val or val not in unmapped_terms:
                        continue
                    key = item[1]
                    concept = key_to_concept.get(key) if key else None
                    concept_id = concept.id if concept else None

                    sub_tags = item[2] if len(item) > 2 else []
                    if not isinstance(sub_tags, list):
                        sub_tags = []
                    sub_tags = [str(t).strip() for t in sub_tags if t]

                    try:
                        confidence = float(item[3]) if len(item) > 3 else 0.5
                    except (TypeError, ValueError):
                        confidence = 0.5

                    mapping = KnowledgeSourceMapping(
                        source_namespace=source_namespace,
                        source_value=val,
                        concept_id=concept_id,
                        status=AlignmentStatus.SUGGESTED,
                        confidence=confidence,
                        sub_skill_tags=tuple(sub_tags),
                    )
                    _upsert_mapping(conn, mapping)

        # 3. Retrieve all final mappings to return
        results = []
        with connect(self.db_path) as conn:
            for term in source_terms:
                row = conn.execute(
                    """
                    SELECT * FROM knowledge_source_mappings
                    WHERE source_namespace = ? AND source_value = ?
                    """,
                    (source_namespace, term)
                ).fetchone()
                if row:
                    results.append(_mapping_from_row(row))
        return results

    def migrate_legacy_grading_mappings(self) -> LegacyAlignmentMigrationReport:
        self.initialize_database()
        copied = 0
        reused = 0
        conflicts: list[str] = []
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT * FROM knowledge_source_mappings
                WHERE source_namespace = 'grading_weak_point'
                  AND source_value LIKE '%·%'
                ORDER BY id
                """
            ).fetchall()
            for row in rows:
                legacy_value = str(row["source_value"])
                legacy_id, _, _ = legacy_value.partition("·")
                source_value = build_grading_knowledge_term(
                    legacy_id, legacy_value
                ).source_value
                target = conn.execute(
                    """
                    SELECT * FROM knowledge_source_mappings
                    WHERE source_namespace = 'grading_weak_point'
                      AND normalized_value = ?
                    ORDER BY CASE status
                        WHEN 'confirmed' THEN 0
                        WHEN 'rejected' THEN 1
                        ELSE 2
                    END, id
                    LIMIT 1
                    """,
                    (normalize_source_value(source_value),),
                ).fetchone()
                if target is not None:
                    same_decision = (
                        str(target["status"]) == str(row["status"])
                        and target["concept_id"] == row["concept_id"]
                    )
                    if same_decision:
                        reused += 1
                    elif (
                        str(target["status"]) == AlignmentStatus.CONFIRMED.value
                        and str(row["status"]) == AlignmentStatus.CONFIRMED.value
                    ):
                        conflicts.append(source_value)
                    continue
                _upsert_mapping(
                    conn,
                    KnowledgeSourceMapping(
                        source_namespace="grading_weak_point",
                        source_value=source_value,
                        concept_id=row["concept_id"],
                        status=AlignmentStatus(str(row["status"])),
                        confidence=float(row["confidence"] or 0.0),
                        sub_skill_tags=tuple(_json_list(row["sub_skill_tags"])),
                    ),
                    reviewed_by=row["reviewed_by"],
                    evidence={"legacy_source_value": legacy_value},
                )
                copied += 1
        return LegacyAlignmentMigrationReport(
            copied=copied,
            reused=reused,
            conflicts=tuple(sorted(set(conflicts))),
        )

    def resolve_for_training(
        self,
        source_namespace: str,
        source_value: str,
    ) -> AlignmentResolution:
        resolved = self.resolve(source_namespace, source_value)
        if resolved.status in {
            AlignmentStatus.CONFIRMED,
            AlignmentStatus.REJECTED,
        }:
            return resolved
        with connect(self.db_path) as conn:
            concept = _safe_automatic_concept(conn, source_value)
        if concept is None:
            return resolved
        self.confirm_mapping(
            source_namespace,
            source_value,
            concept.id,
            reviewed_by="system:exact-or-alias",
            evidence={"automatic_rule": "exact-or-registered-alias"},
        )
        return self.resolve(source_namespace, source_value)

    def revision_token(self) -> str:
        self.initialize_database()
        with connect(self.db_path) as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS item_count,
                       COALESCE(MAX(updated_at), '') AS latest_update
                FROM knowledge_source_mappings
                """
            ).fetchone()
        return f"{int(row['item_count'])}:{row['latest_update']}"

    def resolve(self, source_namespace: str, source_value: str) -> AlignmentResolution:
        self.initialize_database()
        namespace = normalize_source_value(source_namespace)
        normalized_value = normalize_source_value(source_value)
        display_value = KnowledgeSourceMapping(
            source_namespace=namespace,
            source_value=source_value,
            concept_id=None,
            status=AlignmentStatus.UNMAPPED,
            confidence=0.0,
        ).source_value
        with connect(self.db_path) as conn:
            persisted = conn.execute(
                """
                SELECT * FROM knowledge_source_mappings
                WHERE source_namespace = ? AND normalized_value = ?
                ORDER BY CASE status
                    WHEN 'confirmed' THEN 0
                    WHEN 'rejected' THEN 1
                    ELSE 2
                END, id
                LIMIT 1
                """,
                (namespace, normalized_value),
            ).fetchone()
            if persisted is not None:
                mapping = _mapping_from_row(persisted)
                concept = _load_concept(conn, mapping.concept_id)
                return AlignmentResolution(
                    source_namespace=namespace,
                    source_value=mapping.source_value,
                    status=mapping.status,
                    concept=concept,
                    confidence=mapping.confidence,
                    sub_skill_tags=mapping.sub_skill_tags,
                )

            concept, confidence = _suggest_concept(conn, display_value)
            if concept is not None:
                return AlignmentResolution(
                    source_namespace=namespace,
                    source_value=display_value,
                    status=AlignmentStatus.SUGGESTED,
                    concept=concept,
                    confidence=confidence,
                    sub_skill_tags=(),
                )
        return AlignmentResolution(
            source_namespace=namespace,
            source_value=display_value,
            status=AlignmentStatus.UNMAPPED,
            concept=None,
            confidence=0.0,
            sub_skill_tags=(),
        )


def _upsert_mapping(
    conn: sqlite3.Connection,
    mapping: KnowledgeSourceMapping,
    *,
    reviewed_by: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> KnowledgeSourceMapping:
    reviewed_at = "datetime('now','localtime')" if reviewed_by or mapping.status is not AlignmentStatus.SUGGESTED else "NULL"
    conn.execute(
        f"""
        INSERT INTO knowledge_source_mappings (
            source_namespace, source_value, normalized_value, concept_id,
            status, confidence, sub_skill_tags, evidence_json, reviewed_by, reviewed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, {reviewed_at})
        ON CONFLICT(source_namespace, source_value) DO UPDATE SET
            normalized_value = excluded.normalized_value,
            concept_id = excluded.concept_id,
            status = excluded.status,
            confidence = excluded.confidence,
            sub_skill_tags = excluded.sub_skill_tags,
            evidence_json = excluded.evidence_json,
            reviewed_by = excluded.reviewed_by,
            reviewed_at = {reviewed_at},
            updated_at = datetime('now','localtime')
        WHERE excluded.status <> 'suggested'
           OR knowledge_source_mappings.status = 'suggested'
        """,
        (
            mapping.source_namespace,
            mapping.source_value,
            mapping.normalized_value,
            mapping.concept_id,
            mapping.status.value,
            mapping.confidence,
            _json(list(mapping.sub_skill_tags)),
            _json(evidence or {}),
            _optional(reviewed_by),
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM knowledge_source_mappings
        WHERE source_namespace = ? AND source_value = ?
        """,
        (mapping.source_namespace, mapping.source_value),
    ).fetchone()
    return _mapping_from_row(row)


def _suggest_concept(
    conn: sqlite3.Connection,
    source_value: str,
) -> tuple[KnowledgeConcept | None, float]:
    normalized_source = normalize_source_value(source_value)
    if not normalized_source:
        return None, 0.0
    rows = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE status = 'active' ORDER BY id"
    ).fetchall()
    concepts = [_concept_from_row(row) for row in rows]
    registry_match = canonicalize_knowledge(source_value)

    best: tuple[float, KnowledgeConcept | None] = (0.0, None)
    for concept in concepts:
        candidates = (concept.canonical_key, concept.name, *concept.aliases)
        for candidate in candidates:
            normalized_candidate = normalize_source_value(candidate)
            if normalized_source == normalized_candidate:
                return concept, 0.95
            if registry_match is not None and normalize_source_value(registry_match.canonical_id) == concept.canonical_key:
                best = max(best, (0.9, concept), key=lambda item: item[0])
            if min(len(normalized_source), len(normalized_candidate)) >= 2 and (
                normalized_source in normalized_candidate or normalized_candidate in normalized_source
            ):
                best = max(best, (0.8, concept), key=lambda item: item[0])
            similarity = SequenceMatcher(None, normalized_source, normalized_candidate).ratio()
            if similarity >= 0.7:
                best = max(best, (min(0.89, similarity), concept), key=lambda item: item[0])
    return best[1], round(best[0], 4)


def _safe_automatic_concept(
    conn: sqlite3.Connection,
    source_value: str,
) -> KnowledgeConcept | None:
    normalized = normalize_source_value(source_value)
    if not normalized:
        return None
    concepts = [
        _concept_from_row(row)
        for row in conn.execute(
            "SELECT * FROM knowledge_concepts WHERE status = 'active' ORDER BY id"
        ).fetchall()
    ]
    registry_match = canonicalize_knowledge(source_value)
    for concept in concepts:
        exact_values = (concept.canonical_key, concept.name, *concept.aliases)
        if normalized in {
            normalize_source_value(value) for value in exact_values
        }:
            return concept
        if (
            registry_match is not None
            and normalize_source_value(registry_match.canonical_id)
            == concept.canonical_key
        ):
            return concept
    return None


def _load_concept(
    conn: sqlite3.Connection,
    concept_id: int | None,
) -> KnowledgeConcept | None:
    if concept_id is None:
        return None
    row = conn.execute(
        "SELECT * FROM knowledge_concepts WHERE id = ?",
        (int(concept_id),),
    ).fetchone()
    return _concept_from_row(row) if row is not None else None


def _concept_from_row(row: sqlite3.Row) -> KnowledgeConcept:
    aliases = _json_list(row["aliases_json"])
    return KnowledgeConcept(
        id=int(row["id"]),
        canonical_key=str(row["canonical_key"]),
        name=str(row["name"]),
        aliases=tuple(aliases),
    )


def _mapping_from_row(row: sqlite3.Row) -> KnowledgeSourceMapping:
    sub_skills = _json_list(row["sub_skill_tags"]) if "sub_skill_tags" in row.keys() else []
    return KnowledgeSourceMapping(
        source_namespace=str(row["source_namespace"]),
        source_value=str(row["source_value"]),
        concept_id=int(row["concept_id"]) if row["concept_id"] is not None else None,
        status=AlignmentStatus(str(row["status"])),
        confidence=float(row["confidence"]),
        sub_skill_tags=tuple(sub_skills),
    )


def _json_list(value: object) -> list[str]:
    try:
        loaded = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in loaded] if isinstance(loaded, list) else []


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
