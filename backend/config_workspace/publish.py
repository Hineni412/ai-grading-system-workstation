from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from session_manager import validate_generated_config


@dataclass(frozen=True, slots=True)
class PublishedConfig:
    rubric_path: Path
    answer_key_path: Path
    created_paths: tuple[Path, ...]


def publish_generated_config(
    upload_config_dir: Path,
    payload: dict[str, Any],
    *,
    job_id: int,
) -> PublishedConfig:
    clean_job_id = int(job_id)
    if clean_job_id <= 0:
        raise ValueError("job_id must be positive")
    validate_generated_config(payload)
    filesystem = SecureRootFilesystem(Path(upload_config_dir))
    rubric_path = filesystem.root / f"rubric_job-{clean_job_id}.json"
    answer_key_path = filesystem.root / f"answer_key_job-{clean_job_id}.json"
    if rubric_path.exists() or answer_key_path.exists():
        raise FileExistsError("config generation output already exists")

    created: list[Path] = []
    try:
        filesystem.atomic_write_bytes(
            rubric_path,
            _json_bytes(payload["rubric"]),
        )
        created.append(rubric_path)
        filesystem.atomic_write_bytes(
            answer_key_path,
            _json_bytes(payload["answer_key"]),
        )
        created.append(answer_key_path)
    except BaseException:
        try:
            filesystem.unlink_many(reversed(created))
        except SecureFilesystemError:
            pass
        raise
    return PublishedConfig(
        rubric_path=rubric_path,
        answer_key_path=answer_key_path,
        created_paths=tuple(created),
    )


def remove_published_config(
    upload_config_dir: Path,
    paths: tuple[Path, ...],
) -> None:
    if not paths:
        return
    try:
        SecureRootFilesystem(Path(upload_config_dir)).unlink_many(paths)
    except SecureFilesystemError:
        pass


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ).encode("utf-8")


__all__ = [
    "PublishedConfig",
    "publish_generated_config",
    "remove_published_config",
]
