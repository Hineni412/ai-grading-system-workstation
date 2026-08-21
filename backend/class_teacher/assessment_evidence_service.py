from __future__ import annotations

import hashlib
import hmac
import json
import re
import csv
from io import BytesIO, StringIO
from contextlib import closing
from datetime import UTC, datetime
from typing import Any, Callable, Protocol
from uuid import uuid4

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
from .existing_student_roster import ExistingStudent, ExistingStudentRosterSource
from .secure_repository import EncryptedObjectRepository


_RESULT_STATES = {
    "normal",
    "absent",
    "exempt",
    "missing",
    "incomplete",
    "makeup",
}


def _iso() -> str:
    return datetime.now(UTC).isoformat()


_NAME_HEADER = re.compile(r"姓名|名字")
_SUB_HEADER_LABELS = {
    "得分",
    "成绩",
    "分数",
    "等级",
    "校次",
    "班次",
    "排名",
    "校名",
    "班名",
    "得分率",
    "满分",
}


def _split_header(
    rows: list[list[str]],
) -> tuple[list[str], list[list[str]]]:
    """Locate the real header row and merge a two-level header if present.

    School grade exports often start with a merged title row, followed by a
    group header row (序号/姓名/数学/...) and a sub header row (得分/等级/...).
    The first row containing 姓名/名字 is treated as the header; when the row
    right below it is a sub header row, the two are joined into single labels
    such as "数学-得分". Plain single-header sheets keep the old behavior.
    """
    header_index = 0
    for index, row in enumerate(rows[:10]):
        if any(_NAME_HEADER.search(cell) for cell in row):
            header_index = index
            break
    header_row = rows[header_index]
    data_start = header_index + 1
    sub_row: list[str] | None = None
    if data_start < len(rows):
        candidate = rows[data_start]
        if sum(1 for cell in candidate if cell in _SUB_HEADER_LABELS) >= 2:
            sub_row = candidate
            data_start += 1
    width = max(len(row) for row in rows[header_index:])
    sub_values = set(sub_row) if sub_row is not None else set()

    def _cell(row: list[str], index: int) -> str:
        return row[index] if index < len(row) else ""

    headers: list[str] = []
    group = ""
    for index in range(width):
        top = _cell(header_row, index)
        # Some writers repeat the sub labels (得分/等级/...) inside the merged
        # group row; treat those as blank anchors so the group name carries.
        if top and top not in sub_values:
            group = top
        sub = _cell(sub_row, index) if sub_row is not None else ""
        parts = [part for part in (group, sub) if part]
        # Avoid "姓名-姓名" when a merged label repeats on both levels.
        if len(parts) == 2 and parts[0] == parts[1]:
            parts.pop()
        headers.append("-".join(parts) or f"未命名列 {index + 1}")
    return headers, rows[data_start:]


class AssessmentEvidenceSource(Protocol):
    def read(self, query: dict[str, object]) -> dict[str, object]: ...


class ExistingMathAssessmentAdapter:
    """Read-only boundary around an injected math-summary reader."""

    def __init__(
        self,
        reader: Callable[[dict[str, object]], dict[str, object]],
    ) -> None:
        self._reader = reader

    def read(self, query: dict[str, object]) -> dict[str, object]:
        batch = dict(self._reader(dict(query)))
        batch["source_kind"] = "existing_math"
        batch["read_only"] = True
        return batch


class ConfirmedSpreadsheetAdapter:
    """Turns an already teacher-confirmed preview into a neutral batch."""

    @staticmethod
    def preview(
        *,
        file_name: str,
        content: bytes,
        sheet_name: str | None = None,
    ) -> dict[str, object]:
        if not content or len(content) > 10 * 1024 * 1024:
            raise VaultError(
                "assessment_file_size_invalid",
                "成绩文件不能为空且不能超过 10 MB",
                status_code=422,
            )
        lower_name = file_name.casefold()
        if lower_name.endswith(".csv"):
            decoded = None
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    decoded = content.decode(encoding)
                    break
                except UnicodeDecodeError:
                    continue
            if decoded is None:
                raise VaultError(
                    "assessment_csv_encoding_invalid",
                    "CSV 文件编码无法识别",
                    status_code=422,
                )
            rows = [
                [str(cell).strip() for cell in row]
                for row in csv.reader(StringIO(decoded))
                if any(str(cell).strip() for cell in row)
            ]
            sheets = ["CSV"]
            selected_sheet = "CSV"
        elif lower_name.endswith(".xlsx"):
            try:
                from openpyxl import load_workbook

                workbook = load_workbook(
                    BytesIO(content),
                    read_only=True,
                    data_only=True,
                )
                sheets = list(workbook.sheetnames)
                selected_sheet = sheet_name or sheets[0]
                if selected_sheet not in sheets:
                    raise VaultError(
                        "assessment_sheet_not_found",
                        "选择的工作表不存在",
                        status_code=422,
                    )
                worksheet = workbook[selected_sheet]
                # Some tools write a wrong declared dimension (e.g. A1:A1);
                # rescan so trailing columns are not silently dropped.
                worksheet.reset_dimensions()
                rows = [
                    [
                        "" if cell is None else str(cell).strip()
                        for cell in row
                    ]
                    for row in worksheet.iter_rows(
                        min_row=1,
                        max_row=5002,
                        values_only=True,
                    )
                    if any(cell is not None and str(cell).strip() for cell in row)
                ]
                workbook.close()
            except VaultError:
                raise
            except Exception as exc:
                raise VaultError(
                    "assessment_xlsx_invalid",
                    "XLSX 文件无法读取或已经损坏",
                    status_code=422,
                ) from exc
        else:
            raise VaultError(
                "assessment_file_type_invalid",
                "只支持 CSV 或 XLSX 成绩文件",
                status_code=422,
            )
        if not rows:
            raise VaultError(
                "assessment_sheet_empty",
                "成绩工作表为空",
                status_code=422,
            )
        headers, data_rows = _split_header(rows)
        width = len(headers)
        preview_rows = [
            {
                headers[index]: (
                    row[index] if index < len(row) else ""
                )
                for index in range(width)
            }
            for row in data_rows[:5000]
        ]
        return {
            "file_name": file_name,
            "sheet_names": sheets,
            "selected_sheet": selected_sheet,
            "headers": headers,
            "rows": preview_rows,
            "preview_row_count": len(preview_rows),
            "truncated": len(data_rows) > 5000,
            "raw_file_retained": False,
            "temporary_file_created": False,
        }

    def read(self, query: dict[str, object]) -> dict[str, object]:
        if not bool(query.get("teacher_confirmed")):
            raise VaultError(
                "assessment_preview_confirmation_required",
                "成绩预览必须由教师确认后才能进入证据库",
                status_code=422,
            )
        return {
            "source_kind": "confirmed_spreadsheet",
            "read_only": True,
            "teacher_confirmed": True,
            "source_label": str(
                query.get("source_label") or "教师确认的表格预览"
            ),
            "assessments": list(query.get("assessments") or []),
        }


class AssessmentEvidenceService:
    def __init__(
        self,
        database: EncryptedDatabase,
        repository: EncryptedObjectRepository,
        key_provider: Callable[[str], bytes],
        subject_ensurer: Callable[..., str] | None = None,
        roster_source: ExistingStudentRosterSource | None = None,
    ) -> None:
        self.database = database
        self.repository = repository
        self._key_provider = key_provider
        self._subject_ensurer = subject_ensurer
        self._roster_source = roster_source

    def confirm_batch(
        self,
        *,
        token: str,
        operation_id: str,
        batch: dict[str, object],
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "evidence.batch.confirm")
        if replay is not None:
            return replay
        source_kind = str(batch.get("source_kind") or "")
        if source_kind not in {"existing_math", "confirmed_spreadsheet"}:
            raise VaultError(
                "assessment_source_invalid",
                "学业证据来源无效",
                status_code=422,
            )
        if not bool(batch.get("teacher_confirmed")):
            raise VaultError(
                "assessment_confirmation_required",
                "学业证据必须经过教师确认",
                status_code=422,
            )
        assessments = list(batch.get("assessments") or [])
        if not assessments:
            raise VaultError(
                "assessment_batch_empty",
                "没有可确认的考试证据",
                status_code=422,
            )
        source_signature = {
            "source_kind": source_kind,
            "source_label": str(batch.get("source_label") or ""),
            "assessments": assessments,
        }
        fingerprint = hmac.new(
            vmk,
            json.dumps(
                source_signature,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8"),
            hashlib.sha256,
        ).digest()
        with closing(self.database.connect()) as connection:
            duplicate = connection.execute(
                """
                SELECT import_id FROM source_fingerprints
                WHERE source_fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
        if duplicate is not None:
            return {
                "import_id": str(duplicate["import_id"]),
                "duplicate": True,
                "created_assessments": 0,
                "created_results": 0,
                "model_enabled": False,
                "physical_request_count": 0,
            }
        import_id = uuid4().hex
        import_object_id = f"assessment-import-{import_id}"
        timestamp = _iso()
        created_results = 0
        with closing(self.database.connect()) as connection:
            with connection:
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=import_object_id,
                    object_type="assessment_import",
                    payload={
                        "source_kind": source_kind,
                        "source_label": str(
                            batch.get("source_label") or ""
                        ),
                        "teacher_confirmed": True,
                        "read_only_source": True,
                        "raw_file_retained": False,
                        "model_enabled": False,
                        "physical_request_count": 0,
                    },
                )
                connection.execute(
                    """
                    INSERT INTO assessment_imports (
                        import_id, payload_object_id, source_kind,
                        status, created_at
                    ) VALUES (?, ?, ?, 'confirmed', ?)
                    """,
                    (
                        import_id,
                        import_object_id,
                        source_kind,
                        timestamp,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO source_fingerprints (
                        source_fingerprint, import_id, created_at
                    ) VALUES (?, ?, ?)
                    """,
                    (fingerprint, import_id, timestamp),
                )
                for assessment in assessments:
                    created_results += self._insert_assessment(
                        connection,
                        vmk=vmk,
                        import_id=import_id,
                        assessment=dict(assessment),
                    )
                result = {
                    "import_id": import_id,
                    "duplicate": False,
                    "created_assessments": len(assessments),
                    "created_results": created_results,
                    "model_enabled": False,
                    "physical_request_count": 0,
                }
                self._remember(
                    connection,
                    operation_id,
                    "evidence.batch.confirm",
                    result,
                )
        return result

    def list_subject_evidence(
        self,
        *,
        token: str,
        subject_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            self._subject_exists(connection, subject_id)
            rows = connection.execute(
                """
                SELECT sr.*, a.occurred_on, a.payload_object_id AS assessment_object,
                       ai.source_kind, ai.payload_object_id AS import_object,
                       ev.evidence_version_id, ev.version,
                       ev.payload_object_id AS evidence_object, ev.state
                FROM subject_results sr
                JOIN assessments a ON a.assessment_id = sr.assessment_id
                JOIN assessment_imports ai ON ai.import_id = a.import_id
                JOIN evidence_versions ev ON ev.result_id = sr.result_id
                WHERE sr.subject_id = ? AND ev.state = 'active'
                ORDER BY a.occurred_on, sr.created_at
                """,
                (subject_id,),
            ).fetchall()
            items = [
                self._evidence_from_row(connection, vmk, row)
                for row in rows
            ]
        return {
            "subject_id": subject_id,
            "items": items,
            "model_enabled": False,
            "physical_request_count": 0,
        }

    def compare(
        self,
        *,
        token: str,
        older_evidence_version_id: str,
        newer_evidence_version_id: str,
    ) -> dict[str, object]:
        older = self.get_evidence(
            token=token,
            evidence_version_id=older_evidence_version_id,
        )
        newer = self.get_evidence(
            token=token,
            evidence_version_id=newer_evidence_version_id,
        )
        if older["subject_id"] != newer["subject_id"]:
            raise VaultError(
                "assessment_subject_mismatch",
                "只能比较同一学生的证据",
                status_code=422,
            )
        status, limitations = self._comparability(older, newer)
        delta = None
        if (
            status == "directly_comparable"
            and
            older["result_state"] in {"normal", "makeup"}
            and newer["result_state"] in {"normal", "makeup"}
            and older.get("score") is not None
            and newer.get("score") is not None
        ):
            delta = float(newer["score"]) - float(older["score"])
        return {
            "older_evidence_version_id": older_evidence_version_id,
            "newer_evidence_version_id": newer_evidence_version_id,
            "comparability": status,
            "limitations": limitations,
            "score_delta": delta,
        }

    def trend(
        self,
        *,
        token: str,
        subject_id: str,
        subject_name: str,
    ) -> dict[str, object]:
        items = [
            item
            for item in self.list_subject_evidence(
                token=token,
                subject_id=subject_id,
            )["items"]
            if item["subject_name"] == subject_name
        ]
        comparisons = [
            self._comparability(items[index - 1], items[index])
            for index in range(1, len(items))
        ]
        trend_allowed = (
            len(items) >= 3
            and all(status == "directly_comparable" for status, _ in comparisons)
        )
        return {
            "subject_id": subject_id,
            "subject_name": subject_name,
            "evidence_count": len(items),
            "trend_allowed": trend_allowed,
            "conclusion": (
                "口径相近的证据达到三次，可描述变化趋势"
                if trend_allowed
                else "证据不足或口径不同，不能称为趋势"
            ),
            "comparison_basis": [
                {
                    "from": items[index - 1]["evidence_version_id"],
                    "to": items[index]["evidence_version_id"],
                    "comparability": comparisons[index - 1][0],
                    "limitations": comparisons[index - 1][1],
                }
                for index in range(1, len(items))
            ],
        }

    def get_evidence(
        self,
        *,
        token: str,
        evidence_version_id: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT sr.*, a.occurred_on,
                       a.payload_object_id AS assessment_object,
                       ai.source_kind, ai.payload_object_id AS import_object,
                       ev.evidence_version_id, ev.version,
                       ev.payload_object_id AS evidence_object, ev.state
                FROM evidence_versions ev
                JOIN subject_results sr ON sr.result_id = ev.result_id
                JOIN assessments a ON a.assessment_id = sr.assessment_id
                JOIN assessment_imports ai ON ai.import_id = a.import_id
                WHERE ev.evidence_version_id = ?
                """,
                (evidence_version_id,),
            ).fetchone()
            if row is None:
                raise VaultError(
                    "assessment_evidence_not_found",
                    "学业证据不存在",
                    status_code=404,
                )
            return self._evidence_from_row(connection, vmk, row)

    def supersede_evidence(
        self,
        *,
        token: str,
        operation_id: str,
        evidence_version_id: str,
        reason: str,
    ) -> dict[str, object]:
        vmk = self._key_provider(token)
        replay = self._idempotent(operation_id, "evidence.supersede")
        if replay is not None:
            return replay
        clean_reason = self._text(reason, "证据修订原因", 1000)
        with closing(self.database.connect()) as connection:
            with connection:
                row = connection.execute(
                    """
                    SELECT * FROM evidence_versions
                    WHERE evidence_version_id = ?
                    """,
                    (evidence_version_id,),
                ).fetchone()
                if row is None:
                    raise VaultError(
                        "assessment_evidence_not_found",
                        "学业证据不存在",
                        status_code=404,
                    )
                payload, revision = self.repository.get(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                )
                payload["superseded_reason"] = clean_reason
                payload["superseded_at"] = _iso()
                self.repository.put(
                    connection,
                    vmk=vmk,
                    object_id=str(row["payload_object_id"]),
                    object_type="assessment_evidence_version",
                    payload=payload,
                    expected_revision=revision,
                )
                connection.execute(
                    """
                    UPDATE evidence_versions SET state = 'superseded'
                    WHERE evidence_version_id = ?
                    """,
                    (evidence_version_id,),
                )
                connection.execute(
                    """
                    UPDATE attention_cards SET state = 'invalidated',
                        updated_at = ?
                    WHERE evidence_version_id = ? AND state = 'draft'
                    """,
                    (_iso(), evidence_version_id),
                )
                result = {
                    "evidence_version_id": evidence_version_id,
                    "superseded": True,
                }
                self._remember(
                    connection,
                    operation_id,
                    "evidence.supersede",
                    result,
                )
        return result

    def _insert_assessment(
        self,
        connection: Any,
        *,
        vmk: bytes,
        import_id: str,
        assessment: dict[str, object],
    ) -> int:
        title = self._text(assessment.get("title"), "考试名称", 500)
        subject_name = self._text(
            assessment.get("subject_name"),
            "学科名称",
            120,
        )
        occurred_on = self._text(
            assessment.get("occurred_on"),
            "考试日期",
            40,
        )
        try:
            datetime.fromisoformat(occurred_on)
        except ValueError as exc:
            raise VaultError(
                "assessment_date_invalid",
                "考试日期无效",
                status_code=422,
            ) from exc
        results = list(assessment.get("results") or [])
        if not results:
            raise VaultError(
                "assessment_results_empty",
                "考试没有学生结果",
                status_code=422,
            )
        assessment_id = uuid4().hex
        object_id = f"assessment-{assessment_id}"
        assessment_payload = {
            "title": title,
            "subject_name": subject_name,
            "max_score": self._optional_number(assessment.get("max_score")),
            "rank_scope": str(assessment.get("rank_scope") or "") or None,
            "participant_count": self._optional_integer(
                assessment.get("participant_count")
            ),
            "assessment_nature": str(
                assessment.get("assessment_nature") or ""
            ) or None,
            "rank_origin": str(assessment.get("rank_origin") or "") or None,
            "cohort_key": str(assessment.get("cohort_key") or "") or None,
            "ranking_rule_version": str(
                assessment.get("ranking_rule_version") or ""
            ) or None,
        }
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="assessment",
            payload=assessment_payload,
        )
        connection.execute(
            """
            INSERT INTO assessments (
                assessment_id, import_id, payload_object_id,
                occurred_on, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (assessment_id, import_id, object_id, occurred_on, _iso()),
        )
        session_id = self._ensure_session(
            connection,
            vmk=vmk,
            assessment=assessment,
            fallback_title=title,
            fallback_date=occurred_on,
        )
        measure_role = str(assessment.get("measure_role") or "subject_score")
        if measure_role not in {"subject_score", "total_score"}:
            raise VaultError("assessment_measure_role_invalid", "学业指标类型无效", status_code=422)
        measure_key = hmac.new(
            vmk,
            f"academic-measure|{subject_name}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        for result in results:
            evidence_id = self._insert_result(
                connection,
                vmk=vmk,
                assessment_id=assessment_id,
                assessment_payload=assessment_payload,
                occurred_on=occurred_on,
                session_id=session_id,
                measure_role=measure_role,
                measure_key=measure_key,
                result=dict(result),
            )
            connection.execute(
                """
                INSERT INTO assessment_session_members (
                    session_id, evidence_version_id, measure_role,
                    measure_key, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, evidence_id, measure_role, measure_key, _iso()),
            )
        return len(results)

    def _insert_result(
        self,
        connection: Any,
        *,
        vmk: bytes,
        assessment_id: str,
        assessment_payload: dict[str, object],
        occurred_on: str,
        session_id: str,
        measure_role: str,
        measure_key: bytes,
        result: dict[str, object],
    ) -> str:
        subject_id = str(result.get("subject_id") or "")
        if not subject_id:
            subject_id = self._ensure_subject_from_identity(
                connection, vmk=vmk, result=result
            )
        self._subject_exists(connection, subject_id)
        result_state = str(result.get("result_state") or "")
        if result_state not in _RESULT_STATES:
            raise VaultError(
                "assessment_result_state_invalid",
                "成绩状态无效",
                status_code=422,
            )
        score = self._optional_number(result.get("score"))
        if result_state in {"normal", "makeup"} and score is None:
            raise VaultError(
                "assessment_score_required",
                "正常成绩和补考成绩必须保留数值，0 分也是有效成绩",
                status_code=422,
            )
        if result_state not in {"normal", "makeup"} and score is not None:
            raise VaultError(
                "assessment_score_state_conflict",
                "缺考、免考、缺失或未完成不能伪装成数值成绩",
                status_code=422,
            )
        result_id = uuid4().hex
        result_object_id = f"subject-result-{result_id}"
        result_payload = {
            "score": score,
            "result_state": result_state,
            "teacher_note": str(result.get("teacher_note") or "") or None,
        }
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=result_object_id,
            object_type="subject_result",
            payload=result_payload,
        )
        connection.execute(
            """
            INSERT INTO subject_results (
                result_id, assessment_id, subject_id,
                payload_object_id, result_state, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                assessment_id,
                subject_id,
                result_object_id,
                result_state,
                _iso(),
            ),
        )
        rank = self._optional_integer(result.get("rank"))
        class_rank = self._optional_integer(result.get("class_rank"))
        if rank is not None or class_rank is not None:
            rank_context_id = uuid4().hex
            rank_object_id = f"rank-context-{rank_context_id}"
            self.repository.put(
                connection,
                vmk=vmk,
                object_id=rank_object_id,
                object_type="rank_context",
                payload={
                    "rank": rank,
                    "class_rank": class_rank,
                    "rank_scope": assessment_payload["rank_scope"],
                    "participant_count": assessment_payload[
                        "participant_count"
                    ],
                    "rank_origin": str(
                        result.get("rank_origin")
                        or assessment_payload.get("rank_origin")
                        or ""
                    ) or None,
                    "cohort_key": str(
                        result.get("cohort_key")
                        or assessment_payload.get("cohort_key")
                        or ""
                    ) or None,
                    "ranking_rule_version": str(
                        result.get("ranking_rule_version")
                        or assessment_payload.get("ranking_rule_version")
                        or ""
                    ) or None,
                },
            )
            connection.execute(
                """
                INSERT INTO rank_contexts (
                    rank_context_id, result_id,
                    payload_object_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (rank_context_id, result_id, rank_object_id, _iso()),
            )
        evidence_id = uuid4().hex
        evidence_object_id = f"evidence-version-{evidence_id}"
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=evidence_object_id,
            object_type="assessment_evidence_version",
            payload={
                **assessment_payload,
                **result_payload,
                "occurred_on": occurred_on,
                "session_id": session_id,
                "measure_role": measure_role,
                "measure_key": measure_key.hex(),
                "source_version": hmac.new(
                    vmk,
                    json.dumps(
                        {
                            "assessment": assessment_payload,
                            "result": result_payload,
                            "occurred_on": occurred_on,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    hashlib.sha256,
                ).hexdigest(),
                "teacher_confirmed_at": _iso(),
                "source_snapshot": True,
                "source_write_back": False,
            },
        )
        connection.execute(
            """
            INSERT INTO evidence_versions (
                evidence_version_id, result_id, version,
                payload_object_id, state, created_at
            ) VALUES (?, ?, 1, ?, 'active', ?)
            """,
            (evidence_id, result_id, evidence_object_id, _iso()),
        )
        return evidence_id

    def _ensure_session(
        self,
        connection: Any,
        *,
        vmk: bytes,
        assessment: dict[str, object],
        fallback_title: str,
        fallback_date: str,
    ) -> str:
        supplied = dict(assessment.get("session") or {})
        required = (
            "title",
            "academic_year",
            "term",
            "grade",
            "exam_type",
            "comparison_series",
            "occurred_on",
            "source_reference",
        )
        metadata_complete = all(str(supplied.get(key) or "").strip() for key in required)
        payload = {
            "title": str(supplied.get("title") or fallback_title),
            "academic_year": supplied.get("academic_year"),
            "term": supplied.get("term"),
            "grade": supplied.get("grade"),
            "exam_type": supplied.get("exam_type"),
            "comparison_series": supplied.get("comparison_series"),
            "occurred_on": str(supplied.get("occurred_on") or fallback_date),
            "source_reference": supplied.get("source_reference"),
            "teacher_confirmed_at": str(supplied.get("teacher_confirmed_at") or _iso()),
            "raw_file_retained": False,
            "metadata_complete": metadata_complete,
        }
        signature = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fingerprint = hmac.new(vmk, f"assessment-session|{signature}".encode("utf-8"), hashlib.sha256).digest()
        row = connection.execute(
            "SELECT session_id FROM assessment_sessions WHERE source_fingerprint = ?",
            (fingerprint,),
        ).fetchone()
        if row is not None:
            return str(row["session_id"])
        session_id = uuid4().hex
        object_id = f"assessment-session-{session_id}"
        self.repository.put(
            connection,
            vmk=vmk,
            object_id=object_id,
            object_type="assessment_session",
            payload=payload,
        )
        connection.execute(
            """
            INSERT INTO assessment_sessions (
                session_id, payload_object_id, source_fingerprint, state,
                metadata_complete, created_at, updated_at
            ) VALUES (?, ?, ?, 'active', ?, ?, ?)
            """,
            (session_id, object_id, fingerprint, int(metadata_complete), _iso(), _iso()),
        )
        return session_id

    def _evidence_from_row(
        self,
        connection: Any,
        vmk: bytes,
        row: Any,
    ) -> dict[str, object]:
        payload, _ = self.repository.get(
            connection,
            vmk=vmk,
            object_id=str(row["evidence_object"]),
        )
        rank_row = connection.execute(
            """
            SELECT payload_object_id FROM rank_contexts
            WHERE result_id = ?
            """,
            (str(row["result_id"]),),
        ).fetchone()
        rank_context = None
        if rank_row is not None:
            rank_context, _ = self.repository.get(
                connection,
                vmk=vmk,
                object_id=str(rank_row["payload_object_id"]),
            )
        return {
            "evidence_version_id": str(row["evidence_version_id"]),
            "version": int(row["version"]),
            "state": str(row["state"]),
            "result_id": str(row["result_id"]),
            "subject_id": str(row["subject_id"]),
            "result_state": str(row["result_state"]),
            "source_kind": str(row["source_kind"]),
            **payload,
            "rank_context": rank_context,
        }

    @staticmethod
    def _comparability(
        older: dict[str, object],
        newer: dict[str, object],
    ) -> tuple[str, list[str]]:
        limitations: list[str] = []
        if (
            older.get("subject_name") != newer.get("subject_name")
            or older.get("max_score") is None
            or newer.get("max_score") is None
            or not older.get("assessment_nature")
            or not newer.get("assessment_nature")
        ):
            return "insufficient_information", ["学科、满分或考试性质不完整"]
        special_states = {
            str(older.get("result_state")),
            str(newer.get("result_state")),
        }
        if special_states - {"normal", "makeup"}:
            return "not_comparable", ["存在缺考、免考、缺失或未完成"]
        if older.get("assessment_nature") != newer.get("assessment_nature"):
            return "not_comparable", ["考试性质不同"]
        if older.get("max_score") != newer.get("max_score"):
            limitations.append("满分不同")
        if older.get("rank_scope") != newer.get("rank_scope"):
            limitations.append("排名范围不同")
        if older.get("participant_count") != newer.get("participant_count"):
            limitations.append("参考人数不同")
        if "makeup" in special_states:
            limitations.append("包含补考")
        if limitations:
            return "reference_only", limitations
        return "directly_comparable", []

    def _ensure_subject_from_identity(
        self,
        connection: Any,
        *,
        vmk: bytes,
        result: dict[str, object],
    ) -> str:
        identity = result.get("subject_identity")
        if not isinstance(identity, dict):
            raise VaultError(
                "support_subject_not_found",
                "学生支持对象不存在",
                status_code=404,
            )
        display_name = str(identity.get("display_name") or "").strip()
        if not display_name:
            raise VaultError(
                "assessment_subject_identity_invalid",
                "新建学生档案必须提供姓名",
                status_code=422,
            )
        if self._subject_ensurer is None:
            raise VaultError(
                "assessment_subject_ensurer_unavailable",
                "当前部署不支持随成绩登记新建学生档案",
                status_code=422,
            )
        class_label = str(identity.get("class_label") or "").strip() or None
        student_code = str(identity.get("student_code") or "").strip() or None
        # 优先按花名册身份建档（班级+姓名唯一命中，或姓名全校唯一），
        # 保证「一个学生自始至终只有一条档案」；花名册查不到才退回临时身份。
        roster_student = self._match_roster_student(class_label, display_name)
        if roster_student is not None:
            return self._subject_ensurer(
                connection,
                vmk=vmk,
                source_student_id=roster_student.source_key,
                display_name=roster_student.display_name,
                class_label=roster_student.class_label,
                student_code=roster_student.student_code,
            )
        return self._subject_ensurer(
            connection,
            vmk=vmk,
            source_student_id=(
                f"evidence-upload|{class_label or ''}|{student_code or display_name}"
            ),
            display_name=display_name,
            class_label=class_label,
            student_code=student_code,
        )

    def _match_roster_student(
        self,
        class_label: str | None,
        display_name: str,
    ) -> ExistingStudent | None:
        if self._roster_source is None:
            return None
        students, _revision = self._roster_source.snapshot()
        if class_label:
            matches = [
                student
                for student in students
                if student.class_label == class_label
                and student.display_name == display_name
            ]
            if len(matches) == 1:
                return matches[0]
            if matches:
                return None
        matches = [
            student for student in students if student.display_name == display_name
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _subject_exists(connection: Any, subject_id: str) -> None:
        if connection.execute(
            "SELECT 1 FROM student_subject_links WHERE subject_id = ?",
            (subject_id,),
        ).fetchone() is None:
            raise VaultError(
                "support_subject_not_found",
                "学生支持对象不存在",
                status_code=404,
            )

    def _idempotent(
        self,
        operation_id: str,
        operation_type: str,
    ) -> dict[str, object] | None:
        with closing(self.database.connect()) as connection:
            row = connection.execute(
                """
                SELECT operation_type, result_json FROM idempotency_ledger
                WHERE operation_id = ?
                """,
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        if str(row["operation_type"]) != operation_type:
            raise VaultError(
                "vault_operation_conflict",
                "此操作编号已经用于另一项操作",
                status_code=409,
            )
        return dict(json.loads(str(row["result_json"])))

    @staticmethod
    def _remember(
        connection: Any,
        operation_id: str,
        operation_type: str,
        result: dict[str, object],
    ) -> None:
        connection.execute(
            """
            INSERT INTO idempotency_ledger (
                operation_id, operation_type, result_json, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                operation_id,
                operation_type,
                json.dumps(result, sort_keys=True),
                _iso(),
            ),
        )

    @staticmethod
    def _text(value: object, label: str, maximum: int) -> str:
        clean = str(value or "").strip()
        if not clean or len(clean) > maximum:
            raise VaultError(
                "assessment_text_invalid",
                f"{label}不能为空且不能超过 {maximum} 个字符",
                status_code=422,
            )
        return clean

    @staticmethod
    def _optional_number(value: object) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise VaultError(
                "assessment_number_invalid",
                "成绩或满分必须是数字",
                status_code=422,
            ) from exc

    @staticmethod
    def _optional_integer(value: object) -> int | None:
        if value is None or value == "":
            return None
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise VaultError(
                "assessment_integer_invalid",
                "排名或人数必须是整数",
                status_code=422,
            ) from exc
        if parsed < 0:
            raise VaultError(
                "assessment_integer_invalid",
                "排名或人数不能为负数",
                status_code=422,
            )
        return parsed


__all__ = [
    "AssessmentEvidenceService",
    "AssessmentEvidenceSource",
    "ConfirmedSpreadsheetAdapter",
    "ExistingMathAssessmentAdapter",
]
