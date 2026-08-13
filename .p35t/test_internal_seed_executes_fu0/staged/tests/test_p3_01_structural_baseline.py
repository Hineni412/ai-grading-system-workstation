"""P3-01 structural baseline public-command contract."""

from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import os
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "build_p3_01_baseline.py"


def _load_tool_module():
    spec = importlib.util.spec_from_file_location("p3_01_baseline_tool", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL_MODULE = _load_tool_module()


def _run(output_dir: Path, *extra_args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(TOOL),
            "--output-dir",
            str(output_dir),
            *extra_args,
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_p3_01_command_emits_reproducible_redacted_structural_baseline(
    tmp_path: Path,
) -> None:
    first_output = tmp_path / "first"
    second_output = tmp_path / "second"

    first = _run(first_output)
    assert first.returncode == 0, first.stderr

    report_path, markdown_path = TOOL_MODULE.resolve_published_report(first_output)
    assert (first_output / "p3-01-structural-baseline" / "manifest.json").is_file()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    rendered = markdown_path.read_text(encoding="utf-8")

    assert payload["package"] == "P3-01"
    assert payload["report_version"] == 1
    assert "DBManager" in payload["core_targets"]["db_manager.py"]["public_classes"]
    assert "db_manager.py" in payload["core_targets"]
    assert "session_manager.py" in payload["core_targets"]
    assert "backend/api/app.py" in payload["core_targets"]
    assert "backend/api/launcher.py" in payload["core_targets"]
    assert "web_app.py" not in payload["core_targets"]
    assert any(
        item["kind"] in {"importlib.import_module", "__import__"}
        for item in payload["dynamic_imports"]
    )
    assert {
        "source": "tools/storage_maintenance.py",
        "kind": "__import__",
        "line": 50,
        "module": "datetime",
    } in payload["dynamic_imports"]
    assert any(
        item["target"] == "db_manager.py" for item in payload["test_coverage_map"]
    )
    assert "main.py" not in {
        item["path"] for item in payload["source_manifest"]
    }
    assert "main.py" not in payload["core_callers"]["db_manager.py"]
    assert set(payload["schema_inputs"]) == {"grading", "question_bank"}
    assert payload["performance_evidence"]["p1_26"]["package"] == "P1-26"
    assert payload["performance_evidence"]["p1_27"]["p1_26_provenance_code_sha"]
    assert "P1-29" in payload["real_process_evidence"]
    assert "P2-20" in payload["real_process_evidence"]
    assert str(PROJECT_ROOT) not in report_path.read_text(encoding="utf-8")
    assert str(PROJECT_ROOT) not in rendered
    assert "user_data/" not in report_path.read_text(encoding="utf-8")

    second = _run(second_output)
    assert second.returncode == 0, second.stderr
    second_report, second_markdown = TOOL_MODULE.resolve_published_report(second_output)
    assert report_path.read_bytes() == second_report.read_bytes()
    assert markdown_path.read_bytes() == second_markdown.read_bytes()


def test_p3_01_command_rejects_missing_controlled_evidence_without_publication(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"

    completed = _run(
        output_dir,
        "--p1-26-report",
        "docs/performance/missing-p1-26.json",
    )

    assert completed.returncode != 0
    assert "missing required input" in completed.stderr
    assert not output_dir.exists()


def test_p3_01_required_input_rejects_parent_traversal(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    outside_report = tmp_path / "outside.json"
    outside_report.write_text("{}", encoding="utf-8")

    with pytest.raises(
        TOOL_MODULE.BaselineInputError,
        match="inside the repository",
    ):
        TOOL_MODULE._require_file(repository_root, "../outside.json")


def test_p3_01_command_rejects_user_data_output_before_scanning(
    tmp_path: Path,
) -> None:
    isolated_root = tmp_path / "repository"
    output_dir = isolated_root / "user_data" / "baseline"

    completed = _run(output_dir, "--root", str(isolated_root))

    assert completed.returncode != 0
    assert "user_data" in completed.stderr
    assert not output_dir.exists()


def test_p3_01_command_does_not_publish_one_report_when_the_pair_is_blocked(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    blocked_publication_path = output_dir / "p3-01-structural-baseline"
    blocked_publication_path.write_text("blocked", encoding="utf-8")

    completed = _run(output_dir)

    assert completed.returncode != 0
    assert blocked_publication_path.read_text(encoding="utf-8") == "blocked"


def test_p3_01_manifest_activation_interruption_keeps_previous_pair_resolvable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output_dir = tmp_path / "output"
    completed = _run(output_dir)
    assert completed.returncode == 0, completed.stderr
    old_json, old_markdown = TOOL_MODULE.resolve_published_report(output_dir)
    old_json_bytes = old_json.read_bytes()
    old_markdown_bytes = old_markdown.read_bytes()
    replacement = json.loads(old_json.read_text(encoding="utf-8"))
    replacement["source_revision"] = "replacement-candidate"

    real_replace = TOOL_MODULE.os.replace

    def interrupt_manifest_activation(source: Path, destination: Path) -> None:
        if Path(destination).name == "manifest.json":
            raise KeyboardInterrupt
        real_replace(source, destination)

    monkeypatch.setattr(TOOL_MODULE.os, "replace", interrupt_manifest_activation)
    with pytest.raises(KeyboardInterrupt):
        TOOL_MODULE.publish_report(output_dir, replacement)

    active_json, active_markdown = TOOL_MODULE.resolve_published_report(output_dir)
    assert active_json.read_bytes() == old_json_bytes
    assert active_markdown.read_bytes() == old_markdown_bytes


def test_p3_01_repeated_publication_is_deterministic_and_ignores_stale_staging(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    first = _run(output_dir)
    assert first.returncode == 0, first.stderr
    publication_dir = output_dir / "p3-01-structural-baseline"
    manifest_path = publication_dir / "manifest.json"
    first_manifest = manifest_path.read_bytes()
    first_json, first_markdown = TOOL_MODULE.resolve_published_report(output_dir)
    stale = publication_dir / "releases" / ".staging-abandoned"
    stale.mkdir()
    (stale / "partial.json").write_text("partial", encoding="utf-8")

    second = _run(output_dir)
    assert second.returncode == 0, second.stderr
    second_json, second_markdown = TOOL_MODULE.resolve_published_report(output_dir)

    assert manifest_path.read_bytes() == first_manifest
    assert second_json == first_json
    assert second_markdown == first_markdown
    assert stale.is_dir()


def test_p3_01_resolver_rejects_manifest_path_escape_and_digest_mismatch(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    completed = _run(output_dir)
    assert completed.returncode == 0, completed.stderr
    publication_dir = output_dir / "p3-01-structural-baseline"
    manifest_path = publication_dir / "manifest.json"
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)
    manifest["active_release"] = "../outside"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TOOL_MODULE.BaselineInputError):
        TOOL_MODULE.resolve_published_report(output_dir)

    manifest_path.write_text(original_manifest, encoding="utf-8")
    active_json, _ = TOOL_MODULE.resolve_published_report(output_dir)
    active_json.write_text("tampered", encoding="utf-8")
    with pytest.raises(TOOL_MODULE.BaselineInputError):
        TOOL_MODULE.resolve_published_report(output_dir)


def test_p3_01_legacy_pair_remains_resolvable_if_new_activation_is_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_output = tmp_path / "source"
    completed = _run(source_output)
    assert completed.returncode == 0, completed.stderr
    source_json, source_markdown = TOOL_MODULE.resolve_published_report(source_output)
    legacy_output = tmp_path / "legacy"
    legacy_output.mkdir()
    legacy_json = legacy_output / "p3-01-structural-baseline.json"
    legacy_markdown = legacy_output / "p3-01-structural-baseline.md"
    legacy_json.write_bytes(source_json.read_bytes())
    legacy_markdown.write_bytes(source_markdown.read_bytes())
    replacement = json.loads(source_json.read_text(encoding="utf-8"))
    replacement["source_revision"] = "replacement-candidate"
    real_replace = TOOL_MODULE.os.replace
    manifest_activations = 0

    def interrupt_second_manifest(source: Path, destination: Path) -> None:
        nonlocal manifest_activations
        if Path(destination).name == "manifest.json":
            manifest_activations += 1
            if manifest_activations == 2:
                raise KeyboardInterrupt
        real_replace(source, destination)

    monkeypatch.setattr(TOOL_MODULE.os, "replace", interrupt_second_manifest)
    with pytest.raises(KeyboardInterrupt):
        TOOL_MODULE.publish_report(legacy_output, replacement)

    active_json, active_markdown = TOOL_MODULE.resolve_published_report(legacy_output)
    assert active_json.read_bytes() == legacy_json.read_bytes()
    assert active_markdown.read_bytes() == legacy_markdown.read_bytes()


def test_p3_01_resolver_rejects_release_directory_not_derived_from_contents(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    completed = _run(output_dir)
    assert completed.returncode == 0, completed.stderr
    publication_dir = output_dir / "p3-01-structural-baseline"
    manifest_path = publication_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    original_release = publication_dir / "releases" / manifest["active_release"]
    fake_release_id = "0" * 64
    fake_release = publication_dir / "releases" / fake_release_id
    fake_release.mkdir()
    for source in original_release.iterdir():
        (fake_release / source.name).write_bytes(source.read_bytes())
    manifest["active_release"] = fake_release_id
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(TOOL_MODULE.BaselineInputError):
        TOOL_MODULE.resolve_published_report(output_dir)


def test_p3_01_command_rejects_nested_publication_reparse_point(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    external_target = tmp_path / "external"
    external_target.mkdir()
    publication_link = output_dir / "p3-01-structural-baseline"
    if os.name == "nt":
        linked = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(publication_link), str(external_target)],
            text=True,
            capture_output=True,
            check=False,
        )
        assert linked.returncode == 0, linked.stderr
    else:
        publication_link.symlink_to(external_target, target_is_directory=True)

    completed = _run(output_dir)

    assert completed.returncode != 0
    assert "reparse" in completed.stderr
    assert list(external_target.iterdir()) == []


def test_p3_01_resolver_accepts_git_checkout_newline_conversion(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "output"
    completed = _run(output_dir)
    assert completed.returncode == 0, completed.stderr
    active_json, active_markdown = TOOL_MODULE.resolve_published_report(output_dir)
    for path in (active_json, active_markdown):
        content = path.read_bytes().replace(b"\r\n", b"\n")
        path.write_bytes(content.replace(b"\n", b"\r\n"))

    converted_json, converted_markdown = TOOL_MODULE.resolve_published_report(output_dir)

    assert converted_json == active_json
    assert converted_markdown == active_markdown


def test_p3_01_legacy_fallback_rejects_json_markdown_mismatch(
    tmp_path: Path,
) -> None:
    source_output = tmp_path / "source"
    completed = _run(source_output)
    assert completed.returncode == 0, completed.stderr
    source_json, source_markdown = TOOL_MODULE.resolve_published_report(source_output)
    legacy_output = tmp_path / "legacy"
    legacy_output.mkdir()
    (legacy_output / "p3-01-structural-baseline.json").write_bytes(source_json.read_bytes())
    (legacy_output / "p3-01-structural-baseline.md").write_text(
        source_markdown.read_text(encoding="utf-8") + "不匹配",
        encoding="utf-8",
    )

    with pytest.raises(TOOL_MODULE.BaselineInputError):
        TOOL_MODULE.resolve_published_report(legacy_output)


def test_p3_01_publication_syncs_staging_directory_before_release_activation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_output = tmp_path / "source"
    completed = _run(source_output)
    assert completed.returncode == 0, completed.stderr
    source_json, _ = TOOL_MODULE.resolve_published_report(source_output)
    report = json.loads(source_json.read_text(encoding="utf-8"))
    output_dir = tmp_path / "output"
    synced_directories: set[Path] = set()
    real_sync = TOOL_MODULE._sync_directory
    real_replace = TOOL_MODULE.os.replace

    def record_sync(path: Path) -> None:
        real_sync(path)
        synced_directories.add(Path(path))

    def require_sync_before_release(source: Path, destination: Path) -> None:
        source_path = Path(source)
        destination_path = Path(destination)
        if source_path.name.startswith(".staging-") and destination_path.parent.name == "releases":
            assert source_path in synced_directories
        real_replace(source, destination)

    monkeypatch.setattr(TOOL_MODULE, "_sync_directory", record_sync)
    monkeypatch.setattr(TOOL_MODULE.os, "replace", require_sync_before_release)

    TOOL_MODULE.publish_report(output_dir, report)
