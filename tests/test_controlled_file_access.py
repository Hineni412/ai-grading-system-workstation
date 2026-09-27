from __future__ import annotations

from pathlib import Path

import pytest


def _resolve(path_value: object, *, root: Path, data_root: Path):
    from backend.file_access import resolve_controlled_file

    return resolve_controlled_file(
        path_value,
        root=root,
        data_root=data_root,
        allowed_suffixes={".xlsx"},
    )


def test_controlled_file_rejects_relative_path_traversal(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileForbidden

    data_root = tmp_path / "data"
    reports = data_root / "reports"
    reports.mkdir(parents=True)
    outside = data_root / "outside.xlsx"
    outside.write_bytes(b"xlsx")

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve("../outside.xlsx", root=reports, data_root=data_root)


def test_controlled_file_rejects_resolved_escape_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.file_access import ControlledFileForbidden

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"xlsx")
    link = reports / "linked.xlsx"
    link.write_bytes(b"placeholder")
    original_resolve = Path.resolve
    outside_resolved = original_resolve(outside, strict=False)

    def resolve_with_escape(path: Path, *args, **kwargs) -> Path:
        if path == link:
            return outside_resolved
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_with_escape)

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve(link, root=reports, data_root=tmp_path / "data")
