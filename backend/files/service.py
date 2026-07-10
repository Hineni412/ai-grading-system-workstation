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


JOB_FILE_RULES = {
    "report_export": JobFileRule(
        result_field="file_path",
        allowed_suffixes=frozenset({".xlsx"}),
    ),
}


class JobFileService:
    def __init__(self, reports_dir: Path) -> None:
        self.reports_dir = Path(reports_dir)

    def resolve(self, job: JobRecord) -> ResolvedFile:
        if job.status != "succeeded":
            raise JobFileUnavailable("Job file is not available.")
        rule = JOB_FILE_RULES.get(job.job_type)
        if rule is None:
            raise JobFileNotFound("Job does not publish a downloadable file.")
        path_value = job.result.get(rule.result_field)
        if path_value is None or not str(path_value).strip():
            raise ControlledFileExpired("Stored file is no longer available.")
        return resolve_controlled_file(
            path_value,
            root=self.reports_dir,
            data_root=self.reports_dir.parent,
            allowed_suffixes=rule.allowed_suffixes,
        )
