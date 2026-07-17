from __future__ import annotations

from pathlib import Path

from tools import check_documentation as documentation
from tools.check_documentation import (
    check_acceptance_status_consistency,
    check_markdown_links,
    check_package_registry,
    check_removed_references,
    check_status_ownership,
    check_user_documents,
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
    _write(tmp_path / "AGENTS.md", "读取 `docs/missing.md` 和 `tools/missing.py`。\n")

    issues = check_markdown_links(tmp_path)

    assert [(item.code, item.path, item.line) for item in issues] == [
        ("DOC002", "AGENTS.md", 1),
        ("DOC002", "AGENTS.md", 1),
    ]


def test_user_documents_reject_obsolete_runtime_and_skill_instructions(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "运行 run.bat\n")
    _write(tmp_path / "README_私人便携版_v1.5.0.md", "允许相近补入\n")
    issues = check_user_documents(tmp_path)
    assert {item.code for item in issues} == {"DOC101", "DOC102"}


def test_active_documents_reject_superpowers_skill_invocations(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs/current.md",
        "使用 `superpowers:writing-plans` 生成计划。\n",
    )

    issues = documentation.check_skill_authority(tmp_path)

    assert [(item.code, item.path, item.line) for item in issues] == [
        ("DOC403", "docs/current.md", 1)
    ]


def test_user_testing_docs_reject_unimplemented_runtime_banner_claim(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path / "docs" / "user-testing" / "README.md",
        "页面必须持续显示用户验收模式。\n",
    )
    issues = check_user_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC105", "docs/user-testing/README.md")
    ]


def test_acceptance_visible_status_must_match_machine_result(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/user-testing/checkpoints/P2-99-example.md",
        "**状态：** pending\n"
        "<!-- USER_ACCEPTANCE_RESULT_START -->\n"
        "**结果：** passed\n"
        "<!-- USER_ACCEPTANCE_RESULT_END -->\n",
    )

    issues = check_acceptance_status_consistency(tmp_path)

    assert [(item.code, item.path, item.line) for item in issues] == [
        ("DOC106", "docs/user-testing/checkpoints/P2-99-example.md", 1)
    ]


def test_user_guide_requires_existing_portable_launcher(tmp_path: Path) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "双击 `运行.bat`。\n")
    issues = check_user_documents(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC104", "README_工作机使用说明.md")
    ]


def test_phase_and_matrix_must_not_copy_dynamic_status(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/superpowers/packages/phase-1-execution-packages.md",
        "### P1-09 示例\n- **状态/依赖：** `planned`；无。\n",
    )
    _write(
        tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
        "| 包 | 标题 | 状态 | 执行模型 | 夜间资格 |\n",
    )
    issues = check_status_ownership(tmp_path)
    assert {item.code for item in issues} == {"DOC201", "DOC202"}


def test_entry_documents_reject_volatile_snapshots(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "## 当前进度快照（2026-07-11）\n")
    _write(tmp_path / "ARCHITECTURE.md", "## 13. 更新记录\n")
    _write(
        tmp_path
        / "docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md",
        "### 0.4 当前进度（2026-07-11）\n",
    )
    issues = check_status_ownership(tmp_path)
    assert {item.code for item in issues} == {"DOC205", "DOC207", "DOC208"}


def test_entry_documents_reject_live_package_status_actions(tmp_path: Path) -> None:
    _write(tmp_path / "AGENTS.md", "当前 P2-09 已合并。\n")
    _write(tmp_path / "ARCHITECTURE.md", "阻断 P2-14。\n")

    issues = check_status_ownership(tmp_path)

    assert {item.code for item in issues} == {"DOC211", "DOC212"}


def test_persistent_docs_reject_live_worktree_assignment_claims(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
        "并行手册已分配专属 worktree。\n",
    )
    _write(
        tmp_path / "docs/superpowers/packages/NIGHTLY_AUTOMATION.md",
        "在并行手册中分配专属 worktree。\n",
    )

    issues = check_status_ownership(tmp_path)

    assert {item.code for item in issues} == {"DOC209", "DOC210"}


def test_removed_document_name_is_rejected_outside_governance_plan(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "docs/current.md", "读取 `PLAN_AUDIT_2026-07-10.md`。\n")
    issues = check_removed_references(tmp_path)
    assert [(item.code, item.path) for item in issues] == [
        ("DOC401", "docs/current.md")
    ]


def test_retired_completed_plan_cannot_be_restored(tmp_path: Path) -> None:
    retired = (
        tmp_path
        / "docs/superpowers/plans"
        / "2026-07-15-p2-14-workbench-analysis-overview-implementation.md"
    )
    _write(retired, "# Restored historical plan\n")

    issues = check_removed_references(tmp_path)

    assert [(item.code, item.path) for item in issues] == [
        (
            "DOC402",
            "docs/superpowers/plans/"
            "2026-07-15-p2-14-workbench-analysis-overview-implementation.md",
        )
    ]


def test_package_registry_compares_title_and_formal_ids_without_model_metadata(
    tmp_path: Path,
) -> None:
    phase = tmp_path / "docs/superpowers/packages/phase-1-execution-packages.md"
    matrix = tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    _write(phase, "### P1-09 示例包\n- **目标：** 示例目标。\n")
    _write(
        matrix,
        "| 包 | 标题 | 夜间资格 | 理由 |\n"
        "|---|---|---|---|\n"
        "| P1-09 | 错误标题 | `eligible_after_plan` | test |\n",
    )
    issues = check_package_registry(tmp_path, expected_ids={"P1-09"})
    assert {item.code for item in issues} == {"DOC301"}


def test_package_registry_rejects_duplicate_and_unexpected_ids(
    tmp_path: Path,
) -> None:
    phase = tmp_path / "docs/superpowers/packages/phase-1-execution-packages.md"
    matrix = tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    _write(
        phase,
        "### P1-09 示例包\n- **目标：** 示例目标。\n"
        "### P1-09 重复包\n- **目标：** 重复目标。\n"
        "### P6-01 额外包\n- **目标：** 额外目标。\n",
    )
    _write(
        matrix,
        "| P1-09 | 示例包 | `eligible_after_plan` | test |\n"
        "| P1-09 | 重复包 | `eligible_after_plan` | test |\n"
        "| P6-01 | 额外包 | `eligible_after_plan` | test |\n",
    )

    issues = check_package_registry(tmp_path, expected_ids={"P1-09"})

    assert {item.code for item in issues} == {"DOC305", "DOC306"}


def test_repository_registry_requires_all_historical_matrix_ids(
    tmp_path: Path,
) -> None:
    matrix = tmp_path / "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    _write(
        matrix,
        "".join(
            f"| P1-{value:02d} | 历史包 | `completed_not_applicable` | done |\n"
            for value in range(1, 8)
        ),
    )

    issues = check_package_registry(tmp_path)

    assert any(
        item.code == "DOC307" and "P1-08" in item.message for item in issues
    )


def test_run_checks_is_read_only(tmp_path: Path) -> None:
    _write(tmp_path / "README_工作机使用说明.md", "双击 `运行.bat`。\n")
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    run_checks(tmp_path)
    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_repository_documentation_contract() -> None:
    project_root = Path(__file__).resolve().parents[1]
    issues = run_checks(project_root)
    assert not issues, "\n".join(
        f"[{item.code}] {item.path}:{item.line} {item.message}" for item in issues
    )
