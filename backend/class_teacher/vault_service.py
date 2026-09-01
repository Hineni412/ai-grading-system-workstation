from __future__ import annotations

import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.workspaces.contracts import WorkspaceContext

from .encrypted_database import EncryptedDatabase
from .errors import VaultError, unsupported_database_format_error
from .secure_repository import EncryptedObjectRepository


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


class VaultService:
    """Compose the plaintext class-teacher runtime and its business services."""

    _PLAINTEXT_KEY = b"class-teacher-plaintext-debug-key"
    _CLEANUP_INTERVAL_SECONDS = 60.0

    def __init__(
        self,
        context: WorkspaceContext,
        *,
        model_gateway=None,
        workspace_ai_task_port=None,
    ) -> None:
        self._runtime_lock = threading.RLock()
        self._maintenance_lock = threading.Lock()
        self._runtime_prepared = False
        self._unsupported_database_detected = False
        self._last_cleanup = float("-inf")
        self.workspace_ai_task_port = workspace_ai_task_port
        self.workspace_model_gateway = model_gateway
        self.database = EncryptedDatabase(context)
        self.repository = EncryptedObjectRepository()
        from .ordinary_database import OrdinaryWorkDatabase
        from .model_approval import ModelApproval
        from .work_graph import WorkGraph

        self.ordinary_database = OrdinaryWorkDatabase(context)
        self.work = WorkGraph(
            self.ordinary_database,
            model_gateway=model_gateway,
        )
        self.model_approval = ModelApproval(
            self.ordinary_database,
            self.database,
            self.repository,
            self._key_provider,
            model_gateway,
        )
        from .daily_timetable_service import DailyTimetableService

        self.daily_timetable = DailyTimetableService(self.ordinary_database)
        from .sensitive_work_projection import SensitiveWorkProjection

        self.projections = SensitiveWorkProjection(
            self.database,
            self.repository,
            self._key_provider,
            self.work,
        )
        from .action_ledger_service import ActionLedgerService
        from .sop_workflow_service import SopWorkflowService
        from .sop_baseline_service import SopBaselineService
        from .support_record_service import SupportRecordService
        from .student_card_service import StudentCardService
        from .assessment_evidence_service import AssessmentEvidenceService
        from .attention_service import AttentionService
        from .local_speech import LocalSpeechTranscriber

        self.actions = ActionLedgerService(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.sop = SopWorkflowService(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.sop_baselines = SopBaselineService(self.sop)
        self.support = SupportRecordService(
            self.database,
            self.repository,
            self._key_provider,
            self.projections,
        )
        from .class_roster_service import ClassRosterService
        from .existing_student_roster import SqliteExistingStudentRosterSource

        roster_source = SqliteExistingStudentRosterSource(
            Path(getattr(context.paths, "db_path", context.root / "grading.db"))
        )
        self.class_roster = ClassRosterService(
            self.database,
            self.repository,
            self._key_provider,
            self.support,
            roster_source,
        )
        from .daily_table_service import DailyTableService

        self.daily_tables = DailyTableService(
            self.database,
            self._key_provider,
            roster_source,
        )
        self.student_cards = StudentCardService(
            self.database,
            self.repository,
            self._key_provider,
            self.support,
            self.model_approval,
            self.work,
            self.projections,
        )
        from .support_plan_draft_service import SupportPlanDraftService

        self.support_plan_drafts = SupportPlanDraftService(
            model_gateway=self.workspace_model_gateway,
            student_cards=self.student_cards,
            support=self.support,
        )
        self.evidence = AssessmentEvidenceService(
            self.database,
            self.repository,
            self._key_provider,
            self.support.ensure_subject_in_connection,
            roster_source=roster_source,
            projections=self.projections,
        )
        self.attention = AttentionService(
            self.database,
            self.repository,
            self._key_provider,
            self.evidence,
            self.actions,
        )
        self.local_speech = LocalSpeechTranscriber(
            Path(context.paths.project_root),
        )
        from .student_academic_analysis import StudentAcademicAnalysis

        self.academic = StudentAcademicAnalysis(
            self.database,
            self.repository,
            self._key_provider,
            self.evidence,
            self.attention,
            self.projections,
        )
        self.student_cards.academic_summarizer = self.academic.ai_summary
        # 方案完成评「有效」时由支持记录服务回写学生当前档案。
        self.support.student_cards = self.student_cards
        # SOP 档案待确认草稿的确认动作要写支持记录与当前档案（迟绑定避免构造环）。
        self.sop.support_records = self.support
        self.sop.student_cards = self.student_cards
        self._initialize_business_facades()
        from .intake import ClassTeacherIntake

        self.intake = ClassTeacherIntake(
            ordinary_database=self.ordinary_database,
            class_roster=self.class_roster,
            domain_database=self.database,
            repository=self.repository,
            key_provider=self._key_provider,
            support=self.support,
            work=self.work,
            sop=self.sop,
            sop_baselines=self.sop_baselines,
            student_cards=self.student_cards,
            model_gateway=self.workspace_model_gateway,
            ai_tasks=self.workspace_ai_task_port,
        )

    def _initialize_business_facades(self) -> None:
        from .affair_workspace import AffairWorkspace
        from .class_overview import ClassOverview
        from .student_directory import StudentDirectory

        self.student_directory = StudentDirectory(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.class_overview = ClassOverview(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.affairs = AffairWorkspace(
            self.database,
            self.repository,
            self._key_provider,
            self.sop,
            self.projections,
        )

    def prepare_existing_plaintext_runtime(self) -> bool:
        """Run recovery only for an existing database classified as plaintext."""

        with self._runtime_lock:
            if self._runtime_prepared:
                return True
            if self._unsupported_database_detected:
                return False
            prepared = self.database.prepare_existing_plaintext_runtime()
            self._runtime_prepared = prepared
            self._unsupported_database_detected = (
                self.database.exists and not prepared
            )
            return prepared

    def require_runtime_compatible(self) -> None:
        """Block every class-teacher surface for an unsupported database."""

        with self._runtime_lock:
            if self._unsupported_database_detected:
                self._raise_unsupported_database_format()
            if self._runtime_prepared or not self.database.exists:
                return
            prepared = self.database.prepare_existing_plaintext_runtime()
            if not prepared:
                self._unsupported_database_detected = True
                self._raise_unsupported_database_format()
            self._runtime_prepared = True

    @staticmethod
    def _raise_unsupported_database_format() -> None:
        raise unsupported_database_format_error()

    def ensure_plaintext_ready(self) -> bytes:
        """Gate unsupported data, recover known plaintext, and create on first write."""

        self.require_runtime_compatible()
        with self._runtime_lock:
            if not self._runtime_prepared:
                self.database.initialize_schema()
                self._runtime_prepared = True
        self._maybe_cleanup_expired_drafts(self._PLAINTEXT_KEY)
        return self._PLAINTEXT_KEY

    def _key_provider(self, _token: str = "") -> bytes:
        return self.ensure_plaintext_ready()

    def _maybe_cleanup_expired_drafts(self, key: bytes) -> None:
        now = time.monotonic()
        if now - self._last_cleanup < self._CLEANUP_INTERVAL_SECONDS:
            return
        if not self._maintenance_lock.acquire(blocking=False):
            return
        try:
            now = time.monotonic()
            if now - self._last_cleanup < self._CLEANUP_INTERVAL_SECONDS:
                return
            self._cleanup_expired_drafts(key)
            self._last_cleanup = now
        finally:
            self._maintenance_lock.release()

    def _cleanup_expired_drafts(self, vmk: bytes) -> None:
        attention_cutoff = _iso(_now() - timedelta(days=30))
        ai_cutoff = _iso(_now() - timedelta(days=7))
        with closing(self.database.connect()) as connection:
            with connection:
                expired_communication_ids: list[str] = []
                object_ids: list[str] = []
                communication_rows = connection.execute(
                    """
                    SELECT cd.draft_id, cd.payload_object_id,
                           cd.created_at,
                           a.payload_object_id AS action_object_id
                    FROM communication_drafts cd
                    JOIN actions a ON a.action_id = cd.action_id
                    """
                ).fetchall()
                for row in communication_rows:
                    action, _ = self.repository.get(
                        connection,
                        vmk=vmk,
                        object_id=str(row["action_object_id"]),
                    )
                    planned = str(action.get("due_at") or row["created_at"])
                    try:
                        expires = datetime.fromisoformat(planned) + timedelta(days=30)
                    except ValueError:
                        expires = _now() + timedelta(days=1)
                    if expires.astimezone(UTC) < _now():
                        expired_communication_ids.append(str(row["draft_id"]))
                        object_ids.append(str(row["payload_object_id"]))
                object_ids.extend(
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT payload_object_id FROM attention_cards
                        WHERE state = 'draft' AND created_at < ?
                        """,
                        (attention_cutoff,),
                    ).fetchall()
                )
                ai_ids = [
                    str(row[0])
                    for row in connection.execute(
                        """
                        SELECT record_id FROM support_records
                        WHERE record_kind = 'ai_draft' AND created_at < ?
                        """,
                        (ai_cutoff,),
                    ).fetchall()
                ]
                if ai_ids:
                    ai_placeholders = ",".join("?" for _ in ai_ids)
                    object_ids.extend(
                        str(row[0])
                        for row in connection.execute(
                            f"""
                            SELECT payload_object_id FROM record_revisions
                            WHERE record_id IN ({ai_placeholders})
                            """,
                            ai_ids,
                        ).fetchall()
                    )
                if expired_communication_ids:
                    placeholders = ",".join(
                        "?" for _ in expired_communication_ids
                    )
                    connection.execute(
                        f"""
                        DELETE FROM communication_drafts
                        WHERE draft_id IN ({placeholders})
                        """,
                        expired_communication_ids,
                    )
                connection.execute(
                    """
                    DELETE FROM attention_cards
                    WHERE state = 'draft' AND created_at < ?
                    """,
                    (attention_cutoff,),
                )
                if ai_ids:
                    connection.execute(
                        f"""
                        DELETE FROM support_records
                        WHERE record_id IN ({ai_placeholders})
                        """,
                        ai_ids,
                    )
                connection.executemany(
                    "DELETE FROM encrypted_objects WHERE object_id = ?",
                    [(value,) for value in object_ids],
                )


__all__ = ["VaultService"]
