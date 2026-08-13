from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path


WORKSPACE_ROOT = Path(r"D:\AI阅卷系统_工作机版_v1.5.0")
PUBLISHED_ROOT = WORKSPACE_ROOT / "user_data"
SWAP_ROOT = Path(
    r"D:\AI阅卷系统_工作机版_v1.5.0.user-data-swap-20260806-6df9a03d"
)
MANIFEST_PATH = SWAP_ROOT / "staging_manifest.json"
STAGE_HELPER = WORKSPACE_ROOT / ".codex_artifacts" / "stage_user_data_20260806.py"


def _load_stage_helper():
    spec = importlib.util.spec_from_file_location("stage_user_data_20260806", STAGE_HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the fixed staging helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    stage = _load_stage_helper()
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    source_files, source_bytes = stage._source_inventory()
    published = PUBLISHED_ROOT.resolve(strict=True)
    if published != PUBLISHED_ROOT or stage._is_reparse(published):
        raise RuntimeError("Published user_data is not a plain directory")

    actual_files = sorted(
        (path.relative_to(published), path)
        for path in published.rglob("*")
        if path.is_file()
    )
    if len(actual_files) != int(manifest["controlled_file_count"]):
        raise RuntimeError("Published file count differs from the manifest")
    if any(stage._is_sidecar(path) for _relative, path in actual_files):
        raise RuntimeError("Published data contains a SQLite sidecar")
    if any(stage._is_reparse(path) for path in published.rglob("*")):
        raise RuntimeError("Published data contains a reparse point")

    expected_relatives = [relative for relative, _source in source_files]
    actual_relatives = [relative for relative, _path in actual_files]
    if actual_relatives != expected_relatives:
        raise RuntimeError("Published relative paths differ from the source")

    path_digest = hashlib.sha256()
    content_digest = hashlib.sha256()
    sqlite_digest = hashlib.sha256()
    published_bytes = 0
    sqlite_count = 0

    for (relative, source), (_actual_relative, destination) in zip(
        source_files, actual_files, strict=True
    ):
        path_digest.update(relative.as_posix().encode("utf-8"))
        path_digest.update(b"\n")
        published_bytes += destination.stat().st_size
        if source.suffix.lower() in stage.SQLITE_SUFFIXES:
            sqlite_count += 1
            source_snapshot = stage._sqlite_snapshot(source)
            destination_snapshot = stage._sqlite_snapshot(destination)
            if source_snapshot != destination_snapshot:
                raise RuntimeError("A published SQLite database differs logically")
            sqlite_digest.update(relative.as_posix().encode("utf-8"))
            sqlite_digest.update(
                str(source_snapshot["logical_sha256"]).encode("ascii")
            )
        else:
            source_hash = stage._hash_file(source)
            if source_hash != stage._hash_file(destination):
                raise RuntimeError("A published ordinary file hash differs")
            content_digest.update(relative.as_posix().encode("utf-8"))
            content_digest.update(source_hash.encode("ascii"))

    expected = {
        "relative_path_sha256": path_digest.hexdigest(),
        "ordinary_content_sha256": content_digest.hexdigest(),
        "sqlite_logical_sha256": sqlite_digest.hexdigest(),
    }
    for key, value in expected.items():
        if value != manifest[key]:
            raise RuntimeError(f"Published aggregate {key} differs from the manifest")
    if source_bytes != int(manifest["controlled_source_bytes"]):
        raise RuntimeError("Source bytes changed after publication")
    if published_bytes != int(manifest["staged_bytes"]):
        raise RuntimeError("Published bytes differ from the staged manifest")
    if sqlite_count != int(manifest["sqlite_file_count"]):
        raise RuntimeError("Published SQLite count differs from the manifest")

    print(
        json.dumps(
            {
                "file_count": len(actual_files),
                "bytes": published_bytes,
                "sqlite_count": sqlite_count,
                "sidecar_count": 0,
                "reparse_count": 0,
                "manifest_match": True,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
