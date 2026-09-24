from __future__ import annotations

from typing import Any

from backend.grading_workflow import preflight_match_status
from backend.scan_grading.workspace import (
    ScanGradingWorkspace,
    ScanGradingWorkspaceError,
)


def current_manual_context(
    session_id: int,
    workspace: ScanGradingWorkspace,
) -> dict[str, Any] | None:
    """Return the frozen scan batch context shared by all review surfaces.

    Every review-style read (review queue, workbench overview) must use the
    same context so teacher score locks and batch scoping are honored
    consistently.
    """
    try:
        # Preflight already checks that the current batch is frozen and that
        # its analysis belongs to that batch. Avoid loading grading jobs/runs.
        preflight = workspace.get_preflight(session_id)
    except ScanGradingWorkspaceError:
        return None
    return {
        "scan_batch_id": str(preflight["scan_batch_id"]),
        "papers": preflight_match_status(preflight)["papers"],
    }
