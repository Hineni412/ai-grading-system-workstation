from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BACKTICK_REF_RE = re.compile(r"`([^`\n]+)`")
SUPERPOWERS_SKILL_RE = re.compile(
    r"superpowers:[a-z0-9][a-z0-9-]*",
    re.IGNORECASE,
)

AUTHORITY_REFERENCE_PATHS = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "README_工作机使用说明.md",
    "README_私人便携版_v1.5.0.md",
    "frontend/README.md",
    "docs/ui/STYLE.md",
    "docs/superpowers/packages/README.md",
    "docs/superpowers/packages/EXECUTION_INDEX.md",
    "docs/superpowers/packages/NIGHTLY_AUTOMATION.md",
    "docs/superpowers/packages/PARALLEL_WORKTREE_EXECUTION.md",
    "docs/user-testing/README.md",
    "docs/user-testing/USER_TEST_TEMPLATE.md",
)

VERSIONED_REFERENCE_PREFIXES = (
    "docs/",
    "frontend/",
    "tools/",
)

GENERATED_REFERENCE_PREFIXES = (
    "frontend/dist",
)

VERSIONED_ROOT_REFERENCES = {
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "VERSION",
    "运行.bat",
}

RETIRED_DOCUMENT_PATHS = (
    "docs/superpowers/packages/NIGHTLY_ELIGIBILITY_MATRIX.md",
    "docs/superpowers/packages/phase-1-execution-packages.md",
    "docs/superpowers/packages/phase-2-execution-packages.md",
    "docs/superpowers/packages/phase-3-execution-packages.md",
    "docs/user-testing/PHASE2_FRONTEND_RECALIBRATION_TEST_TEMPLATE.md",
    "docs/ui/references/README.md",
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
    candidates = [
        *root.glob("*.md"),
        *(root / "docs").rglob("*.md"),
        root / "frontend" / "README.md",
    ]
    return sorted(
        {
            path
            for path in candidates
            if path.is_file() and ".worktrees" not in path.parts
        }
    )


def _append_text_issue(
    issues: list[DocumentationIssue],
    *,
    root: Path,
    relative_path: str,
    needle: str,
    code: str,
    message: str,
) -> None:
    path = root / relative_path
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
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

    for relative_path in AUTHORITY_REFERENCE_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in BACKTICK_REF_RE.finditer(text):
            raw = match.group(1).strip()
            normalized = raw.replace("\\", "/").split("#", 1)[0]
            normalized = re.sub(r":\d+$", "", normalized)
            if (
                not normalized
                or any(token in normalized for token in ("*", "<", ">", "{", "}"))
                or any(character.isspace() for character in normalized)
                or "://" in normalized
            ):
                continue
            if not (
                normalized.startswith(VERSIONED_REFERENCE_PREFIXES)
                or normalized in VERSIONED_ROOT_REFERENCES
            ):
                continue
            if normalized.startswith(GENERATED_REFERENCE_PREFIXES):
                continue
            candidates = (root / normalized, path.parent / normalized)
            if not any(candidate.resolve().exists() for candidate in candidates):
                issues.append(
                    DocumentationIssue(
                        "DOC002",
                        relative_path,
                        _line_number(text, match.start()),
                        f"Backtick file reference does not exist: {raw}",
                    )
                )

    return sorted(issues)


def check_current_document_facts(
    project_root: Path,
) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    checks = (
        (
            "README_工作机使用说明.md",
            "run.bat",
            "DOC101",
            "Use the current 运行.bat launcher name.",
        ),
        (
            "frontend/README.md",
            "Vue 仍未切入生产",
            "DOC102",
            "Vue is the current production UI.",
        ),
        (
            "frontend/README.md",
            "占位工作区",
            "DOC103",
            "Do not describe implemented workspaces as placeholders.",
        ),
        (
            "ARCHITECTURE.md",
            "增量边界",
            "DOC104",
            "Architecture must describe current facts, not package increments.",
        ),
        (
            "ARCHITECTURE.md",
            "生产 UI 仍未切换",
            "DOC105",
            "Architecture still contains a retired production-UI statement.",
        ),
    )
    for relative_path, needle, code, message in checks:
        _append_text_issue(
            issues,
            root=root,
            relative_path=relative_path,
            needle=needle,
            code=code,
            message=message,
        )
    return sorted(issues)


def check_skill_authority(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []
    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in SUPERPOWERS_SKILL_RE.finditer(text):
            issues.append(
                DocumentationIssue(
                    "DOC201",
                    _relative(root, path),
                    _line_number(text, match.start()),
                    "Superpowers skill invocation is retired; use Matt Pocock skills.",
                )
            )
    return sorted(issues)


def check_p35_governance(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []

    required_fragments = {
        "AGENTS.md": (
            "P3.5",
            "已完成并进入主线",
            "EXECUTION_INDEX.md",
        ),
        "docs/superpowers/packages/EXECUTION_INDEX.md": (
            "P3.5",
            "`completed`",
            "Phase 4",
            "completed_local_integration",
        ),
        "docs/user-testing/README.md": (
            "P3.5",
            "用户",
            "实际",
        ),
    }
    for relative_path, fragments in required_fragments.items():
        path = root / relative_path
        if not path.exists():
            issues.append(
                DocumentationIssue(
                    "DOC301",
                    relative_path,
                    1,
                    "Required P3.5 authority document is missing.",
                )
            )
            continue
        text = path.read_text(encoding="utf-8")
        for fragment in fragments:
            if fragment not in text:
                issues.append(
                    DocumentationIssue(
                        "DOC302",
                        relative_path,
                        1,
                        f"Required P3.5 contract fragment is missing: {fragment}",
                    )
                )

    for relative_path in RETIRED_DOCUMENT_PATHS:
        if (root / relative_path).exists():
            issues.append(
                DocumentationIssue(
                    "DOC303",
                    relative_path,
                    1,
                    "Retired workflow document was restored.",
                )
            )

    for relative_dir, allowed_names in (
        (
            "docs/superpowers/plans",
            {"2026-07-03-frontend-backend-modernization-master-plan.md"},
        ),
        ("docs/superpowers/specs", set()),
        ("docs/user-testing/checkpoints", set()),
    ):
        directory = root / relative_dir
        if not directory.exists():
            continue
        for path in directory.glob("*.md"):
            if path.name not in allowed_names:
                issues.append(
                    DocumentationIssue(
                        "DOC304",
                        _relative(root, path),
                        1,
                        "Historical delivery document was restored.",
                    )
                )

    return sorted(issues)


def run_checks(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    return sorted(
        [
            *check_markdown_links(root),
            *check_current_document_facts(root),
            *check_skill_authority(root),
            *check_p35_governance(root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check current documentation links and P3.5 governance"
    )
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
