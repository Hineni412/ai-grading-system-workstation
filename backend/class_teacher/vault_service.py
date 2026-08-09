from __future__ import annotations

import threading
import time
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.workspaces.contracts import WorkspaceContext

from .encrypted_database import EncryptedDatabase
from .errors import VaultError
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
        self._legacy_database_detected = False
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
        from .home_intake import HomeIntake

        self.home_intake = HomeIntake(
            self.ordinary_database,
            self.work,
            self.model_approval,
        )
        from .home_intake_drafts import HomeIntakeDrafts

        self.home_intake_drafts = HomeIntakeDrafts(
            self.database,
            self.repository,
            self._key_provider,
            self.ordinary_database,
        )
        from .sensitive_work_projection import SensitiveWorkProjection

        self.projections = SensitiveWorkProjection(
            self.database,
            self.repository,
            self._key_provider,
            self.work,
        )
        from .action_ledger_service import ActionLedgerService
        from .planning_service import PlanningService
        from .sop_workflow_service import SopWorkflowService
        from .collection_service import CollectionService
        from .sop_baseline_service import SopBaselineService
        from .support_record_service import SupportRecordService
        from .student_card_service import StudentCardService
        from .quick_inbox_service import QuickInboxService
        from .assessment_evidence_service import AssessmentEvidenceService
        from .attention_service import AttentionService
        from .local_speech import LocalSpeechTranscriber

        self.actions = ActionLedgerService(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.planning = PlanningService(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.sop = SopWorkflowService(
            self.database,
            self.repository,
            self._key_provider,
        )
        self.collections = CollectionService(
            self.database,
            self.repository,
            self._key_provider,
            self.planning,
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

        self.class_roster = ClassRosterService(
            self.database,
            self.repository,
            self._key_provider,
            self.support,
            SqliteExistingStudentRosterSource(
                Path(getattr(context.paths, "db_path", context.root / "grading.db"))
            ),
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
        self.quick_inbox = QuickInboxService(
            self.database,
            self.repository,
            self._key_provider,
            self.support,
            self.actions,
            self.sop,
        )
        self.evidence = AssessmentEvidenceService(
            self.database,
            self.repository,
            self._key_provider,
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
        self._initialize_business_facades()
        from .intake import ClassTeacherIntake

        self.intake = ClassTeacherIntake(
            ordinary_database=self.ordinary_database,
            class_roster=self.class_roster,
            domain_database=self.database,
            repository=self.repository,
            key_provider=self._key_provider,
            support=self.support,
            planning=self.planning,
            work=self.work,
            sop=self.sop,
            sop_baselines=self.sop_baselines,
            student_cards=self.student_cards,
            model_gateway=self.workspace_model_gateway,
            ai_tasks=self.workspace_ai_task_port,
        )

    def _initialize_business_facades(self) -> None:
        from .affair_workspace import AffairWorkspace
        from .home_intake_finalizer import HomeIntakeFinalizer
        from .student_directory import StudentDirectory
        from .support_ai_review import SupportRecordAIReview

        self.support_ai_reviews = SupportRecordAIReview(
            self.database,
            self.repository,
            self._key_provider,
            self.support,
            self.model_approval,
            self.projections,
        )
        self.student_directory = StudentDirectory(
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
        self.home_intake_finalizer = HomeIntakeFinalizer(
            self.home_intake,
            self.sop_baselines,
            self.affairs,
            self.projections,
            self.home_intake_drafts,
        )

    def prepare_existing_plaintext_runtime(self) -> bool:
        """Run recovery only for an existing database classified as plaintext."""

        with self._runtime_lock:
            if self._runtime_prepared:
                return True
            if self._legacy_database_detected:
                return False
            prepared = self.database.prepare_existing_plaintext_runtime()
            self._runtime_prepared = prepared
            self._legacy_database_detected = self.database.exists and not prepared
            return prepared

    def require_runtime_compatible(self) -> None:
        """Block every class-teacher surface while a legacy database is present."""

        with self._runtime_lock:
            if self._legacy_database_detected:
                self._raise_legacy_database_required()
            if self._runtime_prepared or not self.database.exists:
                return
            prepared = self.database.prepare_existing_plaintext_runtime()
            if not prepared:
                self._legacy_database_detected = True
                self._raise_legacy_database_required()
            self._runtime_prepared = True

    @staticmethod
    def _raise_legacy_database_required() -> None:
        raise VaultError(
            "vault_plaintext_migration_required",
            "检测到旧加密班主任数据库，已停止读取和写入；请先执行授权迁移",
            status_code=409,
        )

    def ensure_plaintext_ready(self) -> bytes:
        """Gate legacy data, recover known plaintext, and create on first write."""

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
