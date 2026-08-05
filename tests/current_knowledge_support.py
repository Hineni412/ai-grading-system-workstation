from __future__ import annotations

from pathlib import Path

from question_bank.knowledge_graph_release import (
    bootstrap_release,
    load_active_release,
    load_release_for_taxonomy_revision,
)


def install_current_knowledge(
    db_path: Path,
    *,
    taxonomy_revision: int = 3,
) -> str:
    """Install a pinned standard into a temporary historical test fixture.

    Existing suites were authored against revision 3's 294-term vocabulary.
    New-standard behavior is covered by the dedicated revision 4 tests rather
    than silently changing the meaning of those historical fixtures.
    """

    release = load_release_for_taxonomy_revision(taxonomy_revision)
    active = load_active_release(Path(db_path))
    if active is not None:
        assert active.release_id == release.release_id
        assert active.content_hash == release.content_hash
        return release.release_id
    return bootstrap_release(
        Path(db_path),
        release,
        actor_ref="test-suite",
        source_reference="checked-in-test-release",
        reason="install current knowledge for isolated test",
    )


__all__ = ["install_current_knowledge"]
