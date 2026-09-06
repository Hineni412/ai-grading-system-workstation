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

AUTHORITY_DOCUMENT_PATHS = (
    "README.md",
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "CONTEXT.md",
    "docs/product/GRADING.md",
    "docs/product/KNOWLEDGE_AND_TRAINING.md",
    "docs/product/CLASS_TEACHER.md",
    "docs/security/SECURITY.md",
    "docs/maintenance/storage-policy.md",
    "docs/maintenance/packaging.md",
    "docs/testing/README.md",
    "docs/ui/STYLE.md",
)

RETAINED_NON_AUTHORITY_DOCUMENT_PATHS: tuple[str, ...] = ()

SCANNED_DOCUMENT_ROOTS = (
    "docs",
    "frontend",
    "tools",
    "artifacts",
)

EXCLUDED_DOCUMENT_DIRECTORY_NAMES = {
    ".git",
    ".worktrees",
    ".test-runs",
    "dist",
    "node_modules",
    "output",
    "runtime",
    "user_data",
}

FORBIDDEN_DOCUMENT_DIRECTORIES = {
    "acceptance",
    "adr",
    "architecture",
    "performance",
    "phase4",
    "superpowers",
    "user-testing",
}

CHECKED_REFERENCE_PREFIXES = (
    "artifacts/",
    "backend/",
    "components/",
    "docs/",
    "frontend/",
    "migrations/",
    "runtime/",
    "tests/",
    "tools/",
)

GENERATED_OR_PRIVATE_REFERENCE_PREFIXES = (
    ".test-runs/",
    "frontend/dist/",
    "logs/",
    "output/",
    "runtime/",
    "user_data/",
)

VERSIONED_ROOT_REFERENCES = {
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CLAUDE.md",
    "CONTEXT.md",
    "README.md",
    "VERSION",
    "关闭系统.bat",
    "运行.bat",
}

HISTORICAL_CONTENT_MARKERS = (
    (re.compile(r"\bPhase(?:\s*\d+(?:\.\d+)?)?\b", re.IGNORECASE), "Phase marker"),
    (re.compile(r"\bP3\.5\b", re.IGNORECASE), "P3.5 marker"),
    (re.compile(r"\bPR\s*#?\d+\b", re.IGNORECASE), "numbered PR marker"),
    (
        re.compile(r"\bSHA(?:-\d+)?\s*[:=]?\s*[0-9a-f]{7,40}\b", re.IGNORECASE),
        "commit SHA marker",
    ),
    (re.compile(r"执行记录"), "execution-record marker"),
    (re.compile(r"实施计划"), "implementation-plan marker"),
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


def _is_excluded(root: Path, path: Path) -> bool:
    relative_parts = path.resolve().relative_to(root.resolve()).parts[:-1]
    return any(
        part.lower() in EXCLUDED_DOCUMENT_DIRECTORY_NAMES
        for part in relative_parts
    )


def _markdown_files(root: Path) -> list[Path]:
    candidates: set[Path] = {
        path for path in root.glob("*.md") if path.is_file()
    }
    for relative_root in SCANNED_DOCUMENT_ROOTS:
        directory = root / relative_root
        if not directory.is_dir():
            continue
        candidates.update(
            path
            for path in directory.rglob("*.md")
            if path.is_file() and not _is_excluded(root, path)
        )
    return sorted(candidates)


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


def _markdown_target(raw: str) -> str:
    value = raw.strip()
    if value.startswith("<") and ">" in value:
        return value[1 : value.index(">")]
    return value.split(maxsplit=1)[0] if value else ""


def _normalized_backtick_reference(raw: str) -> str:
    normalized = raw.strip().replace("\\", "/").split("#", 1)[0]
    normalized = re.sub(r":\d+(?::\d+)?$", "", normalized)
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def check_markdown_links(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    issues: list[DocumentationIssue] = []

    for path in _markdown_files(root):
        text = path.read_text(encoding="utf-8")
        for match in MARKDOWN_LINK_RE.finditer(text):
            raw = match.group(1).strip()
            target = _markdown_target(raw).split("#", 1)[0]
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

    for relative_path in AUTHORITY_DOCUMENT_PATHS:
        path = root / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for match in BACKTICK_REF_RE.finditer(text):
            raw = match.group(1).strip()
            normalized = _normalized_backtick_reference(raw)
            if (
                not normalized
                or any(token in normalized for token in ("*", "<", ">", "{", "}"))
                or any(character.isspace() for character in normalized)
                or "://" in normalized
            ):
                continue
            if any(
                normalized == prefix.rstrip("/") or normalized.startswith(prefix)
                for prefix in GENERATED_OR_PRIVATE_REFERENCE_PREFIXES
            ):
                continue
            if not (
                normalized.startswith(CHECKED_REFERENCE_PREFIXES)
                or normalized in VERSIONED_ROOT_REFERENCES
            ):
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
            "README.md",
            "日常使用者不需要安装 Node.js",
            "DOC101",
            "Source checkouts require Node.js/npm; complete portable packages use frontend/dist.",
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
                    "A forbidden skill invocation appears in project documentation.",
                )
            )
    return sorted(issues)


def check_authority_documents(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    return [
        DocumentationIssue(
            "DOC301",
            relative_path,
            1,
            "Required current authority document is missing.",
        )
        for relative_path in AUTHORITY_DOCUMENT_PATHS
        if not (root / relative_path).is_file()
    ]


def check_historical_documents(project_root: Path) -> list[DocumentationIssue]:
    root = Path(project_root).resolve()
    authority_paths = set(AUTHORITY_DOCUMENT_PATHS)
    retained_non_authority_paths = set(RETAINED_NON_AUTHORITY_DOCUMENT_PATHS)
    issues: list[DocumentationIssue] = []

    for path in _markdown_files(root):
        relative_path = _relative(root, path)
        if (
            relative_path in authority_paths
            or relative_path in retained_non_authority_paths
            or relative_path.startswith("docs/requests/")
        ):
            continue
        parts = Path(relative_path).parts
        lower_parts = tuple(part.lower() for part in parts)
        if (
            len(lower_parts) >= 2
            and lower_parts[0] == "docs"
            and lower_parts[1] in FORBIDDEN_DOCUMENT_DIRECTORIES
        ):
            code = "DOC302"
            message = "A forbidden historical documentation directory was restored."
        elif len(parts) == 1 and re.fullmatch(r"README_.+\.md", parts[0], re.IGNORECASE):
            code = "DOC303"
            message = "An obsolete root README variant was restored."
        else:
            code = "DOC305"
            message = "Markdown file is outside the current authority document set."
        issues.append(DocumentationIssue(code, relative_path, 1, message))

    for relative_path in AUTHORITY_DOCUMENT_PATHS:
        path = root / relative_path
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern, label in HISTORICAL_CONTENT_MARKERS:
            match = pattern.search(text)
            if match is None:
                continue
            issues.append(
                DocumentationIssue(
                    "DOC304",
                    relative_path,
                    _line_number(text, match.start()),
                    f"Current authority document contains a historical {label}.",
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
            *check_authority_documents(root),
            *check_historical_documents(root),
        ]
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the current documentation authority set and references"
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
