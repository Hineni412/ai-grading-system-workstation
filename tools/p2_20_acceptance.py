from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path[:1]:
    sys.path.insert(0, str(REPO_ROOT))

from tools import p1_29_acceptance as workspace_tools
from question_id_contract import QuestionIdCatalog, QuestionIdContractError


PACKAGE = "P2-20"
METADATA_FILENAME = "p2-20-acceptance.json"
SERVER_STATE_FILENAME = "server-state.json"
SERVER_CLAIM_FILENAME = "server-start.claim"
SERVER_STOP_FILENAME = "server-stop.request"
_CONFIG_KEY_RE = re.compile(
    r"^[ \t]*(?P<key>DATA_DIR|LOGS_DIR|[\"']DATA_DIR[\"']|[\"']LOGS_DIR[\"'])[ \t]*:"
)


class AcceptanceError(RuntimeError):
    """Raised when the P2-20 acceptance workspace is unsafe."""


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def validate_workspace(
    workspace: Path | str,
    *,
    require_empty: bool = True,
) -> Path:
    candidate = Path(workspace).expanduser().resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()
    if candidate == temp_root or not _is_relative_to(candidate, temp_root):
        raise AcceptanceError(
            "workspace must be strictly below the system temporary directory"
        )
    if any(part.casefold() == "user_data" for part in candidate.parts):
        raise AcceptanceError("workspace must not contain a user_data path segment")
    if candidate.exists() and not candidate.is_dir():
        raise AcceptanceError("workspace must be an empty directory")
    if require_empty and candidate.exists() and next(candidate.iterdir(), None) is not None:
        raise AcceptanceError("workspace must be empty")
    return candidate


def load_metadata(workspace: Path | str) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata_path = target / METADATA_FILENAME
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("prepared workspace metadata is unavailable") from exc
    common_keys = {
        "package",
        "source_sha",
        "state",
        "authorized_session_id",
    }
    if not isinstance(metadata, dict) or not common_keys.issubset(metadata):
        raise AcceptanceError("prepared workspace metadata is invalid")
    state = metadata["state"]
    if metadata["package"] != PACKAGE or state not in {
        "code_prepared",
        "inputs_prepared",
        "runtime_ready",
    }:
        raise AcceptanceError("prepared workspace metadata is invalid")
    if state == "code_prepared":
        if set(metadata) != common_keys or metadata["authorized_session_id"] is not None:
            raise AcceptanceError("prepared workspace metadata is invalid")
    else:
        input_keys = common_keys | {
            "paper_count",
            "source_fingerprint",
            "input_manifest",
        }
        if state == "inputs_prepared":
            expected_keys = input_keys
        else:
            expected_keys = input_keys | {
                "profile_fingerprint",
                "model_request_budget",
            }
        if set(metadata) != expected_keys:
            raise AcceptanceError("prepared workspace metadata is invalid")
        if (
            not isinstance(metadata["authorized_session_id"], int)
            or not isinstance(metadata["paper_count"], int)
            or not isinstance(metadata["source_fingerprint"], str)
            or len(metadata["source_fingerprint"]) != 64
            or metadata["input_manifest"] != "acceptance_inputs/manifest.json"
        ):
            raise AcceptanceError("prepared workspace metadata is invalid")
        if state == "runtime_ready" and (
            not isinstance(metadata["profile_fingerprint"], str)
            or len(metadata["profile_fingerprint"]) != 64
            or not isinstance(metadata["model_request_budget"], int)
        ):
            raise AcceptanceError("prepared workspace metadata is invalid")
    source_sha = metadata["source_sha"]
    if not isinstance(source_sha, str) or len(source_sha) != 40:
        raise AcceptanceError("prepared workspace metadata is invalid")
    return metadata


def prepare_code_workspace(
    source_ref: str,
    workspace: Path | str,
    *,
    repo_root: Path,
) -> dict[str, object]:
    target = validate_workspace(workspace)
    try:
        source_sha = workspace_tools.resolve_source_sha(
            source_ref,
            repo_root=Path(repo_root).resolve(),
        )
    except workspace_tools.AcceptanceError as exc:
        raise AcceptanceError(str(exc)) from exc
    target.mkdir(parents=True, exist_ok=False)
    try:
        workspace_tools._archive_commit(
            source_sha,
            repo_root=Path(repo_root).resolve(),
            workspace=target,
        )
    except workspace_tools.AcceptanceError as exc:
        raise AcceptanceError(str(exc)) from exc
    metadata: dict[str, object] = {
        "package": PACKAGE,
        "source_sha": source_sha,
        "state": "code_prepared",
        "authorized_session_id": None,
    }
    temporary_path = target / f"{METADATA_FILENAME}.tmp"
    temporary_path.write_text(
        json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(target / METADATA_FILENAME)
    return load_metadata(target)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _database_generation(database: Path) -> dict[str, str | None]:
    return {
        suffix or "main": (
            _sha256_file(Path(f"{database}{suffix}"))
            if Path(f"{database}{suffix}").is_file()
            else None
        )
        for suffix in ("", "-wal", "-shm")
    }


def _resolve_source_file(raw_path: object, source_data_root: Path) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AcceptanceError("authorized exam contains a missing source file")
    candidate = Path(raw_path)
    candidates = [candidate]
    if not candidate.is_absolute():
        candidates.append(source_data_root / candidate)
    parts = list(candidate.parts)
    user_data_indexes = [
        index for index, part in enumerate(parts) if part.casefold() == "user_data"
    ]
    if user_data_indexes:
        relative_parts = parts[user_data_indexes[-1] + 1 :]
        candidates.append(source_data_root.joinpath(*relative_parts))
    resolved_root = source_data_root.resolve()
    for possible in candidates:
        try:
            resolved = possible.resolve(strict=True)
        except OSError:
            continue
        if (
            resolved.is_file()
            and _is_relative_to(resolved, resolved_root)
            and not resolved.is_symlink()
        ):
            return resolved
    raise AcceptanceError("authorized exam source file is missing or outside the data root")


def _combined_fingerprint(
    database_generation: dict[str, str | None],
    file_hashes: dict[str, str],
) -> str:
    canonical = json.dumps(
        {"database": database_generation, "files": file_hashes},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _write_metadata(workspace: Path, metadata: dict[str, object]) -> None:
    temporary_path = workspace / f"{METADATA_FILENAME}.tmp"
    temporary_path.write_text(
        json.dumps(metadata, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(workspace / METADATA_FILENAME)


def prepare_authorized_inputs(
    workspace: Path | str,
    *,
    source_data_root: Path | str,
    session_id: int,
    expected_session_name: str,
    paper_limit: int,
) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "code_prepared":
        raise AcceptanceError("authorized inputs have already been prepared")
    if (
        isinstance(session_id, bool)
        or not isinstance(session_id, int)
        or session_id < 1
        or not isinstance(expected_session_name, str)
        or not expected_session_name.strip()
    ):
        raise AcceptanceError("authorized exam identity is invalid")
    if (
        isinstance(paper_limit, bool)
        or not isinstance(paper_limit, int)
        or paper_limit < 1
        or paper_limit > 3
    ):
        raise AcceptanceError("paper limit must be between 1 and 3")

    data_root = Path(source_data_root).expanduser().resolve()
    if not data_root.is_dir() or _is_relative_to(data_root, target):
        raise AcceptanceError("source data root is missing or unsafe")
    database = data_root / "databases" / "grading_system.db"
    if not database.is_file():
        raise AcceptanceError("source grading database is missing")

    database_before = _database_generation(database)
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        session = connection.execute(
            """
            SELECT id, session_name, source_paper_path
            FROM grading_sessions
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (session_id,),
        ).fetchone()
        if session is None or session["session_name"] != expected_session_name:
            raise AcceptanceError("authorized exam does not match the requested session")
        confirmed_template = connection.execute(
            """
            SELECT 1
            FROM session_templates
            WHERE session_id = ? AND is_confirmed = 1
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        if confirmed_template is None:
            raise AcceptanceError("authorized exam has no confirmed template")
        papers = connection.execute(
            """
            SELECT DISTINCT
                p.id AS paper_id,
                p.front_image,
                p.back_image,
                s.student_code,
                s.name,
                s.class_name
            FROM exam_papers AS p
            JOIN students AS s ON s.id = p.student_id
            JOIN session_results AS r
              ON r.session_id = p.session_id
             AND r.paper_id = p.id
             AND r.student_id = s.id
            WHERE p.session_id = ?
              AND p.match_status = 'matched'
              AND p.processing_status = 'graded'
              AND TRIM(COALESCE(s.student_code, '')) <> ''
              AND TRIM(COALESCE(s.name, '')) <> ''
            ORDER BY p.id
            LIMIT ?
            """,
            (session_id, paper_limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise AcceptanceError("authorized exam database could not be read safely") from exc
    finally:
        connection.close()
    if len(papers) != paper_limit:
        raise AcceptanceError("authorized exam has too few eligible answer sheets")

    source_paper = _resolve_source_file(session["source_paper_path"], data_root)
    template_candidates = sorted(
        (data_root / "templates" / f"session_{session_id}").glob("*.pdf")
    )
    if len(template_candidates) != 1:
        raise AcceptanceError("authorized exam must have exactly one template PDF")
    template_pdf = _resolve_source_file(str(template_candidates[0]), data_root)

    selected_files: list[tuple[str, Path]] = [
        (f"source-paper{source_paper.suffix.lower()}", source_paper),
        ("template.pdf", template_pdf),
    ]
    for index, paper in enumerate(papers, start=1):
        front = _resolve_source_file(paper["front_image"], data_root)
        back = _resolve_source_file(paper["back_image"], data_root)
        selected_files.extend(
            [
                (f"answer-sheets/answer-{index:02}-front{front.suffix.lower()}", front),
                (f"answer-sheets/answer-{index:02}-back{back.suffix.lower()}", back),
            ]
        )
    hashes_before = {
        logical_name: _sha256_file(source_path)
        for logical_name, source_path in selected_files
    }

    staging = target / "acceptance_inputs-preparing"
    published = target / "acceptance_inputs"
    backup = target / "authorized-inputs-backup.zip"
    if staging.exists() or published.exists() or backup.exists():
        raise AcceptanceError("acceptance input destination is not empty")
    staging.mkdir()
    for logical_name, source_path in selected_files:
        destination = staging.joinpath(*Path(logical_name).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, destination)
    roster_path = staging / "roster.csv"
    with roster_path.open("w", encoding="utf-8-sig", newline="") as roster_file:
        writer = csv.writer(roster_file, lineterminator="\n")
        writer.writerow(("student_code", "name", "class_name"))
        for paper in papers:
            writer.writerow(
                (
                    paper["student_code"],
                    paper["name"],
                    paper["class_name"] or "",
                )
            )
    manifest = {
        "package": PACKAGE,
        "authorized_session_id": session_id,
        "paper_count": paper_limit,
        "files": {
            logical_name: hashes_before[logical_name]
            for logical_name, _source_path in selected_files
        },
    }
    (staging / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )

    hashes_after = {
        logical_name: _sha256_file(source_path)
        for logical_name, source_path in selected_files
    }
    database_after = _database_generation(database)
    if hashes_after != hashes_before or database_after != database_before:
        raise AcceptanceError("authorized source changed while the copy was prepared")

    with zipfile.ZipFile(
        backup,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    staging.replace(published)
    prepared_metadata: dict[str, object] = {
        "package": PACKAGE,
        "source_sha": metadata["source_sha"],
        "state": "inputs_prepared",
        "authorized_session_id": session_id,
        "paper_count": paper_limit,
        "source_fingerprint": _combined_fingerprint(
            database_before,
            hashes_before,
        ),
        "input_manifest": "acceptance_inputs/manifest.json",
    }
    _write_metadata(target, prepared_metadata)
    return load_metadata(target)


def _current_authorized_source_fingerprint(
    source_data_root: Path | str,
    *,
    session_id: int,
    paper_limit: int,
) -> str:
    data_root = Path(source_data_root).expanduser().resolve()
    database = data_root / "databases" / "grading_system.db"
    if not data_root.is_dir() or not database.is_file():
        raise AcceptanceError("authorized source data is unavailable")

    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        session = connection.execute(
            """
            SELECT source_paper_path
            FROM grading_sessions
            WHERE id = ? AND COALESCE(is_deleted, 0) = 0
            """,
            (session_id,),
        ).fetchone()
        papers = connection.execute(
            """
            SELECT DISTINCT p.front_image, p.back_image
            FROM exam_papers AS p
            JOIN students AS s ON s.id = p.student_id
            JOIN session_results AS r
              ON r.session_id = p.session_id
             AND r.paper_id = p.id
             AND r.student_id = s.id
            WHERE p.session_id = ?
              AND p.match_status = 'matched'
              AND p.processing_status = 'graded'
              AND TRIM(COALESCE(s.student_code, '')) <> ''
              AND TRIM(COALESCE(s.name, '')) <> ''
            ORDER BY p.id
            LIMIT ?
            """,
            (session_id, paper_limit),
        ).fetchall()
    except sqlite3.Error as exc:
        raise AcceptanceError("authorized source could not be verified safely") from exc
    finally:
        connection.close()
    if session is None or len(papers) != paper_limit:
        raise AcceptanceError("authorized source no longer matches the prepared scope")

    source_paper = _resolve_source_file(session["source_paper_path"], data_root)
    template_candidates = sorted(
        (data_root / "templates" / f"session_{session_id}").glob("*.pdf")
    )
    if len(template_candidates) != 1:
        raise AcceptanceError("authorized source no longer matches the prepared scope")
    selected_files: list[tuple[str, Path]] = [
        (f"source-paper{source_paper.suffix.lower()}", source_paper),
        (
            "template.pdf",
            _resolve_source_file(str(template_candidates[0]), data_root),
        ),
    ]
    for index, paper in enumerate(papers, start=1):
        front = _resolve_source_file(paper["front_image"], data_root)
        back = _resolve_source_file(paper["back_image"], data_root)
        selected_files.extend(
            [
                (f"answer-sheets/answer-{index:02}-front{front.suffix.lower()}", front),
                (f"answer-sheets/answer-{index:02}-back{back.suffix.lower()}", back),
            ]
        )
    return _combined_fingerprint(
        _database_generation(database),
        {
            logical_name: _sha256_file(source_path)
            for logical_name, source_path in selected_files
        },
    )


def _acceptance_data_file(
    raw_path: object,
    *,
    data_root: Path,
    label: str,
    suffix: str | None = None,
) -> Path:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise AcceptanceError(f"{label} is missing")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = data_root / path
    resolved = path.resolve()
    if not _is_relative_to(resolved, data_root) or not resolved.is_file():
        raise AcceptanceError(f"{label} is outside the acceptance workspace or missing")
    if suffix is not None and resolved.suffix.casefold() != suffix.casefold():
        raise AcceptanceError(f"{label} has an unexpected file type")
    return resolved


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise AcceptanceError(f"{label} is not a JSON object")
    return value


def _config_summary(
    session: sqlite3.Row,
    *,
    data_root: Path,
) -> tuple[list[str], set[str], QuestionIdCatalog, str]:
    rubric_path = _acceptance_data_file(
        session["rubric_path"],
        data_root=data_root,
        label="rubric",
        suffix=".json",
    )
    answer_path = _acceptance_data_file(
        session["answer_key_path"],
        data_root=data_root,
        label="answer key",
        suffix=".json",
    )
    rubric = _load_json_object(rubric_path, label="rubric")
    answer = _load_json_object(answer_path, label="answer key")
    rubric_questions = rubric.get("questions")
    answer_questions = answer.get("questions")
    if not isinstance(rubric_questions, list) or not isinstance(
        answer_questions,
        list,
    ):
        raise AcceptanceError("configuration questions are missing")

    def question_ids(
        questions: list[object],
        *,
        label: str,
    ) -> list[str]:
        values: list[str] = []
        for question in questions:
            if not isinstance(question, dict):
                raise AcceptanceError(f"{label} contains an invalid question")
            question_id = str(question.get("question_id") or "").strip()
            if not question_id or question_id in values:
                raise AcceptanceError(f"{label} question identities are invalid")
            values.append(question_id)
        if not values:
            raise AcceptanceError(f"{label} questions are missing")
        return values

    rubric_ids = question_ids(rubric_questions, label="rubric")
    answer_ids = question_ids(answer_questions, label="answer key")
    if set(rubric_ids) != set(answer_ids):
        raise AcceptanceError("configuration question identities conflict")

    try:
        question_catalog = QuestionIdCatalog.from_document(rubric)
    except QuestionIdContractError as exc:
        raise AcceptanceError(
            "rubric question identities are invalid"
        ) from exc
    if (
        not question_catalog.parent_ids
        or set(question_catalog.parent_ids) != set(rubric_ids)
        or not question_catalog.detail_ids
    ):
        raise AcceptanceError("rubric question identities are invalid")
    scoring_ids = (
        set(question_catalog.parent_ids)
        | set(question_catalog.detail_ids)
        | set(question_catalog.aliases)
    )
    config_hash = _combined_fingerprint(
        None,
        {
            "rubric.json": _sha256_file(rubric_path),
            "answer.json": _sha256_file(answer_path),
        },
    )
    return rubric_ids, scoring_ids, question_catalog, config_hash


def _template_summary(
    database: Path,
    *,
    session_id: int,
    data_root: Path,
    question_catalog: QuestionIdCatalog,
) -> tuple[int, str]:
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        templates = connection.execute(
            """
            SELECT front_template_path, back_template_path, ai_analysis_path,
                   template_config_path, regions_path, is_confirmed,
                   regions_snapshot_pending, regions_snapshot_token
            FROM session_templates
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchall()
        regions = connection.execute(
            """
            SELECT mapped_question_id, is_confirmed, mapping_status,
                   multi_region_confirmed
            FROM answer_regions
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise AcceptanceError(
            "acceptance template could not be verified safely"
        ) from exc
    finally:
        connection.close()
    if len(templates) != 1 or not bool(templates[0]["is_confirmed"]):
        raise AcceptanceError("acceptance template is missing or unconfirmed")
    template = templates[0]
    if bool(template["regions_snapshot_pending"]) or template[
        "regions_snapshot_token"
    ]:
        raise AcceptanceError("region snapshot is incomplete")

    template_files: dict[str, str] = {}
    for field, label in (
        ("front_template_path", "front template"),
        ("back_template_path", "back template"),
        ("ai_analysis_path", "template analysis"),
        ("template_config_path", "template configuration"),
        ("regions_path", "region snapshot"),
    ):
        path = _acceptance_data_file(
            template[field],
            data_root=data_root,
            label=label,
        )
        template_files[label] = _sha256_file(path)

    if not regions:
        raise AcceptanceError("confirmed answer regions are missing")
    covered_detail_ids: set[str] = set()
    mapped_regions: dict[str, list[bool]] = {}
    for region in regions:
        mapped_question_id = str(region["mapped_question_id"] or "").strip()
        if (
            not mapped_question_id
            or not bool(region["is_confirmed"])
            or str(region["mapping_status"] or "") == "unbound"
            ):
            raise AcceptanceError("answer regions contain an unconfirmed mapping")
        if mapped_question_id == "__student_name__":
            resolved_question_id = mapped_question_id
            expanded = ()
        else:
            resolved_question_id = question_catalog.resolve(mapped_question_id)
            expanded = (
                question_catalog.expand(resolved_question_id)
                if resolved_question_id is not None
                else ()
            )
        if resolved_question_id is None or (
            resolved_question_id != "__student_name__" and not expanded
        ):
            raise AcceptanceError("answer regions conflict with configuration")
        existing_mappings = mapped_regions.setdefault(
            resolved_question_id,
            [],
        )
        existing_mappings.append(bool(region["multi_region_confirmed"]))
        for detail_id in expanded:
            if detail_id in covered_detail_ids and not existing_mappings[:-1]:
                raise AcceptanceError("answer regions conflict with configuration")
            covered_detail_ids.add(detail_id)
    if any(
        len(confirmations) > 1 and not all(confirmations)
        for confirmations in mapped_regions.values()
    ):
        raise AcceptanceError("duplicate answer regions are not confirmed")
    if covered_detail_ids != set(question_catalog.detail_ids):
        raise AcceptanceError("answer regions are missing configured questions")
    return len(regions), _combined_fingerprint(None, template_files)


def _grading_summary(
    database: Path,
    *,
    session_id: int,
    data_root: Path,
    expected_paper_count: int,
    scoring_ids: set[str],
) -> None:
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        papers = connection.execute(
            """
            SELECT p.id, p.student_id, p.front_image, p.back_image,
                   p.match_status, p.processing_status,
                   s.student_code, s.name
            FROM exam_papers AS p
            LEFT JOIN students AS s ON s.id = p.student_id
            WHERE p.session_id = ?
            ORDER BY p.id
            """,
            (session_id,),
        ).fetchall()
        results = connection.execute(
            """
            SELECT id, student_id, paper_id, total_score, student_score,
                   raw_json
            FROM session_results
            WHERE session_id = ?
            ORDER BY id
            """,
            (session_id,),
        ).fetchall()
        details = connection.execute(
            """
            SELECT d.result_id, d.question_id, d.score_awarded
            FROM session_details AS d
            JOIN session_results AS r ON r.id = d.result_id
            WHERE r.session_id = ?
            ORDER BY d.result_id, d.id
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise AcceptanceError(
            "acceptance grading could not be verified safely"
        ) from exc
    finally:
        connection.close()

    if len(papers) != expected_paper_count or len(results) != expected_paper_count:
        raise AcceptanceError("grading is incomplete or conflicted")
    paper_students: dict[int, int] = {}
    for paper in papers:
        student_id = paper["student_id"]
        if (
            student_id is None
            or str(paper["match_status"]) != "matched"
            or str(paper["processing_status"]) != "graded"
            or not str(paper["student_code"] or "").strip()
            or not str(paper["name"] or "").strip()
        ):
            raise AcceptanceError("grading is incomplete or conflicted")
        paper_id = int(paper["id"])
        paper_students[paper_id] = int(student_id)
        _acceptance_data_file(
            paper["front_image"],
            data_root=data_root,
            label="graded paper front image",
        )
        _acceptance_data_file(
            paper["back_image"],
            data_root=data_root,
            label="graded paper back image",
        )

    details_by_result: dict[int, list[sqlite3.Row]] = {}
    for detail in details:
        details_by_result.setdefault(int(detail["result_id"]), []).append(detail)
    seen_papers: set[int] = set()
    seen_students: set[int] = set()
    for result in results:
        result_id = int(result["id"])
        paper_id = int(result["paper_id"])
        student_id = int(result["student_id"])
        if (
            paper_id in seen_papers
            or student_id in seen_students
            or paper_students.get(paper_id) != student_id
        ):
            raise AcceptanceError("grading is incomplete or conflicted")
        seen_papers.add(paper_id)
        seen_students.add(student_id)
        result_details = details_by_result.get(result_id, [])
        if not result_details:
            raise AcceptanceError("grading is incomplete or conflicted")
        seen_question_ids: set[str] = set()
        awarded_total = 0.0
        for detail in result_details:
            question_id = str(detail["question_id"] or "").strip()
            try:
                score = float(detail["score_awarded"])
            except (TypeError, ValueError) as exc:
                raise AcceptanceError(
                    "grading is incomplete or conflicted"
                ) from exc
            if (
                not question_id
                or question_id not in scoring_ids
                or question_id in seen_question_ids
                or not math.isfinite(score)
                or score < 0
            ):
                raise AcceptanceError("grading is incomplete or conflicted")
            seen_question_ids.add(question_id)
            awarded_total += score
        try:
            student_score = float(result["student_score"])
            total_score = float(result["total_score"])
        except (TypeError, ValueError) as exc:
            raise AcceptanceError("grading is incomplete or conflicted") from exc
        if (
            not math.isfinite(student_score)
            or not math.isfinite(total_score)
            or total_score <= 0
            or student_score < 0
            or student_score > total_score + 1e-6
            or abs(awarded_total - student_score) > 1e-6
        ):
            raise AcceptanceError("grading is incomplete or conflicted")
        try:
            raw_json = json.loads(str(result["raw_json"]))
        except json.JSONDecodeError as exc:
            raise AcceptanceError("grading is incomplete or conflicted") from exc
        completeness = (
            raw_json.get("grading_completeness")
            if isinstance(raw_json, dict)
            else None
        )
        if (
            not isinstance(completeness, dict)
            or completeness.get("status") != "complete"
            or any(
                completeness.get(key)
                for key in (
                    "missing_question_ids",
                    "duplicate_question_ids",
                    "unexpected_question_ids",
                )
            )
        ):
            raise AcceptanceError("grading is incomplete or conflicted")


def _pending_teacher_review_count(
    database: Path,
    *,
    session_id: int,
) -> int:
    try:
        from backend.review.service import ReviewApplicationService
        from db_manager import DBManager

        db = DBManager(database)
        session = db.get_grading_session(session_id)
        if not session:
            raise AcceptanceError("acceptance session is missing or deleted")
        questions = ReviewApplicationService(db).list_questions(
            session_id,
            session,
        )
    except AcceptanceError:
        raise
    except Exception as exc:
        raise AcceptanceError(
            "teacher review could not be verified safely"
        ) from exc
    return sum(int(question.needs_review_count) for question in questions)


def _report_summary(
    database: Path,
    *,
    session_id: int,
    data_root: Path,
    report_file: Path | str | None,
) -> tuple[int, str]:
    report_path = _acceptance_data_file(
        str(report_file) if report_file is not None else None,
        data_root=data_root,
        label="exported report",
        suffix=".xlsx",
    )
    reports_root = (data_root / "reports").resolve()
    if not _is_relative_to(report_path, reports_root):
        raise AcceptanceError("exported report is outside the reports directory")

    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        expected_rows = connection.execute(
            """
            SELECT s.student_code, r.student_score
            FROM session_results AS r
            JOIN students AS s ON s.id = r.student_id
            WHERE r.session_id = ?
            ORDER BY r.id
            """,
            (session_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        raise AcceptanceError(
            "reviewed results could not be verified safely"
        ) from exc
    finally:
        connection.close()
    expected = {
        str(row["student_code"]): float(row["student_score"])
        for row in expected_rows
    }

    try:
        from openpyxl import load_workbook

        workbook = load_workbook(
            report_path,
            read_only=True,
            data_only=True,
        )
        try:
            if "成绩与小题明细" not in workbook.sheetnames:
                raise AcceptanceError("exported report is missing the score sheet")
            rows = workbook["成绩与小题明细"].iter_rows(values_only=True)
            header_row = next(rows)
            headers = {
                str(value).strip(): index
                for index, value in enumerate(header_row)
                if value is not None and str(value).strip()
            }
            if "学号" not in headers or "总分" not in headers:
                raise AcceptanceError(
                    "exported report is missing required score columns"
                )
            actual: dict[str, float] = {}
            for row in rows:
                raw_code = row[headers["学号"]]
                raw_score = row[headers["总分"]]
                if raw_code is None and raw_score is None:
                    continue
                student_code = str(raw_code or "").strip()
                try:
                    score = float(raw_score)
                except (TypeError, ValueError) as exc:
                    raise AcceptanceError(
                        "report conflicts with reviewed results"
                    ) from exc
                if (
                    not student_code
                    or student_code in actual
                    or not math.isfinite(score)
                ):
                    raise AcceptanceError(
                        "report conflicts with reviewed results"
                    )
                actual[student_code] = score
        finally:
            workbook.close()
    except AcceptanceError:
        raise
    except (OSError, KeyError, StopIteration, ValueError) as exc:
        raise AcceptanceError(
            "exported report could not be verified safely"
        ) from exc
    if set(actual) != set(expected) or any(
        abs(actual[student_code] - score) > 1e-6
        for student_code, score in expected.items()
    ):
        raise AcceptanceError("report conflicts with reviewed results")
    return len(actual), _sha256_file(report_path)


def build_consistency_report(
    workspace: Path | str,
    *,
    stage: str,
    session_id: int,
    source_data_root: Path | str,
    report_file: Path | str | None = None,
) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "runtime_ready":
        raise AcceptanceError("acceptance runtime is not ready")
    clean_stage = str(stage or "").strip()
    stages = ("session", "config", "template", "grading", "review", "report")
    if clean_stage not in stages:
        raise AcceptanceError("unsupported consistency stage")
    if isinstance(session_id, bool) or not isinstance(session_id, int) or session_id < 1:
        raise AcceptanceError("acceptance session identity is invalid")

    authorized_session_id = int(metadata["authorized_session_id"])
    paper_limit = int(metadata["paper_count"])
    source_fingerprint = _current_authorized_source_fingerprint(
        source_data_root,
        session_id=authorized_session_id,
        paper_limit=paper_limit,
    )
    if source_fingerprint != metadata["source_fingerprint"]:
        raise AcceptanceError("authorized source changed after preparation")

    database = target / "acceptance_data" / "databases" / "grading_system.db"
    if not database.is_file():
        raise AcceptanceError("acceptance database is unavailable")
    connection = sqlite3.connect(
        f"file:{database.as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    try:
        session = connection.execute(
            """
            SELECT status, COALESCE(is_deleted, 0) AS is_deleted,
                   rubric_path, answer_key_path
            FROM grading_sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        papers = int(
            connection.execute(
                "SELECT COUNT(*) FROM exam_papers WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        results = int(
            connection.execute(
                "SELECT COUNT(*) FROM session_results WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        details = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM session_details AS d
                JOIN session_results AS r ON r.id = d.result_id
                WHERE r.session_id = ?
                """,
                (session_id,),
            ).fetchone()[0]
        )
    except sqlite3.Error as exc:
        raise AcceptanceError("acceptance session could not be verified safely") from exc
    finally:
        connection.close()
    if session is None or bool(session["is_deleted"]):
        raise AcceptanceError("acceptance session is missing or deleted")

    data_root = (target / "acceptance_data").resolve()
    report: dict[str, object] = {
        "package": PACKAGE,
        "stage": clean_stage,
        "ok": True,
        "source_unchanged": True,
        "session_id": session_id,
        "session_status": str(session["status"]),
        "counts": {
            "papers": papers,
            "results": results,
            "details": details,
        },
    }
    if stages.index(clean_stage) >= stages.index("config"):
        question_ids, scoring_ids, question_catalog, config_hash = _config_summary(
            session,
            data_root=data_root,
        )
        report.update(
            {
                "question_count": len(question_ids),
                "config_sha256": config_hash,
            }
        )
    if stages.index(clean_stage) >= stages.index("template"):
        region_count, template_hash = _template_summary(
            database,
            session_id=session_id,
            data_root=data_root,
            question_catalog=question_catalog,
        )
        report.update(
            {
                "region_count": region_count,
                "template_confirmed": True,
                "template_sha256": template_hash,
            }
        )
    if stages.index(clean_stage) >= stages.index("grading"):
        _grading_summary(
            database,
            session_id=session_id,
            data_root=data_root,
            expected_paper_count=paper_limit,
            scoring_ids=scoring_ids,
        )
        report["grading_complete"] = True
    if stages.index(clean_stage) >= stages.index("review"):
        pending_review_count = _pending_teacher_review_count(
            database,
            session_id=session_id,
        )
        if pending_review_count:
            raise AcceptanceError("teacher review is incomplete")
        report.update(
            {
                "teacher_review_complete": True,
                "pending_review_count": 0,
            }
        )
    if clean_stage == "report":
        report_row_count, report_hash = _report_summary(
            database,
            session_id=session_id,
            data_root=data_root,
            report_file=report_file,
        )
        report.update(
            {
                "report_matches_results": True,
                "report_row_count": report_row_count,
                "report_sha256": report_hash,
            }
        )
    evidence_dir = target / "acceptance_evidence"
    evidence_dir.mkdir(exist_ok=True)
    evidence_path = evidence_dir / f"consistency-{clean_stage}.json"
    temporary_path = evidence_path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary_path.replace(evidence_path)
    return report


def _require_nonempty_string(profile: dict[str, object], key: str) -> str:
    value = profile.get(key)
    if not isinstance(value, str) or not value.strip():
        raise AcceptanceError("active API profile is not fully configured")
    return value.strip()


def _validate_upstream_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise AcceptanceError("active API profile is not fully configured")
    return value.rstrip("/")


def _validate_proxy_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port is None
        or not parsed.path.rstrip("/").endswith("/acceptance-llm/v1")
    ):
        raise AcceptanceError("acceptance proxy must use a dedicated loopback URL")
    return value.rstrip("/")


def prepare_runtime_profile(
    workspace: Path | str,
    *,
    source_profile_path: Path | str,
    proxy_base_url: str,
    max_forwarded_requests: int,
) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "inputs_prepared":
        raise AcceptanceError("authorized inputs must be prepared before runtime config")
    if (
        isinstance(max_forwarded_requests, bool)
        or not isinstance(max_forwarded_requests, int)
        or max_forwarded_requests < 1
        or max_forwarded_requests > 4
    ):
        raise AcceptanceError("model request budget must be between 1 and 4")
    proxy_url = _validate_proxy_base_url(proxy_base_url)
    source_path = Path(source_profile_path).expanduser().resolve()
    if not source_path.is_file() or _is_relative_to(source_path, target):
        raise AcceptanceError("machine-local API profile is missing or unsafe")
    profile_fingerprint = _sha256_file(source_path)
    try:
        profiles = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("machine-local API profile cannot be read") from exc
    if not isinstance(profiles, list) or not profiles or not isinstance(profiles[-1], dict):
        raise AcceptanceError("machine-local API profile is invalid")
    active: dict[str, object] = profiles[-1]
    provider = _require_nonempty_string(active, "provider")
    config_provider = str(active.get("config_provider") or provider).strip()
    if provider != "custom-openai-compatible" or config_provider != provider:
        raise AcceptanceError("P2-20 requires an OpenAI-compatible active profile")
    grading_base_url = _validate_upstream_url(
        _require_nonempty_string(active, "base_url")
    )
    grading_api_key = _require_nonempty_string(active, "api_key")
    grading_model = _require_nonempty_string(active, "grading_model")
    config_base_url = _validate_upstream_url(
        str(active.get("config_base_url") or grading_base_url)
    )
    config_api_key = str(active.get("config_api_key") or grading_api_key).strip()
    config_model = _require_nonempty_string(active, "config_model")
    if not config_api_key:
        raise AcceptanceError("active API profile is not fully configured")

    config_dir = target / "acceptance_config"
    if config_dir.exists():
        raise AcceptanceError("acceptance runtime config already exists")
    config_dir.mkdir()
    runtime_profile = {
        "name": "P2-20 Acceptance",
        "provider": provider,
        "base_url": proxy_url,
        "api_key": "acceptance-proxy",
        "grading_model": grading_model,
        "ocr_model": "p2-20-local-ocr",
        "config_provider": config_provider,
        "config_base_url": proxy_url,
        "config_api_key": "acceptance-proxy",
        "config_model": config_model,
        "objective_enabled": False,
        "grading_max_workers": 1,
        "precheck_max_workers": 1,
        "hybrid_inflight_workers": 1,
        "llm_grading_max_retries": 0,
        "llm_config_generation_max_retries": 0,
        "llm_recognition_max_retries": 0,
    }
    upstream = {
        "grading": {
            "base_url": grading_base_url,
            "api_key": grading_api_key,
            "model": grading_model,
        },
        "config": {
            "base_url": config_base_url,
            "api_key": config_api_key,
            "model": config_model,
        },
        "max_forwarded_requests": max_forwarded_requests,
    }
    runtime_profile_path = config_dir / "api_profiles.json"
    upstream_path = config_dir / "upstream.json"
    runtime_profile_path.write_text(
        json.dumps([runtime_profile], ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    upstream_path.write_text(
        json.dumps(upstream, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    runtime_profile_path.chmod(0o600)
    upstream_path.chmod(0o600)
    if _sha256_file(source_path) != profile_fingerprint:
        raise AcceptanceError("machine-local API profile changed while it was copied")
    runtime_metadata: dict[str, object] = {
        **metadata,
        "state": "runtime_ready",
        "profile_fingerprint": profile_fingerprint,
        "model_request_budget": max_forwarded_requests,
    }
    _write_metadata(target, runtime_metadata)
    return load_metadata(target)


ModelForwarder = Callable[
    [dict[str, str], dict[str, object]],
    Awaitable[tuple[int, dict[str, str], bytes]],
]


def _load_upstream_config(path: Path) -> dict[str, object]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("acceptance upstream config is unavailable") from exc
    if not isinstance(config, dict):
        raise AcceptanceError("acceptance upstream config is invalid")
    if (
        not isinstance(config.get("max_forwarded_requests"), int)
        or not 1 <= int(config["max_forwarded_requests"]) <= 4
    ):
        raise AcceptanceError("acceptance upstream config is invalid")
    for kind in ("grading", "config"):
        upstream = config.get(kind)
        if not isinstance(upstream, dict):
            raise AcceptanceError("acceptance upstream config is invalid")
        for key in ("base_url", "api_key", "model"):
            if not isinstance(upstream.get(key), str) or not upstream[key].strip():
                raise AcceptanceError("acceptance upstream config is invalid")
        _validate_upstream_url(str(upstream["base_url"]))
    return config


def _load_budget_state(state_path: Path, maximum: int) -> dict[str, int]:
    if not state_path.exists():
        return {
            "forwarded_requests": 0,
            "local_ocr_requests": 0,
            "max_forwarded_requests": maximum,
        }
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("model budget state is unavailable") from exc
    expected = {
        "forwarded_requests",
        "local_ocr_requests",
        "max_forwarded_requests",
    }
    if (
        not isinstance(state, dict)
        or set(state) != expected
        or any(
            isinstance(state[key], bool) or not isinstance(state[key], int)
            for key in expected
        )
        or state["forwarded_requests"] < 0
        or state["local_ocr_requests"] < 0
        or state["max_forwarded_requests"] != maximum
        or state["forwarded_requests"] > maximum
    ):
        raise AcceptanceError("model budget state is invalid")
    return state


def _write_budget_state(state_path: Path, state: dict[str, int]) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = state_path.with_suffix(f"{state_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(state, ensure_ascii=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(state_path)


async def _default_model_forwarder(
    upstream: dict[str, str],
    payload: dict[str, object],
) -> tuple[int, dict[str, str], bytes]:
    import httpx

    url = f"{upstream['base_url'].rstrip('/')}/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=600.0) as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {upstream['api_key']}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise AcceptanceError("approved model request failed at the provider") from exc
    return (
        response.status_code,
        {"content-type": response.headers.get("content-type", "application/json")},
        response.content,
    )


def create_model_budget_proxy(
    upstream_config_path: Path | str,
    *,
    state_path: Path | str,
    forwarder: ModelForwarder | None = None,
):
    from fastapi import FastAPI, Header, HTTPException
    from fastapi.responses import Response

    upstream_path = Path(upstream_config_path).resolve()
    budget_path = Path(state_path).resolve()
    config = _load_upstream_config(upstream_path)
    maximum = int(config["max_forwarded_requests"])
    state = _load_budget_state(budget_path, maximum)
    _write_budget_state(budget_path, state)
    state_lock = threading.Lock()
    dispatch = forwarder or _default_model_forwarder
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.post("/v1/chat/completions")
    async def chat_completions(
        payload: dict[str, Any],
        authorization: str | None = Header(default=None),
    ):
        if authorization != "Bearer acceptance-proxy":
            raise HTTPException(status_code=401, detail="acceptance proxy key required")
        model = payload.get("model")
        if model == "p2-20-local-ocr":
            with state_lock:
                state["local_ocr_requests"] += 1
                _write_budget_state(budget_path, state)
            return {
                "id": "p2-20-local-ocr",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": "p2-20-local-ocr",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "NOT_FOUND",
                        },
                        "finish_reason": "stop",
                    }
                ],
            }
        candidates = [
            dict(config[kind])
            for kind in ("config", "grading")
            if isinstance(config[kind], dict) and config[kind].get("model") == model
        ]
        if not candidates:
            raise HTTPException(status_code=403, detail="model is outside P2-20 scope")
        with state_lock:
            if state["forwarded_requests"] >= maximum:
                raise HTTPException(
                    status_code=429,
                    detail="P2-20 model request budget exhausted",
                )
            state["forwarded_requests"] += 1
            _write_budget_state(budget_path, state)
        try:
            status_code, headers, content = await dispatch(candidates[0], payload)
        except AcceptanceError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return Response(
            content=content,
            status_code=status_code,
            media_type=headers.get("content-type", "application/json"),
        )

    return app


def _replace_config_value(line: str, value: str) -> str:
    if line.endswith("\r\n"):
        ending = "\r\n"
    elif line.endswith("\n"):
        ending = "\n"
    else:
        ending = ""
    return f"{value}{ending}"


def configure_staged_runtime(workspace: Path | str) -> None:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "runtime_ready":
        raise AcceptanceError("acceptance runtime profile has not been prepared")
    config_path = target / "config" / "app_config.yaml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    except (OSError, UnicodeError) as exc:
        raise AcceptanceError("staged app config is unavailable") from exc
    positions: dict[str, list[int]] = {"DATA_DIR": [], "LOGS_DIR": []}
    for index, line in enumerate(lines):
        match = _CONFIG_KEY_RE.match(line)
        if match:
            positions[match.group("key").strip("\"'")].append(index)
    if any(len(indexes) != 1 for indexes in positions.values()):
        raise AcceptanceError(
            "staged config must contain exactly one DATA_DIR and LOGS_DIR key"
        )
    lines[positions["DATA_DIR"][0]] = _replace_config_value(
        lines[positions["DATA_DIR"][0]],
        "DATA_DIR: acceptance_data",
    )
    lines[positions["LOGS_DIR"][0]] = _replace_config_value(
        lines[positions["LOGS_DIR"][0]],
        "LOGS_DIR: acceptance_logs",
    )
    temporary = config_path.with_suffix(".yaml.tmp")
    temporary.write_text("".join(lines), encoding="utf-8", newline="")
    temporary.replace(config_path)
    for relative in ("acceptance_data", "acceptance_logs", "acceptance_ops"):
        directory = target / relative
        directory.mkdir(exist_ok=False)


def _sanitize_build_log(text: str, *, workspace: Path, repo_root: Path) -> str:
    sanitized = text
    replacements = (
        (workspace, "<workspace>"),
        (repo_root, "<source-repo>"),
    )
    for path, placeholder in replacements:
        native = str(path.resolve())
        for variant in {native, native.replace("\\", "/"), native.replace("/", "\\")}:
            sanitized = re.sub(
                re.escape(variant),
                lambda _match, value=placeholder: value,
                sanitized,
                flags=re.IGNORECASE,
            )
    return sanitized


def build_and_copy_frontend(
    workspace: Path | str,
    *,
    repo_root: Path | str,
    expected_source_sha: str,
) -> None:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["source_sha"] != expected_source_sha:
        raise AcceptanceError("frontend build source does not match prepared code")
    source_root = Path(repo_root).resolve()
    resolved = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if resolved.returncode != 0 or resolved.stdout.strip() != expected_source_sha:
        raise AcceptanceError("frontend build requires the exact source commit")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if status.returncode != 0 or status.stdout.strip():
        raise AcceptanceError("frontend build source must have no tracked changes")
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm:
        raise AcceptanceError("npm is required to build the acceptance frontend")
    completed = subprocess.run(
        [npm, "run", "build"],
        cwd=source_root / "frontend",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    log_path = target / "acceptance_logs" / "frontend-build.log"
    log_path.write_text(
        _sanitize_build_log(
            (completed.stdout or "") + (completed.stderr or ""),
            workspace=target,
            repo_root=source_root,
        ),
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise AcceptanceError("frontend build failed; see the sanitized workspace log")
    source_dist = source_root / "frontend" / "dist"
    if not (source_dist / "index.html").is_file() or not (
        source_dist / "assets"
    ).is_dir():
        raise AcceptanceError("frontend build did not produce a complete dist")
    destination = target / "frontend" / "dist"
    if destination.exists():
        raise AcceptanceError("acceptance frontend dist already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_dist, destination)


def _validate_staged_config(workspace: Path) -> None:
    config_path = workspace / "config" / "app_config.yaml"
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise AcceptanceError("prepared app config is unavailable") from exc
    values: dict[str, list[str]] = {"DATA_DIR": [], "LOGS_DIR": []}
    for line in lines:
        match = _CONFIG_KEY_RE.match(line)
        if match:
            key = match.group("key").strip("\"'")
            values[key].append(line.split(":", 1)[1].strip())
    if values != {
        "DATA_DIR": ["acceptance_data"],
        "LOGS_DIR": ["acceptance_logs"],
    }:
        raise AcceptanceError("prepared app config is not acceptance-safe")


def create_acceptance_app(
    workspace: Path | str,
    control_token: str | None = None,
):
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "runtime_ready":
        raise AcceptanceError("acceptance runtime is not ready")
    _validate_staged_config(target)
    frontend_dist = target / "frontend" / "dist"
    if not (frontend_dist / "index.html").is_file() or not (
        frontend_dist / "assets"
    ).is_dir():
        raise AcceptanceError("acceptance frontend dist is missing")
    launcher_root = Path(__file__).resolve().parents[1]
    if launcher_root != target:
        raise AcceptanceError("acceptance server must run from the staged workspace")

    os.environ["AI_GRADING_DATA_DIR"] = str(target / "acceptance_data")
    os.environ["AI_GRADING_API_PROFILES_PATH"] = str(
        target / "acceptance_config" / "api_profiles.json"
    )
    os.environ["AI_GRADING_OPS_STATE_DIR"] = str(target / "acceptance_ops")
    for name in (
        "LLM_API_KEY",
        "LLM_CONFIG_API_KEY",
        "LLM_OBJECTIVE_API_KEY",
        "OPENAI_API_KEY",
        "QUESTION_BANK_TAGGING_API_KEY",
        "QUESTION_BANK_TAGGING_REVIEW_API_KEY",
    ):
        os.environ[name] = ""

    from path_manager import PathManager

    paths = PathManager()
    if (
        paths.project_root.resolve() != target
        or paths.data_root.resolve() != (target / "acceptance_data").resolve()
        or paths.api_profiles_path.resolve()
        != (target / "acceptance_config" / "api_profiles.json").resolve()
        or paths.ops_state_dir.resolve() != (target / "acceptance_ops").resolve()
    ):
        raise AcceptanceError("acceptance runtime resolved outside the workspace")
    paths.ensure_directories()

    from db_manager import DBManager
    from question_bank.database.schema import initialize_database

    DBManager(paths.db_path).initialize()
    initialize_database(paths.qb_db_path, seed_skills=False)

    from backend.api.app import create_app
    from fastapi import HTTPException, Request as FastAPIRequest
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app = create_app(path_manager=paths)
    proxy = create_model_budget_proxy(
        target / "acceptance_config" / "upstream.json",
        state_path=target / "acceptance_config" / "budget-state.json",
    )
    app.mount("/acceptance-llm", proxy, name="p2-20-model-budget")
    app.mount(
        "/assets",
        StaticFiles(directory=frontend_dist / "assets"),
        name="p2-20-assets",
    )

    @app.get("/acceptance-control/health", include_in_schema=False)
    def acceptance_control_health(request: FastAPIRequest):
        supplied_token = request.headers.get("x-acceptance-control-token")
        if (
            control_token is None
            or supplied_token is None
            or not hmac.compare_digest(supplied_token, control_token)
        ):
            raise HTTPException(status_code=404)
        return {
            "package": PACKAGE,
            "source_sha": str(metadata["source_sha"]),
        }

    @app.get("/{frontend_path:path}", include_in_schema=False)
    def serve_frontend(frontend_path: str):
        if frontend_path.startswith(
            ("api/", "acceptance-llm/", "acceptance-control/")
        ):
            raise HTTPException(status_code=404)
        return FileResponse(frontend_dist / "index.html")

    return app


def _server_state_path(workspace: Path) -> Path:
    return workspace / "acceptance_config" / SERVER_STATE_FILENAME


def _server_claim_path(workspace: Path) -> Path:
    return workspace / "acceptance_config" / SERVER_CLAIM_FILENAME


def _server_stop_path(workspace: Path) -> Path:
    return workspace / "acceptance_config" / SERVER_STOP_FILENAME


def _validate_control_token(value: object) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 256:
        raise AcceptanceError("acceptance server control token is invalid")
    return value


def _load_server_claim(workspace: Path) -> dict[str, object] | None:
    claim_path = _server_claim_path(workspace)
    if not claim_path.exists():
        return None
    try:
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("acceptance server start claim is unavailable") from exc
    expected = {
        "package",
        "source_sha",
        "port",
        "control_token",
        "owner_pid",
    }
    if (
        not isinstance(claim, dict)
        or set(claim) != expected
        or claim["package"] != PACKAGE
        or not isinstance(claim["source_sha"], str)
        or len(claim["source_sha"]) != 40
        or isinstance(claim["port"], bool)
        or not isinstance(claim["port"], int)
        or not 1024 <= claim["port"] <= 65535
        or isinstance(claim["owner_pid"], bool)
        or not isinstance(claim["owner_pid"], int)
        or claim["owner_pid"] <= 0
    ):
        raise AcceptanceError("acceptance server start claim is invalid")
    _validate_control_token(claim["control_token"])
    return claim


def _claim_server_start(
    workspace: Path,
    *,
    source_sha: str,
    port: int,
    control_token: str,
) -> None:
    _validate_control_token(control_token)
    claim = {
        "package": PACKAGE,
        "source_sha": source_sha,
        "port": port,
        "control_token": control_token,
        "owner_pid": os.getpid(),
    }
    claim_path = _server_claim_path(workspace)
    try:
        descriptor = os.open(
            claim_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
    except FileExistsError as exc:
        raise AcceptanceError(
            "acceptance server start is already claimed; stop it before restarting"
        ) from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(claim, stream, ensure_ascii=True, separators=(",", ":"))
    except BaseException:
        claim_path.unlink(missing_ok=True)
        raise


def _remove_server_claim_for_token(workspace: Path, control_token: str) -> None:
    try:
        claim = _load_server_claim(workspace)
    except AcceptanceError:
        return
    if claim is not None and hmac.compare_digest(
        str(claim["control_token"]),
        control_token,
    ):
        _server_claim_path(workspace).unlink(missing_ok=True)


def _load_server_state(workspace: Path) -> dict[str, object] | None:
    state_path = _server_state_path(workspace)
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("acceptance server state is unavailable") from exc
    expected = {
        "package",
        "source_sha",
        "state",
        "pid",
        "port",
        "control_token",
    }
    if (
        not isinstance(state, dict)
        or set(state) != expected
        or state["package"] != PACKAGE
        or state["state"] not in {"starting", "running"}
        or isinstance(state["pid"], bool)
        or not isinstance(state["pid"], int)
        or state["pid"] <= 0
        or isinstance(state["port"], bool)
        or not isinstance(state["port"], int)
        or not 1024 <= state["port"] <= 65535
        or not isinstance(state["source_sha"], str)
        or len(state["source_sha"]) != 40
    ):
        raise AcceptanceError("acceptance server state is invalid")
    _validate_control_token(state["control_token"])
    return state


def _write_server_state(workspace: Path, state: dict[str, object]) -> None:
    state_path = _server_state_path(workspace)
    temporary = state_path.with_name(
        f".{state_path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_text(
            json.dumps(state, ensure_ascii=True, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(state_path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove_server_state_for_pid(workspace: Path, pid: int) -> None:
    try:
        state = _load_server_state(workspace)
    except AcceptanceError:
        return
    if state is not None and state["pid"] == pid:
        _server_state_path(workspace).unlink(missing_ok=True)


def _server_is_healthy(
    port: int,
    *,
    control_token: str,
    source_sha: str,
) -> bool:
    try:
        request = Request(
            f"http://127.0.0.1:{port}/acceptance-control/health",
            headers={"X-Acceptance-Control-Token": control_token},
        )
        with urlopen(request, timeout=0.5) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read().decode("utf-8"))
        return payload == {
            "package": PACKAGE,
            "source_sha": source_sha,
        }
    except (OSError, URLError, UnicodeError, ValueError, json.JSONDecodeError):
        return False


def _process_is_running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(
                handle,
                ctypes.byref(exit_code),
            ):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _ensure_loopback_port_available(port: int) -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform == "win32":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        probe.bind(("127.0.0.1", port))
    except OSError as exc:
        raise AcceptanceError("acceptance server port is already in use") from exc
    finally:
        probe.close()


def _validate_prepared_proxy_port(workspace: Path, port: int) -> None:
    try:
        profiles = json.loads(
            (
                workspace / "acceptance_config" / "api_profiles.json"
            ).read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("prepared API profile is unavailable") from exc
    expected_proxy = f"http://127.0.0.1:{port}/acceptance-llm/v1"
    if (
        not isinstance(profiles, list)
        or len(profiles) != 1
        or profiles[0].get("base_url") != expected_proxy
    ):
        raise AcceptanceError("serve port does not match the prepared model proxy")


def start_acceptance_server(
    workspace: Path | str,
    port: int,
    *,
    ready_timeout: float = 30.0,
) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    if metadata["state"] != "runtime_ready":
        raise AcceptanceError("acceptance runtime is not ready")
    validated_port = _validate_port(port)
    _validate_prepared_proxy_port(target, validated_port)
    existing = _load_server_state(target)
    if existing is not None:
        if (
            existing["source_sha"] == metadata["source_sha"]
            and existing["port"] == validated_port
            and _process_is_running(int(existing["pid"]))
            and _server_is_healthy(
                validated_port,
                control_token=str(existing["control_token"]),
                source_sha=str(existing["source_sha"]),
            )
        ):
            result = safe_status(target)
            result.update({"server_state": "running", "port": validated_port})
            return result
        raise AcceptanceError(
            "acceptance server state is stale; stop it before restarting"
        )
    if _load_server_claim(target) is not None:
        raise AcceptanceError(
            "acceptance server start is already claimed; stop it before restarting"
        )
    control_token = secrets.token_urlsafe(32)
    _claim_server_start(
        target,
        source_sha=str(metadata["source_sha"]),
        port=validated_port,
        control_token=control_token,
    )
    process: subprocess.Popen[bytes] | None = None
    try:
        _ensure_loopback_port_available(validated_port)
        _server_stop_path(target).unlink(missing_ok=True)
        stdout_path = target / "acceptance_logs" / "server-stdout.log"
        stderr_path = target / "acceptance_logs" / "server-stderr.log"
        creationflags = 0
        if sys.platform == "win32":
            creationflags = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "DETACHED_PROCESS", 0)
            )
        command = [
            sys.executable,
            str(target / "tools" / "p2_20_acceptance.py"),
            "serve",
            "--workspace",
            str(target),
            "--port",
            str(validated_port),
            "--control-token",
            control_token,
        ]
        with stdout_path.open("ab") as stdout, stderr_path.open("ab") as stderr:
            process = subprocess.Popen(
                command,
                cwd=target,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                close_fds=True,
                creationflags=creationflags,
            )
    except BaseException:
        _remove_server_claim_for_token(target, control_token)
        raise
    assert process is not None
    startup_failure = "acceptance server did not become healthy in time"
    deadline = time.monotonic() + max(1.0, float(ready_timeout))
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _remove_server_state_for_pid(target, process.pid)
            _remove_server_claim_for_token(target, control_token)
            raise AcceptanceError(
                "acceptance server exited during startup; see workspace logs"
            )
        if _server_is_healthy(
            validated_port,
            control_token=control_token,
            source_sha=str(metadata["source_sha"]),
        ):
            registered = _load_server_state(target)
            if (
                registered is None
                or registered["pid"] != process.pid
                or not hmac.compare_digest(
                    str(registered["control_token"]),
                    control_token,
                )
            ):
                break
            registered["state"] = "running"
            try:
                _write_server_state(target, registered)
            except OSError:
                startup_failure = "acceptance server running state could not be saved"
                break
            result = safe_status(target)
            if result["server_state"] == "running":
                result.update({"port": validated_port})
                return result
            startup_failure = "acceptance server lost health during startup"
            break
        time.sleep(0.2)
    _server_stop_path(target).touch(exist_ok=True)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    _remove_server_state_for_pid(target, process.pid)
    _remove_server_claim_for_token(target, control_token)
    raise AcceptanceError(startup_failure)


def stop_acceptance_server(
    workspace: Path | str,
    *,
    stop_timeout: float = 20.0,
) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    state = _load_server_state(target)
    if state is None:
        claim = _load_server_claim(target)
        if claim is not None:
            if _process_is_running(int(claim["owner_pid"])):
                raise AcceptanceError(
                    "acceptance server start is still in progress; retry stop shortly"
                )
            _remove_server_claim_for_token(
                target,
                str(claim["control_token"]),
            )
        result = safe_status(target)
        result.update({"server_state": "stopped"})
        return result
    if state["source_sha"] != metadata["source_sha"]:
        raise AcceptanceError("acceptance server source does not match the workspace")
    claim = _load_server_claim(target)
    if (
        claim is None
        or claim["source_sha"] != state["source_sha"]
        or claim["port"] != state["port"]
        or not hmac.compare_digest(
            str(claim["control_token"]),
            str(state["control_token"]),
        )
    ):
        raise AcceptanceError("acceptance server ownership is inconsistent")
    port = int(state["port"])
    _server_stop_path(target).touch(exist_ok=True)
    deadline = time.monotonic() + max(1.0, float(stop_timeout))
    while time.monotonic() < deadline:
        if not _process_is_running(int(state["pid"])):
            _server_state_path(target).unlink(missing_ok=True)
            _server_stop_path(target).unlink(missing_ok=True)
            _remove_server_claim_for_token(
                target,
                str(state["control_token"]),
            )
            result = safe_status(target)
            result.update({"server_state": "stopped", "port": port})
            return result
        time.sleep(0.2)
    raise AcceptanceError("acceptance server did not stop in time")


def run_acceptance_server(
    workspace: Path | str,
    port: int,
    control_token: str,
) -> None:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    validated_port = _validate_port(port)
    _validate_control_token(control_token)
    claim = _load_server_claim(target)
    if (
        claim is None
        or claim["source_sha"] != metadata["source_sha"]
        or claim["port"] != validated_port
        or not hmac.compare_digest(
            str(claim["control_token"]),
            control_token,
        )
    ):
        raise AcceptanceError("acceptance server start claim does not match")
    state = {
        "package": PACKAGE,
        "source_sha": metadata["source_sha"],
        "state": "starting",
        "pid": os.getpid(),
        "port": validated_port,
        "control_token": control_token,
    }
    try:
        _write_server_state(target, state)
        app = create_acceptance_app(target, control_token)
        import uvicorn

        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=validated_port)
        )
        stop_path = _server_stop_path(target)

        def watch_for_stop() -> None:
            while not server.should_exit:
                if stop_path.exists():
                    server.should_exit = True
                    return
                time.sleep(0.2)

        watcher = threading.Thread(target=watch_for_stop, daemon=True)
        watcher.start()
        server.run()
    finally:
        _server_stop_path(target).unlink(missing_ok=True)
        _remove_server_state_for_pid(target, os.getpid())
        _remove_server_claim_for_token(target, control_token)


def safe_status(workspace: Path | str) -> dict[str, object]:
    target = validate_workspace(workspace, require_empty=False)
    metadata = load_metadata(target)
    result = {
        key: metadata[key]
        for key in (
            "package",
            "state",
            "source_sha",
            "authorized_session_id",
            "paper_count",
            "model_request_budget",
        )
        if key in metadata
    }
    budget_path = target / "acceptance_config" / "budget-state.json"
    if "model_request_budget" in metadata:
        state = _load_budget_state(
            budget_path,
            int(metadata["model_request_budget"]),
        )
        result.update(
            {
                "forwarded_requests": state["forwarded_requests"],
                "local_ocr_requests": state["local_ocr_requests"],
            }
        )
    server = _load_server_state(target)
    if server is None:
        result["server_state"] = "stopped"
    else:
        port = int(server["port"])
        healthy = (
            _process_is_running(int(server["pid"]))
            and _server_is_healthy(
                port,
                control_token=str(server["control_token"]),
                source_sha=str(server["source_sha"]),
            )
        )
        result.update(
            {
                "server_state": (
                    "running"
                    if healthy
                    else (
                        "starting"
                        if server["state"] == "starting"
                        else "unhealthy"
                    )
                ),
                "port": port,
            }
        )
    return result


def _validate_port(port: int) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
        raise AcceptanceError("port must be between 1024 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="P2-20 bounded acceptance launcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--source-ref", required=True)
    prepare.add_argument("--workspace", type=Path, required=True)
    prepare.add_argument("--source-data-root", type=Path, required=True)
    prepare.add_argument("--session-id", type=int, required=True)
    prepare.add_argument("--session-name", required=True)
    prepare.add_argument("--profile-path", type=Path, required=True)
    prepare.add_argument("--port", type=int, required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--workspace", type=Path, required=True)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--control-token", required=True)
    start = subparsers.add_parser("start")
    start.add_argument("--workspace", type=Path, required=True)
    start.add_argument("--port", type=int, required=True)
    stop = subparsers.add_parser("stop")
    stop.add_argument("--workspace", type=Path, required=True)
    status = subparsers.add_parser("status")
    status.add_argument("--workspace", type=Path, required=True)
    report = subparsers.add_parser("report")
    report.add_argument("--workspace", type=Path, required=True)
    report.add_argument(
        "--stage",
        choices=("session", "config", "template", "grading", "review", "report"),
        required=True,
    )
    report.add_argument("--session-id", type=int, required=True)
    report.add_argument("--source-data-root", type=Path, required=True)
    report.add_argument("--report-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            port = _validate_port(args.port)
            code_metadata = prepare_code_workspace(
                args.source_ref,
                args.workspace,
                repo_root=REPO_ROOT,
            )
            prepare_authorized_inputs(
                args.workspace,
                source_data_root=args.source_data_root,
                session_id=args.session_id,
                expected_session_name=args.session_name,
                paper_limit=3,
            )
            prepare_runtime_profile(
                args.workspace,
                source_profile_path=args.profile_path,
                proxy_base_url=(
                    f"http://127.0.0.1:{port}/acceptance-llm/v1"
                ),
                max_forwarded_requests=4,
            )
            configure_staged_runtime(args.workspace)
            build_and_copy_frontend(
                args.workspace,
                repo_root=REPO_ROOT,
                expected_source_sha=str(code_metadata["source_sha"]),
            )
            result = safe_status(args.workspace)
        elif args.command == "status":
            result = safe_status(args.workspace)
        elif args.command == "start":
            result = start_acceptance_server(args.workspace, args.port)
        elif args.command == "stop":
            result = stop_acceptance_server(args.workspace)
        elif args.command == "report":
            result = build_consistency_report(
                args.workspace,
                stage=args.stage,
                session_id=args.session_id,
                source_data_root=args.source_data_root,
                report_file=args.report_file,
            )
        elif args.command == "serve":
            port = _validate_port(args.port)
            target = validate_workspace(args.workspace, require_empty=False)
            _validate_prepared_proxy_port(target, port)
            run_acceptance_server(target, port, args.control_token)
            return 0
        else:
            raise AcceptanceError("unsupported acceptance command")
        print(json.dumps(result, ensure_ascii=True, separators=(",", ":")))
        return 0
    except KeyboardInterrupt:
        return 130
    except (AcceptanceError, OSError, KeyError, ValueError) as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
