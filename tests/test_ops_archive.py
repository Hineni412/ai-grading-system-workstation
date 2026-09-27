from __future__ import annotations

import asyncio
import zipfile
from pathlib import Path

import pytest

from backend.ops.archive import (
    OpsArchiveInvalid,
    OpsArchivePolicy,
    OpsArchiveTooLarge,
    extract_validated_zip,
    inspect_zip,
    stage_zip_upload,
)


def _zip_with_member(path: Path, name: str, content: bytes = b"x") -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, content)
    return path


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "/rooted.txt", "C:/secret.txt", "user_data/../escape.txt"],
)
def test_inspect_zip_rejects_path_escape(tmp_path: Path, name: str) -> None:
    archive = _zip_with_member(tmp_path / "bad.zip", name)

    with pytest.raises(OpsArchiveInvalid):
        inspect_zip(
            archive,
            policy=OpsArchivePolicy(),
            allowed_roots={"user_data", "config"},
        )
