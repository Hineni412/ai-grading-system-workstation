from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace


REPO_ROOT = Path(__file__).resolve().parents[1]

ACTIVE_MODULES = (
    Path("grading_service.py"),
    Path("manual_review_service.py"),
    Path("original_paper_exporter.py"),
    Path("session_cleanup.py"),
    Path("template_upload_service.py"),
    Path("integration/diagnosis_profile_service.py"),
)

ALLOWED_BACKEND_DB_MANAGER_IMPORTS = {
    Path("backend/repositories/compat.py"),
}


def _imports_db_manager(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and node.module == "db_manager"
        )
        or (
            isinstance(node, ast.Import)
            and any(alias.name == "db_manager" for alias in node.names)
        )
        for node in ast.walk(tree)
    )


def test_active_repository_callers_do_not_import_db_manager() -> None:
    paths = [
        path
        for path in (REPO_ROOT / "backend").rglob("*.py")
        if "__pycache__" not in path.parts
    ]
    paths.extend(REPO_ROOT / path for path in ACTIVE_MODULES)

    offenders = [
        path.relative_to(REPO_ROOT)
        for path in paths
        if _imports_db_manager(path)
        and path.relative_to(REPO_ROOT) not in ALLOWED_BACKEND_DB_MANAGER_IMPORTS
    ]

    assert offenders == []


def test_compatibility_source_is_exposed_as_named_repositories() -> None:
    from backend.repositories.access import as_grading_repositories

    source = SimpleNamespace(
        db_path=Path("grading.db"),
        student_repository=object(),
        session_repository=object(),
        paper_repository=object(),
        result_repository=object(),
        review_repository=object(),
        template_repository=object(),
        settings_repository=object(),
    )

    repositories = as_grading_repositories(source)

    assert repositories.db_path == Path("grading.db")
    assert repositories.students is source.student_repository
    assert repositories.sessions is source.session_repository
    assert repositories.papers is source.paper_repository
    assert repositories.results is source.result_repository
    assert repositories.reviews is source.review_repository
    assert repositories.templates is source.template_repository
    assert repositories.settings is source.settings_repository
    assert as_grading_repositories(repositories) is repositories


def test_production_grading_dependency_returns_repository_access(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from backend.api import dependencies
    from backend.repositories.access import GradingRepositoryAccess

    monkeypatch.setattr(
        dependencies,
        "get_path_manager",
        lambda: SimpleNamespace(db_path=tmp_path / "grading.db"),
    )

    assert isinstance(dependencies.get_grading_db(), GradingRepositoryAccess)


def test_lightweight_compatibility_double_and_instance_override_still_work() -> None:
    from backend.repositories.access import as_grading_repositories

    source = SimpleNamespace(
        get_grading_session=lambda session_id: {"id": session_id},
    )
    repositories = as_grading_repositories(source)

    assert repositories.sessions is source
    assert repositories.get_grading_session(4) == {"id": 4}

    source.get_grading_session = lambda session_id: {"id": session_id, "patched": True}
    assert repositories.get_grading_session(4) == {"id": 4, "patched": True}
