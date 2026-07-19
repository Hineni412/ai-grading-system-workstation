from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import subprocess
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools import p2_20_acceptance as acceptance


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _committed_source_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "source"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "p2-20@example.invalid")
    _git(repo, "config", "user.name", "P2-20 Test")
    (repo / "safe.txt").write_text("safe", encoding="utf-8")
    protected = repo / "user_data"
    protected.mkdir()
    (protected / "secret.txt").write_text("must not stage", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _authorized_source_data(tmp_path: Path, *, paper_count: int = 3) -> Path:
    data_root = tmp_path / "source-user-data"
    database_dir = data_root / "databases"
    exam_dir = data_root / "exams" / "session_1"
    template_dir = data_root / "templates" / "session_1"
    database_dir.mkdir(parents=True)
    exam_dir.mkdir(parents=True)
    template_dir.mkdir(parents=True)
    source_paper = exam_dir / "source.docx"
    source_paper.write_bytes(b"authorized exam source")
    (template_dir / "template_source_full_class.pdf").write_bytes(
        b"%PDF-1.4 authorized template"
    )
    database = database_dir / "grading_system.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE grading_sessions (
            id INTEGER PRIMARY KEY,
            session_name TEXT NOT NULL,
            source_paper_path TEXT,
            is_deleted INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE students (
            id INTEGER PRIMARY KEY,
            student_code TEXT NOT NULL,
            name TEXT NOT NULL,
            class_name TEXT
        );
        CREATE TABLE exam_papers (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            front_image TEXT,
            back_image TEXT,
            student_id INTEGER,
            match_status TEXT,
            processing_status TEXT
        );
        CREATE TABLE session_results (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            paper_id INTEGER NOT NULL
        );
        CREATE TABLE session_templates (
            id INTEGER PRIMARY KEY,
            session_id INTEGER NOT NULL,
            is_confirmed INTEGER NOT NULL
        );
        """
    )
    connection.execute(
        "INSERT INTO grading_sessions(id, session_name, source_paper_path) VALUES(1, ?, ?)",
        ("0609", str(source_paper)),
    )
    connection.execute(
        "INSERT INTO session_templates(id, session_id, is_confirmed) VALUES(1, 1, 1)"
    )
    for index in range(1, paper_count + 1):
        front = exam_dir / f"student-{index}-front.jpg"
        back = exam_dir / f"student-{index}-back.jpg"
        front.write_bytes(f"front-{index}".encode())
        back.write_bytes(f"back-{index}".encode())
        connection.execute(
            "INSERT INTO students(id, student_code, name, class_name) VALUES(?, ?, ?, ?)",
            (index, f"S{index:03}", f"学生{index}", "七年级一班"),
        )
        connection.execute(
            """
            INSERT INTO exam_papers(
                id, session_id, front_image, back_image, student_id,
                match_status, processing_status
            ) VALUES(?, 1, ?, ?, ?, 'matched', 'graded')
            """,
            (100 + index, str(front), str(back), index),
        )
        connection.execute(
            """
            INSERT INTO session_results(id, session_id, student_id, paper_id)
            VALUES(?, 1, ?, ?)
            """,
            (200 + index, index, 100 + index),
        )
    connection.commit()
    connection.close()
    return data_root


def test_validate_workspace_accepts_only_empty_directory_below_system_temp(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "p2-20-workspace"

    assert acceptance.validate_workspace(workspace) == workspace.resolve()

    workspace.mkdir()
    (workspace / "unexpected.txt").write_text("occupied", encoding="utf-8")
    with pytest.raises(acceptance.AcceptanceError, match="empty"):
        acceptance.validate_workspace(workspace)


def test_validate_workspace_rejects_temp_root_repository_and_user_data_paths(
    tmp_path: Path,
) -> None:
    repo_root = Path(acceptance.__file__).resolve().parents[1]

    with pytest.raises(acceptance.AcceptanceError, match="strictly below"):
        acceptance.validate_workspace(Path(tempfile.gettempdir()))
    with pytest.raises(acceptance.AcceptanceError, match="system temporary"):
        acceptance.validate_workspace(repo_root / "output" / "p2-20")
    with pytest.raises(acceptance.AcceptanceError, match="user_data"):
        acceptance.validate_workspace(tmp_path / "user_data" / "p2-20")


def test_prepare_code_workspace_stages_exact_commit_without_user_data(
    tmp_path: Path,
) -> None:
    repo, source_sha = _committed_source_repo(tmp_path)
    workspace = tmp_path / "prepared"

    metadata = acceptance.prepare_code_workspace(
        source_sha,
        workspace,
        repo_root=repo,
    )

    assert metadata == {
        "package": "P2-20",
        "source_sha": source_sha,
        "state": "code_prepared",
        "authorized_session_id": None,
    }
    assert (workspace / "safe.txt").read_text(encoding="utf-8") == "safe"
    assert not (workspace / "user_data").exists()
    assert acceptance.load_metadata(workspace) == metadata


def test_prepare_code_workspace_rejects_symbolic_ref(tmp_path: Path) -> None:
    repo, _source_sha = _committed_source_repo(tmp_path)

    with pytest.raises(acceptance.AcceptanceError, match="40-character"):
        acceptance.prepare_code_workspace(
            "HEAD",
            tmp_path / "prepared",
            repo_root=repo,
        )


def test_prepare_authorized_inputs_copies_only_selected_exam_materials(
    tmp_path: Path,
) -> None:
    repo, source_sha = _committed_source_repo(tmp_path)
    workspace = tmp_path / "prepared"
    acceptance.prepare_code_workspace(source_sha, workspace, repo_root=repo)
    source_data = _authorized_source_data(tmp_path)
    source_database = source_data / "databases" / "grading_system.db"
    database_hash = _sha256(source_database)

    metadata = acceptance.prepare_authorized_inputs(
        workspace,
        source_data_root=source_data,
        session_id=1,
        expected_session_name="0609",
        paper_limit=3,
    )

    assert metadata["state"] == "inputs_prepared"
    assert metadata["authorized_session_id"] == 1
    assert metadata["paper_count"] == 3
    assert _sha256(source_database) == database_hash
    assert not (workspace / "acceptance_inputs" / "databases").exists()
    assert (workspace / "acceptance_inputs" / "source-paper.docx").is_file()
    assert (workspace / "acceptance_inputs" / "template.pdf").is_file()
    assert sorted(
        path.name
        for path in (workspace / "acceptance_inputs" / "answer-sheets").iterdir()
    ) == [
        "answer-01-back.jpg",
        "answer-01-front.jpg",
        "answer-02-back.jpg",
        "answer-02-front.jpg",
        "answer-03-back.jpg",
        "answer-03-front.jpg",
    ]
    roster = (workspace / "acceptance_inputs" / "roster.csv").read_text(
        encoding="utf-8-sig"
    )
    assert roster.splitlines() == [
        "student_code,name,class_name",
        "S001,学生1,七年级一班",
        "S002,学生2,七年级一班",
        "S003,学生3,七年级一班",
    ]
    backup = workspace / "authorized-inputs-backup.zip"
    with zipfile.ZipFile(backup) as archive:
        assert "manifest.json" in archive.namelist()
        assert "roster.csv" in archive.namelist()
        assert not any("database" in name for name in archive.namelist())


def test_prepare_authorized_inputs_fails_closed_on_wrong_exam_or_too_few_papers(
    tmp_path: Path,
) -> None:
    repo, source_sha = _committed_source_repo(tmp_path)
    workspace = tmp_path / "prepared"
    acceptance.prepare_code_workspace(source_sha, workspace, repo_root=repo)
    source_data = _authorized_source_data(tmp_path, paper_count=2)

    with pytest.raises(acceptance.AcceptanceError, match="authorized exam"):
        acceptance.prepare_authorized_inputs(
            workspace,
            source_data_root=source_data,
            session_id=1,
            expected_session_name="not-authorized",
            paper_limit=2,
        )
    with pytest.raises(acceptance.AcceptanceError, match="eligible answer sheets"):
        acceptance.prepare_authorized_inputs(
            workspace,
            source_data_root=source_data,
            session_id=1,
            expected_session_name="0609",
            paper_limit=3,
        )
    assert acceptance.load_metadata(workspace)["state"] == "code_prepared"


def test_prepare_runtime_profile_keeps_only_bounded_config_and_grading_secrets(
    tmp_path: Path,
) -> None:
    repo, source_sha = _committed_source_repo(tmp_path)
    workspace = tmp_path / "prepared"
    acceptance.prepare_code_workspace(source_sha, workspace, repo_root=repo)
    source_data = _authorized_source_data(tmp_path)
    acceptance.prepare_authorized_inputs(
        workspace,
        source_data_root=source_data,
        session_id=1,
        expected_session_name="0609",
        paper_limit=3,
    )
    profile_path = tmp_path / "machine-config" / "api_profiles.json"
    profile_path.parent.mkdir()
    profile_path.write_text(
        json.dumps(
            [
                {"name": "old", "api_key": "old-secret"},
                {
                    "name": "active",
                    "provider": "custom-openai-compatible",
                    "base_url": "https://grading.example.invalid/v1",
                    "api_key": "grading-secret",
                    "grading_model": "grading-model",
                    "ocr_model": "ocr-model",
                    "config_provider": "custom-openai-compatible",
                    "config_base_url": "https://config.example.invalid/v1",
                    "config_api_key": "config-secret",
                    "config_model": "config-model",
                    "objective_enabled": True,
                    "objective_api_key": "objective-secret",
                    "tagging_api_key": "tagging-secret",
                },
            ]
        ),
        encoding="utf-8",
    )
    profile_hash = _sha256(profile_path)

    metadata = acceptance.prepare_runtime_profile(
        workspace,
        source_profile_path=profile_path,
        proxy_base_url="http://127.0.0.1:8120/acceptance-llm/v1",
        max_forwarded_requests=4,
    )

    assert metadata["state"] == "runtime_ready"
    assert metadata["model_request_budget"] == 4
    assert _sha256(profile_path) == profile_hash
    runtime_profiles = json.loads(
        (
            workspace / "acceptance_config" / "api_profiles.json"
        ).read_text(encoding="utf-8")
    )
    assert len(runtime_profiles) == 1
    runtime_profile = runtime_profiles[0]
    assert runtime_profile["name"] == "P2-20 Acceptance"
    assert runtime_profile["base_url"].startswith("http://127.0.0.1:8120/")
    assert runtime_profile["config_base_url"] == runtime_profile["base_url"]
    assert runtime_profile["api_key"] == "acceptance-proxy"
    assert runtime_profile["config_api_key"] == "acceptance-proxy"
    assert runtime_profile["ocr_model"] == "p2-20-local-ocr"
    assert runtime_profile["objective_enabled"] is False
    assert runtime_profile["grading_max_workers"] == 1
    assert runtime_profile["llm_grading_max_retries"] == 0
    assert runtime_profile["llm_config_generation_max_retries"] == 0
    assert runtime_profile["llm_recognition_max_retries"] == 0
    assert not any("tagging" in key for key in runtime_profile)
    assert "objective_api_key" not in runtime_profile
    upstream = json.loads(
        (workspace / "acceptance_config" / "upstream.json").read_text(
            encoding="utf-8"
        )
    )
    assert upstream == {
        "grading": {
            "base_url": "https://grading.example.invalid/v1",
            "api_key": "grading-secret",
            "model": "grading-model",
        },
        "config": {
            "base_url": "https://config.example.invalid/v1",
            "api_key": "config-secret",
            "model": "config-model",
        },
        "max_forwarded_requests": 4,
    }


def test_prepare_runtime_profile_rejects_unsupported_or_unconfigured_provider(
    tmp_path: Path,
) -> None:
    repo, source_sha = _committed_source_repo(tmp_path)
    workspace = tmp_path / "prepared"
    acceptance.prepare_code_workspace(source_sha, workspace, repo_root=repo)
    source_data = _authorized_source_data(tmp_path)
    acceptance.prepare_authorized_inputs(
        workspace,
        source_data_root=source_data,
        session_id=1,
        expected_session_name="0609",
        paper_limit=3,
    )
    profile_path = tmp_path / "profiles.json"
    profile_path.write_text(
        json.dumps(
            [
                {
                    "name": "active",
                    "provider": "unsupported",
                    "base_url": "",
                    "api_key": "",
                    "grading_model": "",
                    "config_model": "",
                }
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(acceptance.AcceptanceError, match="OpenAI-compatible"):
        acceptance.prepare_runtime_profile(
            workspace,
            source_profile_path=profile_path,
            proxy_base_url="http://127.0.0.1:8120/acceptance-llm/v1",
            max_forwarded_requests=4,
        )
    assert acceptance.load_metadata(workspace)["state"] == "inputs_prepared"


def test_model_budget_proxy_stubs_ocr_and_hard_stops_after_four_forwards(
    tmp_path: Path,
) -> None:
    upstream_path = tmp_path / "upstream.json"
    upstream_path.write_text(
        json.dumps(
            {
                "grading": {
                    "base_url": "https://provider.example.invalid/v1",
                    "api_key": "grading-secret",
                    "model": "shared-model",
                },
                "config": {
                    "base_url": "https://provider.example.invalid/v1",
                    "api_key": "config-secret",
                    "model": "shared-model",
                },
                "max_forwarded_requests": 4,
            }
        ),
        encoding="utf-8",
    )
    forwarded: list[dict[str, object]] = []

    async def fake_forward(
        upstream: dict[str, str],
        payload: dict[str, object],
    ) -> tuple[int, dict[str, str], bytes]:
        forwarded.append({"upstream": upstream, "payload": payload})
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(
                {
                    "id": "bounded",
                    "choices": [
                        {"message": {"role": "assistant", "content": "ok"}}
                    ],
                }
            ).encode(),
        )

    app = acceptance.create_model_budget_proxy(
        upstream_path,
        state_path=tmp_path / "budget-state.json",
        forwarder=fake_forward,
    )
    client = TestClient(app)
    headers = {"Authorization": "Bearer acceptance-proxy"}

    ocr = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "p2-20-local-ocr", "messages": []},
    )
    assert ocr.status_code == 200
    assert ocr.json()["choices"][0]["message"]["content"] == "NOT_FOUND"
    assert forwarded == []

    for _ in range(4):
        response = client.post(
            "/v1/chat/completions",
            headers=headers,
            json={"model": "shared-model", "messages": []},
        )
        assert response.status_code == 200
    blocked = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "shared-model", "messages": []},
    )
    assert blocked.status_code == 429
    assert len(forwarded) == 4
    assert all(
        item["upstream"]["api_key"] in {"grading-secret", "config-secret"}
        for item in forwarded
    )
    state = json.loads(
        (tmp_path / "budget-state.json").read_text(encoding="utf-8")
    )
    assert state == {
        "forwarded_requests": 4,
        "local_ocr_requests": 1,
        "max_forwarded_requests": 4,
    }
    assert "secret" not in blocked.text


def test_model_budget_proxy_rejects_wrong_key_and_unknown_model(tmp_path: Path) -> None:
    upstream_path = tmp_path / "upstream.json"
    upstream_path.write_text(
        json.dumps(
            {
                "grading": {
                    "base_url": "https://provider.example.invalid/v1",
                    "api_key": "grading-secret",
                    "model": "grading-model",
                },
                "config": {
                    "base_url": "https://provider.example.invalid/v1",
                    "api_key": "config-secret",
                    "model": "config-model",
                },
                "max_forwarded_requests": 4,
            }
        ),
        encoding="utf-8",
    )
    app = acceptance.create_model_budget_proxy(
        upstream_path,
        state_path=tmp_path / "budget-state.json",
    )
    client = TestClient(app)

    assert (
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong"},
            json={"model": "grading-model", "messages": []},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer acceptance-proxy"},
            json={"model": "unapproved-model", "messages": []},
        ).status_code
        == 403
    )
