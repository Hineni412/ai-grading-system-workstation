from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from backend.config_workspace.secure_fs import (
    SecureFilesystemError,
    SecureRootFilesystem,
)
from question_bank.solution_evidence.contracts import FineTermResolver
from question_bank.training_criteria.in_memory import (
    DeferredCombinedAnalysisBundle,
)


_ARTIFACT_ID = re.compile(r"^[0-9a-f]{32}$")
_SOURCE_ID = re.compile(r"^[0-9a-f]{32}$")
_REVISION = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_HASH = re.compile(r"^[0-9a-f]{64}$")
MAX_DEFERRED_ANALYSIS_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DeferredAnalysisArtifact:
    artifact_id: str
    session_id: int
    source_id: str
    source_revision: str
    curriculum_volume_id: str
    bundle: DeferredCombinedAnalysisBundle
    content_hash: str


class DeferredAnalysisArtifactStore:
    """Controlled hand-off between config analysis and question-bank import."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.filesystem = SecureRootFilesystem(self.root)

    @staticmethod
    def new_artifact_id() -> str:
        return uuid.uuid4().hex

    def save(
        self,
        *,
        artifact_id: str,
        session_id: int,
        source_id: str,
        source_revision: str,
        curriculum_volume_id: str,
        bundle: DeferredCombinedAnalysisBundle,
    ) -> DeferredAnalysisArtifact:
        identity = _normalized_identity(
            artifact_id=artifact_id,
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
            curriculum_volume_id=curriculum_volume_id,
        )
        if bundle.curriculum_volume_id != identity[4]:
            raise ValueError("deferred analysis curriculum volume changed")
        body: dict[str, Any] = {
            "schema_version": "deferred-analysis-artifact-v1",
            "artifact_id": identity[0],
            "session_id": identity[1],
            "source_id": identity[2],
            "source_revision": identity[3],
            "curriculum_volume_id": identity[4],
            "bundle": bundle.to_checkpoint_dict(),
        }
        content_hash = _hash_payload(body)
        payload = {**body, "content_hash": content_hash}
        if len(_json_bytes(payload)) > MAX_DEFERRED_ANALYSIS_BYTES:
            raise ValueError("deferred analysis artifact exceeds size limit")
        self.filesystem.write_json_atomic(self._path(identity[0]), payload)
        return DeferredAnalysisArtifact(
            artifact_id=identity[0],
            session_id=identity[1],
            source_id=identity[2],
            source_revision=identity[3],
            curriculum_volume_id=identity[4],
            bundle=bundle,
            content_hash=content_hash,
        )

    def load(
        self,
        artifact_id: str,
        *,
        session_id: int,
        source_id: str,
        source_revision: str,
        curriculum_volume_id: str,
        expected_content_hash: str | None = None,
        resolver: FineTermResolver | None = None,
    ) -> DeferredAnalysisArtifact:
        identity = _normalized_identity(
            artifact_id=artifact_id,
            session_id=session_id,
            source_id=source_id,
            source_revision=source_revision,
            curriculum_volume_id=curriculum_volume_id,
        )
        raw = self.filesystem.read_text(
            self._path(identity[0]),
            encoding="utf-8",
            max_bytes=MAX_DEFERRED_ANALYSIS_BYTES,
        )
        payload = json.loads(raw)
        if not isinstance(payload, Mapping):
            raise ValueError("deferred analysis artifact must be an object")
        expected_keys = {
            "schema_version",
            "artifact_id",
            "session_id",
            "source_id",
            "source_revision",
            "curriculum_volume_id",
            "bundle",
            "content_hash",
        }
        if set(payload) != expected_keys:
            raise ValueError("deferred analysis artifact fields are invalid")
        if payload.get("schema_version") != "deferred-analysis-artifact-v1":
            raise ValueError("deferred analysis artifact version is invalid")
        actual_identity = _normalized_identity(
            artifact_id=str(payload.get("artifact_id") or ""),
            session_id=int(payload.get("session_id")),
            source_id=str(payload.get("source_id") or ""),
            source_revision=str(payload.get("source_revision") or ""),
            curriculum_volume_id=str(payload.get("curriculum_volume_id") or ""),
        )
        if actual_identity != identity:
            raise ValueError("deferred analysis artifact identity changed")
        submitted_hash = str(payload.get("content_hash") or "").strip().lower()
        if not _CONTENT_HASH.fullmatch(submitted_hash):
            raise ValueError("deferred analysis artifact hash is invalid")
        body = {key: value for key, value in payload.items() if key != "content_hash"}
        if _hash_payload(body) != submitted_hash:
            raise ValueError("deferred analysis artifact content hash does not match")
        if expected_content_hash is not None:
            expected_hash = str(expected_content_hash or "").strip().lower()
            if not _CONTENT_HASH.fullmatch(expected_hash) or expected_hash != submitted_hash:
                raise ValueError("deferred analysis artifact is not the requested revision")
        raw_bundle = payload.get("bundle")
        if not isinstance(raw_bundle, Mapping):
            raise ValueError("deferred analysis bundle is invalid")
        bundle = DeferredCombinedAnalysisBundle.from_checkpoint_dict(
            raw_bundle,
            resolver=resolver,
        )
        if bundle.curriculum_volume_id != identity[4]:
            raise ValueError("deferred analysis bundle identity changed")
        return DeferredAnalysisArtifact(
            artifact_id=identity[0],
            session_id=identity[1],
            source_id=identity[2],
            source_revision=identity[3],
            curriculum_volume_id=identity[4],
            bundle=bundle,
            content_hash=submitted_hash,
        )

    def discard(self, artifact_id: str) -> None:
        clean_id = _artifact_id(artifact_id)
        try:
            self.filesystem.unlink_many((self._path(clean_id),))
        except SecureFilesystemError:
            pass

    def exists(self, artifact_id: str) -> bool:
        """Check only the exact controlled artifact path.

        The subsequent load remains handle-anchored and hash-verified; this
        helper is only used to choose between a first run and a resume.
        """

        return self._path(_artifact_id(artifact_id)).is_file()

    def _path(self, artifact_id: str) -> Path:
        return self.root / f"deferred_question_analysis_{_artifact_id(artifact_id)}.json"


def _normalized_identity(
    *,
    artifact_id: str,
    session_id: int,
    source_id: str,
    source_revision: str,
    curriculum_volume_id: str,
) -> tuple[str, int, str, str, str]:
    clean_artifact_id = _artifact_id(artifact_id)
    if isinstance(session_id, bool) or int(session_id) <= 0:
        raise ValueError("deferred analysis session id is invalid")
    clean_source_id = str(source_id or "").strip().lower()
    clean_revision = str(source_revision or "").strip().lower()
    clean_volume_id = str(curriculum_volume_id or "").strip()
    if not _SOURCE_ID.fullmatch(clean_source_id):
        raise ValueError("deferred analysis source id is invalid")
    if not _REVISION.fullmatch(clean_revision):
        raise ValueError("deferred analysis source revision is invalid")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", clean_volume_id):
        raise ValueError("deferred analysis curriculum volume is invalid")
    return (
        clean_artifact_id,
        int(session_id),
        clean_source_id,
        clean_revision,
        clean_volume_id,
    )


def _artifact_id(value: object) -> str:
    clean = str(value or "").strip().lower()
    if not _ARTIFACT_ID.fullmatch(clean):
        raise ValueError("deferred analysis artifact id is invalid")
    return clean


def _hash_payload(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


__all__ = [
    "DeferredAnalysisArtifact",
    "DeferredAnalysisArtifactStore",
    "MAX_DEFERRED_ANALYSIS_BYTES",
]
