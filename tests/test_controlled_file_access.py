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


def test_controlled_file_accepts_existing_allowlisted_file_inside_root(tmp_path: Path) -> None:
    reports = tmp_path / "data" / "reports"
    report = reports / "成绩.xlsx"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"xlsx")

    resolved = _resolve(report, root=reports, data_root=tmp_path / "data")

    assert resolved.path == report.resolve()
    assert resolved.media_type == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_controlled_file_rejects_existing_absolute_path_outside_root(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileForbidden

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"xlsx")

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve(outside, root=reports, data_root=tmp_path / "data")


def test_controlled_file_rejects_relative_path_traversal(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileForbidden

    data_root = tmp_path / "data"
    reports = data_root / "reports"
    reports.mkdir(parents=True)
    outside = data_root / "outside.xlsx"
    outside.write_bytes(b"xlsx")

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve("../outside.xlsx", root=reports, data_root=data_root)


def test_controlled_file_does_not_basename_remap_missing_absolute_path(
    tmp_path: Path,
) -> None:
    from backend.file_access import ControlledFileForbidden

    data_root = tmp_path / "data"
    reports = data_root / "reports"
    reports.mkdir(parents=True)
    (reports / "collision.xlsx").write_bytes(b"allowed")
    missing_outside = tmp_path / "missing" / "collision.xlsx"

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve(missing_outside, root=reports, data_root=data_root)


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../collision.xlsx",
        "..\\collision.xlsx",
        "Z:\\missing\\collision.xlsx",
        "\\\\server\\share\\collision.xlsx",
    ],
)
def test_controlled_file_does_not_basename_remap_unsafe_windows_or_traversal_path(
    tmp_path: Path,
    unsafe_path: str,
) -> None:
    from backend.file_access import ControlledFileForbidden

    data_root = tmp_path / "data"
    reports = data_root / "reports"
    reports.mkdir(parents=True)
    (reports / "collision.xlsx").write_bytes(b"allowed")

    with pytest.raises(ControlledFileForbidden, match="outside the allowed root"):
        _resolve(unsafe_path, root=reports, data_root=data_root)


def test_controlled_file_remaps_full_legacy_user_data_relative_path(
    tmp_path: Path,
) -> None:
    data_root = tmp_path / "data"
    reports = data_root / "reports"
    report = reports / "class-a" / "report.xlsx"
    report.parent.mkdir(parents=True)
    report.write_bytes(b"xlsx")

    resolved = _resolve(
        "Z:\\old-machine\\user_data\\reports\\class-a\\report.xlsx",
        root=reports,
        data_root=data_root,
    )

    assert resolved.path == report.resolve()


def test_controlled_file_rejects_disallowed_extension_inside_root(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileTypeError

    reports = tmp_path / "data" / "reports"
    executable = reports / "report.exe"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"not safe")

    with pytest.raises(ControlledFileTypeError, match="type is not allowed"):
        _resolve(executable, root=reports, data_root=tmp_path / "data")


def test_controlled_file_marks_missing_file_inside_root_as_expired(tmp_path: Path) -> None:
    from backend.file_access import ControlledFileExpired

    reports = tmp_path / "data" / "reports"
    reports.mkdir(parents=True)

    with pytest.raises(ControlledFileExpired, match="no longer available"):
        _resolve(reports / "missing.xlsx", root=reports, data_root=tmp_path / "data")


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
