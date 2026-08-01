from __future__ import annotations

import pytest

from tools import teacher_platform_preview as preview


def test_check_preview_rejects_a_build_from_an_older_preview_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_heads = {
        branch: f"head-{index}"
        for index, branch in enumerate(preview.SOURCE_BRANCHES)
    }
    monkeypatch.setattr(
        preview,
        "_assert_preview_workspace",
        lambda: ("current-preview-head", source_heads),
    )
    monkeypatch.setattr(preview, "_assert_workspace_labels", lambda: None)
    monkeypatch.setattr(
        preview,
        "_read_stamp",
        lambda: {
            "preview_head": "older-preview-head",
            "source_checkpoints": source_heads,
        },
    )

    with pytest.raises(preview.PreviewGuardError, match="页面仍是旧构建"):
        preview.check_preview()


def test_workspace_label_check_requires_both_new_workspaces(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    bundle = assets / "index.js"
    bundle.write_text("备课工作台", encoding="utf-8")
    monkeypatch.setattr(preview, "DIST_DIR", dist)

    with pytest.raises(preview.PreviewGuardError, match="班主任工作台"):
        preview._assert_workspace_labels()

    bundle.write_text("备课工作台 班主任工作台", encoding="utf-8")
    preview._assert_workspace_labels()
