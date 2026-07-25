from __future__ import annotations

from pathlib import Path

from tools.check_documentation import (
    check_current_document_facts,
    check_markdown_links,
    check_p35_governance,
    check_skill_authority,
    run_checks,
)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def test_stale_frontend_production_claim_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "frontend" / "README.md", "Vue 仍未切入生产。\n")
    issues = check_current_document_facts(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC102", "frontend/README.md")
    ]


def test_superpowers_skill_invocation_is_rejected(tmp_path: Path) -> None:
    _write(tmp_path / "docs" / "guide.md", "调用 superpowers:writing-plans。\n")
    issues = check_skill_authority(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC201", "docs/guide.md")
    ]


def test_p35_authorities_and_retired_documents_are_guarded(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "AGENTS.md", "P3.5\n")
    _write(
        tmp_path / "docs/superpowers/packages/EXECUTION_INDEX.md",
        "Phase 3.5\n",
    )
    _write(tmp_path / "docs/user-testing/README.md", "P3.5\n")
    _write(
        tmp_path
        / "docs/superpowers/packages/phase-1-execution-packages.md",
        "# old\n",
    )

    issues = check_p35_governance(tmp_path)

    assert {item.code for item in issues} == {"DOC302", "DOC303"}


def test_repository_documentation_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    issues = run_checks(project_root)
    assert not issues, "\n".join(
        f"[{item.code}] {item.path}:{item.line} {item.message}" for item in issues
    )
