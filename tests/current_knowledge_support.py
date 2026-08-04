from __future__ import annotations

from pathlib import Path

from question_bank.knowledge_graph_release import (
    bootstrap_release,
    load_active_release,
    load_release,
)


def install_current_knowledge(db_path: Path) -> str:
    """Install the checked-in standard into a temporary test database."""

    release = load_release()
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
