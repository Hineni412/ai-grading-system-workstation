from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (PROJECT_ROOT / relative_path).read_text(encoding="utf-8")


def test_project_rules_define_three_package_milestones_and_one_full_gate() -> None:
    agents = _read("AGENTS.md")
    execution = _read("docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md")

    assert "每批固定 3 个相关执行包" in agents
    assert "每批固定 3 个相关执行包" in execution
    assert "批次末只运行一次完整门槛" in execution
    assert "高风险包可以单独成批" in execution
    assert "第 3 包" in execution and "前 2 包" in execution


def test_handoff_and_nightly_rules_allow_only_a_declared_milestone_base() -> None:
    package_readme = _read("docs/superpowers/packages/README.md")
    nightly = _read("docs/superpowers/packages/NIGHTLY_AUTOMATION.md")

    assert "**交接基线：** <完整 Git 提交 SHA>" in package_readme
    assert "未声明时仍以 `origin/main` 为基线" in package_readme
    assert "已在 Index 声明的活动里程碑" in nightly
    assert "交接基线" in nightly
    assert "npm run verify" in nightly
    assert "lint、typecheck、unit 和 build" not in nightly


def test_current_index_declares_first_three_package_milestone() -> None:
    index = _read("docs/superpowers/packages/EXECUTION_INDEX.md")

    assert "M2-01" in index
    assert "P2-10 → P2-11 → P2-12" in index
    assert "第 1 包" in index


def test_parallel_pilot_and_test_consolidation_deferral_are_explicit() -> None:
    execution = _read("docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md")

    assert "第一个里程碑" in execution and "同一 SHA" in execution
    assert "第二个里程碑" in execution and "2 个进程" in execution
    assert "P3-01" in execution and "合并或删除测试" in execution
