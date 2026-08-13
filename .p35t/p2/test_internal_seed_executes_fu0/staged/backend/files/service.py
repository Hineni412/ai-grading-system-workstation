from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from backend.file_access import ControlledFileExpired, ResolvedFile, resolve_controlled_file
from backend.jobs.store import JobRecord


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


JOB_FILE_RULES = {
    "report_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".pdf", ".xlsx"}),
        root_name="reports_dir",
        data_root_depth=1,
    ),
    "training_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".docx", ".md", ".zip"}),
        root_name="training_outputs_dir",
        data_root_depth=2,
    ),
    "assembly_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".docx", ".md"}),
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
        training_outputs_dir: Path | None = None,
        assembly_outputs_dir: Path | None = None,
        backups_dir: Path | None = None,
        ops_outputs_dir: Path | None = None,
    ) -> None:
        self.reports_dir = Path(reports_dir)
        self.training_outputs_dir = (
            Path(training_outputs_dir) if training_outputs_dir is not None else None
        )
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
