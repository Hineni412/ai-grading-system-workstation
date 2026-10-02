from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.file_access import (
    ControlledFileExpired,
    ResolvedFile,
    resolve_controlled_file,
)
from backend.jobs.store import JobRecord, JobStore


class JobFileUnavailable(RuntimeError):
    """Raised when a job has not completed successfully."""


class JobFileNotFound(LookupError):
    """Raised when a job type does not publish a downloadable file."""


@dataclass(frozen=True, slots=True)
class JobFileRule:
    result_field: str
    allowed_suffixes: frozenset[str]
    root_name: str
    data_root_depth: int
    consume_after_download: bool = False


# 个人学情报告按场次留存在本机：下载后不删除，用户可在报表记录里手动删除。
# 其余 report_export 类型（成绩 Excel、批注原卷 PDF）维持下载后即删。
RETAINED_REPORT_TYPES = frozenset({"personal_analysis_html"})


JOB_FILE_RULES = {
    "wrong_question_export": JobFileRule(
        result_field="file_path", allowed_suffixes=frozenset({".docx", ".zip"}),
        root_name="reports_dir", data_root_depth=1, consume_after_download=True,
    ),
    "personalized_handout_export": JobFileRule(
        result_field="file_path", allowed_suffixes=frozenset({".docx", ".zip"}),
        root_name="reports_dir", data_root_depth=1, consume_after_download=True,
    ),
    "report_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".pdf", ".xlsx", ".html", ".zip"}),
        root_name="reports_dir",
        data_root_depth=1,
        consume_after_download=True,
    ),
    "assembly_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".docx", ".md", ".pdf"}),
        root_name="assembly_outputs_dir",
        data_root_depth=2,
    ),
    "ops_backup": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".zip"}),
        root_name="backups_dir",
        data_root_depth=1,
    ),
    "ops_transfer_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".zip"}),
        root_name="ops_outputs_dir",
        data_root_depth=2,
    ),
}


class JobFileService:
    def __init__(
        self,
        reports_dir: Path,
        *,
        assembly_outputs_dir: Path | None = None,
        backups_dir: Path | None = None,
        ops_outputs_dir: Path | None = None,
    ) -> None:
        self.reports_dir = Path(reports_dir)
        self.assembly_outputs_dir = (
            Path(assembly_outputs_dir) if assembly_outputs_dir is not None else None
        )
        self.backups_dir = Path(backups_dir) if backups_dir is not None else None
        self.ops_outputs_dir = (
            Path(ops_outputs_dir) if ops_outputs_dir is not None else None
        )

    def resolve(self, job: JobRecord) -> ResolvedFile:
        if job.status != "succeeded":
            raise JobFileUnavailable("Job file is not available.")
        rule = JOB_FILE_RULES.get(job.job_type)
        if rule is None:
            raise JobFileNotFound("Job does not publish a downloadable file.")
        path_value = job.result.get(rule.result_field)
        if path_value is None or not str(path_value).strip():
            raise ControlledFileExpired("Stored file is no longer available.")
        root = getattr(self, rule.root_name, None)
        if root is None:
            raise JobFileNotFound("Job does not publish a downloadable file.")
        data_root = root.parents[rule.data_root_depth - 1]
        return resolve_controlled_file(
            path_value,
            root=root,
            data_root=data_root,
            allowed_suffixes=rule.allowed_suffixes,
        )

    def should_consume(self, job: JobRecord) -> bool:
        rule = JOB_FILE_RULES.get(job.job_type)
        if not (rule and rule.consume_after_download):
            return False
        if (
            job.job_type == "report_export"
            and str(job.payload.get("report_type") or "") in RETAINED_REPORT_TYPES
        ):
            return False
        return True

    def is_retained_report(self, job: JobRecord) -> bool:
        return (
            job.job_type == "report_export"
            and str(job.payload.get("report_type") or "") in RETAINED_REPORT_TYPES
        )

    def delete_retained_file(self, job: JobRecord, store: JobStore) -> int | None:
        """删除一份留存的报告文件并清掉 job 的下载引用，返回释放字节数。

        已清空/已过期（文件引用不在）时幂等返回 None。顺序固定为先删文件再清
        记录：清记录失败时 resolve 会因文件缺失按过期处理，不留孤儿文件。
        """
        if not self.is_retained_report(job):
            raise JobFileNotFound("Job does not publish a retained report file.")
        try:
            resolved = self.resolve(job)
        except ControlledFileExpired:
            return None
        try:
            freed = resolved.path.stat().st_size
        except OSError:
            freed = 0
        self._unlink_quietly(resolved.path)
        store.clear_downloadable_file(job.id)
        return freed

    def consume_after_send(
        self,
        job: JobRecord,
        resolved: ResolvedFile,
        store: JobStore,
    ) -> None:
        if not self.should_consume(job):
            return
        self._unlink_quietly(resolved.path)
        store.clear_downloadable_file(job.id)

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
            return
        except OSError:
            pass
        try:
            graveyard = path.with_name(path.name + ".downloaded")
            path.replace(graveyard)
            graveyard.unlink(missing_ok=True)
        except OSError:
            return
