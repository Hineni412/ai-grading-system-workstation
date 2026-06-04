from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import objective_answer_loader
import objective_question_registry


def test_registry_path_uses_path_manager_config_dir(monkeypatch, tmp_path: Path) -> None:
    config_dir = tmp_path / "custom_data" / "config"
    fake_pm = SimpleNamespace(config_dir=config_dir)

    monkeypatch.setattr(objective_question_registry, "get_path_manager", lambda: fake_pm)

    assert objective_question_registry._registry_path() == config_dir / "objective_question_registry.json"


def test_objective_answer_loader_uses_templates_dir(monkeypatch, tmp_path: Path) -> None:
    templates_dir = tmp_path / "custom_data" / "templates"
    session_dir = templates_dir / "session_42"
    session_dir.mkdir(parents=True)
    (session_dir / "answer_key.json").write_text(json.dumps({"Q1": "A"}), encoding="utf-8")
    fake_pm = SimpleNamespace(
        templates_dir=templates_dir,
        project_root=tmp_path / "project",
    )

    monkeypatch.setattr(objective_answer_loader, "get_path_manager", lambda: fake_pm)

    result = objective_answer_loader.load_objective_answer_sources("42")

    assert result["answers_by_question_id"]["Q1"]["standard_answer"] == "A"
