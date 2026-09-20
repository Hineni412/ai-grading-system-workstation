from __future__ import annotations

from pathlib import Path

import pytest

from tools.check_documentation import (
    AUTHORITY_DOCUMENT_PATHS,
    check_authority_documents,
    check_current_document_facts,
    check_historical_documents,
    check_markdown_links,
    check_skill_authority,
    run_checks,
)


def _write(path: Path, text: str = "# 当前说明\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_authority_set(root: Path) -> None:
    for relative_path in AUTHORITY_DOCUMENT_PATHS:
        _write(root / relative_path)


def test_missing_relative_markdown_link_has_stable_location(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "guide.md", "[missing](other.md)\n")
    issues = check_markdown_links(tmp_path)
    assert [(item.code, item.path, item.line) for item in issues] == [
        ("DOC001", "docs/guide.md", 1)
    ]


def test_missing_backtick_authority_reference_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "读取 `docs/missing.md`。\n")
    issues = check_markdown_links(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC002", "AGENTS.md")
    ]


def test_all_missing_authority_documents_are_reported(tmp_path: Path) -> None:
    issues = check_authority_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC301", relative_path)
        for relative_path in AUTHORITY_DOCUMENT_PATHS
    ]


def test_forbidden_document_directory_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "adr" / "0001-old.md")
    issues = check_historical_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC302", "docs/adr/0001-old.md")
    ]


def test_obsolete_root_readme_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "README_旧说明.md")
    issues = check_historical_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC303", "README_旧说明.md")
    ]


def test_non_authority_markdown_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "tools" / "testing" / "AB_BROWSER_ACCEPTANCE.md")
    issues = check_historical_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC305", "tools/testing/AB_BROWSER_ACCEPTANCE.md")
    ]


def test_user_requested_plan_is_allowed_outside_current_facts(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "docs/requests/next-steps.md", "# 用户要求的后续方案\n\nPhase 2\n")

    assert not check_historical_documents(tmp_path)


def test_unrequested_historical_directory_is_still_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "docs/requests/next-steps.md")
    _write(tmp_path / "docs" / "architecture" / "another-research.md")

    issues = check_historical_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC302", "docs/architecture/another-research.md")
    ]


def test_user_requested_documents_still_receive_link_and_skill_checks(
    tmp_path: Path,
) -> None:
    link_path = "docs/requests/plan.md"
    skill_path = "docs/requests/investigation.md"
    _write(tmp_path / link_path, "[missing](missing.md)\n")
    _write(tmp_path / skill_path, "调用 superpowers:writing-plans。\n")

    link_issues = check_markdown_links(tmp_path)
    skill_issues = check_skill_authority(tmp_path)

    assert [(item.code, item.path) for item in link_issues] == [
        ("DOC001", link_path)
    ]
    assert [(item.code, item.path) for item in skill_issues] == [
        ("DOC201", skill_path)
    ]


@pytest.mark.parametrize(
    "marker",
    (
        "Phase 2",
        "P3.5",
        "PR #17",
        "SHA: deadbeef",
        "执行记录",
        "实施计划",
    ),
)
def test_historical_marker_in_authority_document_is_rejected(
    tmp_path: Path,
    marker: str,
) -> None:
    _write(tmp_path / "README.md", f"# 当前说明\n\n{marker}\n")
    issues = check_historical_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC304", "README.md")
    ]


def test_unqualified_frontend_runtime_claim_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "日常使用者不需要安装 Node.js。\n",
    )
    issues = check_current_document_facts(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC101", "README.md")
    ]


def test_superpowers_skill_invocation_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "guide.md", "调用 superpowers:writing-plans。\n")
    issues = check_skill_authority(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC201", "docs/guide.md")
    ]


def test_clean_authority_set_passes_governance(tmp_path: Path) -> None:
    _write_authority_set(tmp_path)
    assert not run_checks(tmp_path)


def test_repository_documentation_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    issues = run_checks(project_root)
    assert not issues, "\n".join(
        f"[{item.code}] {item.path}:{item.line} {item.message}" for item in issues
    )
