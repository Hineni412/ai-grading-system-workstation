from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
PHASE_PACKAGE_RE = re.compile(r"^###\s+(P[1-5]-\d{2})\s+(.+?)\s*$")
MODEL_RE = re.compile(r"^- \*\*模型：\*\*\s+`([^`]+)`")
MATRIX_ROW_RE = re.compile(
    r"^\|\s*(P[1-5]-\d{2})\s*\|\s*(.*?)\s*\|\s*`(T-M|T-H|S-H|S-XH)`\s*\|\s*`?"
    r"(eligible_after_plan|daytime_only|completed_not_applicable)`?\s*\|"
)

PHASE_PATHS = tuple(
    f"docs/superpowers/packages/phase-{phase}-execution-packages.md"
    for phase in range(1, 6)
)
FORMAL_IDS = frozenset(
    [*(f"P1-{value:02d}" for value in range(9, 30))]
    + [*(f"P2-{value:02d}" for value in range(1, 23))]
    + [*(f"P3-{value:02d}" for value in range(1, 20))]
    + [*(f"P4-{value:02d}" for value in range(1, 13))]
    + [*(f"P5-{value:02d}" for value in range(1, 14))]
)

GOVERNANCE_PLANNING_PATHS = frozenset(
    {
        "docs/superpowers/specs/2026-07-11-document-governance-design.md",
        "docs/superpowers/plans/2026-07-11-document-governance-implementation.md",
    }
)
REMOVED_REFERENCE_NAMES = frozenset(
    {
        "PLAN_AUDIT_2026-07-10.md",
        "code-ownership-map.md",
        "knowledge-practice-operations.md",
        "2026-07-03-wp0-2-repo-hygiene-implementation.md",
        "2026-07-03-wp0-3-dependency-lock-implementation.md",
        "2026-07-03-wp0-4-schema-baseline-implementation.md",
        "2026-07-03-wp0-5-smoke-script-implementation.md",
        "2026-07-08-wp1-1-fastapi-skeleton-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-a-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-b-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-c-implementation.md",
        "2026-07-09-wp1-2-api-routes-batch-d-implementation.md",
        "2026-07-09-wp1-2-batch-e-grading-run-job-implementation.md",
        "2026-07-09-wp1-2-batch-e-report-export-job-implementation.md",
        "2026-07-09-wp1-2-batch-e-review-routes-implementation.md",
        "2026-07-09-wp1-2-batch-e-scan-analysis-job-implementation.md",
        "2026-07-09-wp1-3-job-manager-minimal-implementation.md",
        "2026-07-10-p1-09-windows-path-and-skip-cleanup-implementation.md",
        "2026-07-10-p1-10-job-manager-schema-lifecycle-implementation.md",
        "2026-07-10-p1-11-cooperative-job-cancellation-implementation.md",
        "2026-07-10-p1-12-review-service-atomic-write-implementation.md",
        "2026-07-10-p1-13-controlled-media-download-implementation.md",
        "2026-07-10-p1-14-phase-1-stabilization-checkpoint-implementation.md",
        "2026-07-10-p1-15-question-bank-read-routes-implementation.md",
        "2026-06-30-fine-grained-knowledge-graph-design.md",
        "2026-06-28-grading-paper-skill-workflow-design.md",
        "2026-06-13-answer-region-calibration-redesign.md",
        "2026-06-13-knowledge-graph-question-bank-practice-design.md",
        "2026-06-19-grading-completeness-and-shared-regions-design.md",
        "2026-06-19-simplified-knowledge-alignment-training-recommendation-design.md",
        "2026-06-21-question-bank-source-paper-archive-design.md",
        "2026-06-22-unified-skill-catalog-design.md",
        "2026-06-28-question-tags-knowledge-graph-design.md",
        "2026-06-29-import-dialog-direct-tag-save-design.md",
        "2026-06-30-question-bank-tagging-config-readonly-design.md",
        "2026-07-01-unified-question-ids-resumable-grading-tag-retry-design.md",
        "2026-07-10-roadmap-execution-packages-design.md",
        "2026-07-11-nightly-eligibility-matrix-design.md",
        "2026-07-11-nightly-single-package-automation-design.md",
        "2026-07-11-nightly-automation-implementation.md",
        "2026-07-11-nightly-eligibility-matrix-implementation.md",
        "2026-07-11-day-night-handoff-user-acceptance-design.md",
        "2026-07-11-day-night-handoff-protocol-implementation.md",
        "2026-07-11-user-acceptance-runtime-foundation-implementation.md",
    }
)


@dataclass(frozen=True, order=True)
class DocumentationIssue:
    code: str
    path: str
    line: int
    message: str


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _markdown_files(root: Path) -> list[Path]:
    paths = [*root.glob("*.md"), *(root / "docs").rglob("*.md")]
    return sorted(path for path in paths if ".worktrees" not in path.parts)


def check_markdown_links(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw = match.group(1).strip().strip("<>")
            target = raw.split("#", 1)[0]
            if not target or "://" in target or target.startswith("#"):
                continue
            if not (path.parent / target).resolve().exists():
                issues.append(
                    DocumentationIssue(
                        "DOC001",
                        _relative(root, path),
                        _line_number(text, match.start()),
                        f"Markdown target does not exist: {raw}",
                    )
                )
    return sorted(issues)


def _text_issues(
    root: Path,
    relative_path: str,
    checks: tuple[tuple[str, str, str], ...],
) -> list[DocumentationIssue]:
    path = root / relative_path
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    issues: list[DocumentationIssue] = []
    for code, needle, message in checks:
        offset = text.find(needle)
        if offset >= 0:
            issues.append(
                DocumentationIssue(
                    code,
                    relative_path,
                    _line_number(text, offset),
                    message,
                )
            )
    return issues


def check_user_documents(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues = _text_issues(
        root,
        "README_工作机使用说明.md",
        (
            ("DOC101", "run.bat", "Use the portable 运行.bat launcher."),
            (
                "DOC103",
                "set-mode --mode skill",
                "Remove the retired skill-mode operation.",
            ),
        ),
    )
    issues.extend(
        _text_issues(
            root,
            "README_私人便携版_v1.5.0.md",
            (
                (
                    "DOC102",
                    "相近补入",
                    "Remove the retired approximate-skill recommendation.",
                ),
            ),
        )
    )
    for relative_path in (
        "docs/user-testing/README.md",
        "docs/user-testing/USER_TEST_TEMPLATE.md",
    ):
        issues.extend(
            _text_issues(
                root,
                relative_path,
                (
                    (
                        "DOC105",
                        "用户验收模式",
                        "Do not claim an unimplemented dedicated UAT runtime or banner.",
                    ),
                ),
            )
        )
    guide = root / "README_工作机使用说明.md"
    if guide.exists() and "运行.bat" in guide.read_text(encoding="utf-8"):
        launcher = root / "运行.bat"
        if not launcher.exists():
            issues.append(
                DocumentationIssue(
                    "DOC104",
                    "README_工作机使用说明.md",
                    1,
                    "Referenced portable launcher is missing: 运行.bat",
                )
            )
    return sorted(issues)


def check_status_ownership(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for relative_path in PHASE_PATHS:
        issues.extend(
            _text_issues(
                root,
                relative_path,
                (
                    (
                        "DOC201",
                        "**状态/依赖：**",
                        "Phase maps must store dependencies, not dynamic status.",
                    ),
                ),
            )
        )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
            (
                (
                    "DOC202",
                    "| 状态 |",
                    "Eligibility matrix must not copy package status.",
                ),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md",
            (
                (
                    "DOC203",
                    "当前 Git 与 Worktree 快照",
                    "Persistent workflow must not contain a live snapshot.",
                ),
                (
                    "DOC204",
                    ".worktrees/p1-16-question-bank-write",
                    "Persistent workflow must not hard-code active worktrees.",
                ),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "AGENTS.md",
            (
                (
                    "DOC205",
                    "## 当前进度快照",
                    "AGENTS must not copy volatile project status.",
                ),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "ARCHITECTURE.md",
            (
                (
                    "DOC206",
                    "尚未合并到 `main`",
                    "Architecture must not describe branch merge status.",
                ),
                (
                    "DOC207",
                    "## 13. 更新记录",
                    "Move implementation history out of Architecture.",
                ),
            ),
        )
    )
    issues.extend(
        _text_issues(
            root,
            "docs/superpowers/plans/2026-07-03-frontend-backend-modernization-master-plan.md",
            (
                (
                    "DOC208",
                    "### 0.4 当前进度",
                    "Master plan must not copy current progress.",
                ),
            ),
        )
    )
    return sorted(issues)


def check_removed_references(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for path in _markdown_files(root):
        relative = _relative(root, path)
        if relative in GOVERNANCE_PLANNING_PATHS:
            continue
        text = path.read_text(encoding="utf-8")
        for name in sorted(REMOVED_REFERENCE_NAMES):
            offset = text.find(name)
            if offset >= 0:
                issues.append(
                    DocumentationIssue(
                        "DOC401",
                        relative,
                        _line_number(text, offset),
                        f"Reference to removed document: {name}",
                    )
                )
    return sorted(issues)


def _phase_packages(root: Path) -> dict[str, tuple[str, str, str, int]]:
    packages: dict[str, tuple[str, str, str, int]] = {}
    for relative_path in PHASE_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        current: tuple[str, str, int] | None = None
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            heading = PHASE_PACKAGE_RE.match(line)
            if heading:
                current = (heading.group(1), heading.group(2).strip(), line_number)
                continue
            model = MODEL_RE.match(line)
            if current and model:
                parts = [part.strip() for part in model.group(1).split("/")]
                if len(parts) == 3:
                    package_id, title, heading_line = current
                    packages[package_id] = (
                        title,
                        parts[1],
                        relative_path,
                        heading_line,
                    )
                current = None
    return packages


def _matrix_packages(root: Path) -> dict[str, tuple[str, str, str, int]]:
    relative_path = "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md"
    path = root / relative_path
    packages: dict[str, tuple[str, str, str, int]] = {}
    if not path.exists():
        return packages
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        match = MATRIX_ROW_RE.match(line)
        if match:
            packages[match.group(1)] = (
                match.group(2).strip(),
                match.group(3),
                match.group(4),
                line_number,
            )
    return packages


def check_package_registry(
    project_root: Path,
    *,
    expected_ids: set[str] | frozenset[str] = FORMAL_IDS,
) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    phase = _phase_packages(root)
    matrix = _matrix_packages(root)
    issues: list[DocumentationIssue] = []
    for package_id in sorted(set(expected_ids) - set(phase)):
        issues.append(
            DocumentationIssue(
                "DOC303",
                "docs/superpowers/packages",
                1,
                f"Phase map missing {package_id}",
            )
        )
    for package_id in sorted(set(expected_ids) - set(matrix)):
        issues.append(
            DocumentationIssue(
                "DOC304",
                "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
                1,
                f"Matrix missing {package_id}",
            )
        )
    for package_id in sorted(set(expected_ids) & set(phase) & set(matrix)):
        phase_title, phase_model, phase_path, phase_line = phase[package_id]
        matrix_title, matrix_model, _, matrix_line = matrix[package_id]
        if phase_title != matrix_title:
            issues.append(
                DocumentationIssue(
                    "DOC301",
                    phase_path,
                    phase_line,
                    f"Title mismatch for {package_id}",
                )
            )
        if phase_model != matrix_model:
            issues.append(
                DocumentationIssue(
                    "DOC302",
                    "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
                    matrix_line,
                    f"Execution model mismatch for {package_id}",
                )
            )
    return sorted(issues)


def run_checks(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    return sorted(
        [
            *check_markdown_links(root),
            *check_user_documents(root),
            *check_status_ownership(root),
            *check_package_registry(root),
            *check_removed_references(root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check project documentation governance")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args(argv)
    issues = run_checks(args.root)
    for issue in issues:
        print(f"[{issue.code}] {issue.path}:{issue.line} {issue.message}")
    if issues:
        print(f"Documentation check failed: {len(issues)} issue(s).")
        return 1
    print("Documentation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
