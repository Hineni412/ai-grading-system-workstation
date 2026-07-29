"""Fail-fast checks for the machine-local taxonomy state directory.

The taxonomy governance services publish JSON files with a temporary file and
``os.replace``.  Merely reading the current state is therefore not enough to
prove that teacher review decisions can be saved.  This module exercises the
same directory capabilities without changing any existing taxonomy file.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


_PROBE_PREFIX = ".taxonomy-storage-preflight-"


@dataclass(frozen=True)
class TaxonomyStoragePreflightResult:
    """Successful taxonomy storage check."""

    state_path: Path
    existing_files_checked: tuple[Path, ...]


class TaxonomyStoragePreflightError(RuntimeError):
    """A taxonomy state location cannot support the required safe writes."""

    def __init__(
        self,
        *,
        state_path: Path,
        failing_path: Path,
        reason: str,
        cause: BaseException,
    ) -> None:
        self.state_path = Path(state_path)
        self.failing_path = Path(failing_path)
        self.reason = str(reason)
        self.cause = cause
        super().__init__(self.user_message)

    @property
    def user_message(self) -> str:
        action = {
            "parent_create_failed": "cannot create or access the containing folder",
            "probe_create_failed": "cannot create a temporary file in the folder",
            "probe_rename_failed": "cannot safely replace a file in the folder",
            "probe_delete_failed": "cannot remove a temporary file from the folder",
            "existing_file_not_writable": (
                "an existing taxonomy state file cannot be opened for writing"
            ),
        }.get(self.reason, "the taxonomy state location is not writable")
        detail = str(self.cause).strip()
        suffix = f" ({detail})" if detail else ""
        return f"{action}: {self.failing_path}{suffix}"


def _companion_path(state_path: Path, label: str) -> Path:
    suffix = state_path.suffix or ".json"
    return state_path.with_name(f"{state_path.stem}.{label}{suffix}")


def _existing_storage_files(state_path: Path) -> Iterable[Path]:
    """Files that a taxonomy review may update or replace in this directory."""

    yield state_path
    yield state_path.with_name(f"{state_path.name}.lock")
    yield state_path.with_name(f"{state_path.name}.bak")
    yield _companion_path(state_path, "review_receipts")
    yield _companion_path(state_path, "suggestions")


def _raise_preflight_error(
    *,
    state_path: Path,
    failing_path: Path,
    reason: str,
    cause: BaseException,
) -> None:
    raise TaxonomyStoragePreflightError(
        state_path=state_path,
        failing_path=failing_path,
        reason=reason,
        cause=cause,
    ) from cause


def _check_existing_file_writable(state_path: Path, candidate: Path) -> None:
    if not candidate.exists():
        return
    flags = os.O_RDWR
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        descriptor = os.open(candidate, flags)
    except OSError as exc:
        _raise_preflight_error(
            state_path=state_path,
            failing_path=candidate,
            reason="existing_file_not_writable",
            cause=exc,
        )
    else:
        os.close(descriptor)


def _remove_probe(
    state_path: Path,
    probe_path: Path,
    *,
    report_failure: bool,
) -> None:
    try:
        probe_path.unlink(missing_ok=True)
    except OSError as exc:
        if report_failure:
            _raise_preflight_error(
                state_path=state_path,
                failing_path=probe_path,
                reason="probe_delete_failed",
                cause=exc,
            )


def _check_parent_publish_operations(state_path: Path) -> None:
    parent = state_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _raise_preflight_error(
            state_path=state_path,
            failing_path=parent,
            reason="parent_create_failed",
            cause=exc,
        )

    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=_PROBE_PREFIX,
            suffix=".tmp",
            dir=str(parent),
        )
    except OSError as exc:
        _raise_preflight_error(
            state_path=state_path,
            failing_path=parent,
            reason="probe_create_failed",
            cause=exc,
        )

    os.close(descriptor)
    source = Path(temporary_name)
    destination = source.with_name(f"{source.name}.renamed")
    try:
        os.replace(source, destination)
    except OSError as exc:
        _remove_probe(state_path, source, report_failure=False)
        _remove_probe(state_path, destination, report_failure=False)
        _raise_preflight_error(
            state_path=state_path,
            failing_path=parent,
            reason="probe_rename_failed",
            cause=exc,
        )
    _remove_probe(state_path, destination, report_failure=True)


def preflight_taxonomy_storage(
    state_path: str | os.PathLike[str],
) -> TaxonomyStoragePreflightResult:
    """Verify safe-write capabilities without changing existing state files."""

    resolved_state_path = Path(state_path).expanduser().resolve()
    _check_parent_publish_operations(resolved_state_path)
    existing_files: list[Path] = []
    for candidate in _existing_storage_files(resolved_state_path):
        if not candidate.exists():
            continue
        _check_existing_file_writable(resolved_state_path, candidate)
        existing_files.append(candidate)
    return TaxonomyStoragePreflightResult(
        state_path=resolved_state_path,
        existing_files_checked=tuple(existing_files),
    )


def _default_taxonomy_state_path() -> Path:
    from path_manager import PathManager

    return PathManager().taxonomy_state_path


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check that the machine-local taxonomy state can be saved."
    )
    parser.add_argument(
        "--taxonomy-state-path",
        type=Path,
        help="Override used by isolated checks; normal startup uses PathManager.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    state_path = args.taxonomy_state_path or _default_taxonomy_state_path()
    try:
        preflight_taxonomy_storage(state_path)
    except TaxonomyStoragePreflightError as exc:
        print(
            "Taxonomy storage preflight failed; the service was not started.",
            file=sys.stderr,
        )
        print(f"Location: {exc.state_path}", file=sys.stderr)
        print(f"Reason: {exc.user_message}", file=sys.stderr)
        return 5
    return 0


if __name__ == "__main__":
    sys.exit(main())
