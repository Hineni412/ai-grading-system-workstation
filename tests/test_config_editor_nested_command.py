from backend.api.routers.config import _editor_commands
from backend.api.schemas.config import ConfigEditorSaveRequest
from backend.config_workspace.editor import ReplaceQuestionStructureCommand


def test_nested_solution_command_decodes_without_exposing_internal_ids_to_ui() -> None:
    request = ConfigEditorSaveRequest.model_validate(
        {
            "revision": "a" * 64,
            "edits": [],
            "commands": [
                {
                    "kind": "replace_question_structure",
                    "question_id": "Q11",
                    "parts": [
                        {
                            "part_id": "P1",
                            "steps": [
                                {"step_id": "S1", "score": 2, "core_goal": "列出关系"},
                                {"step_id": "S2", "score": 3, "core_goal": "完成计算"},
                            ],
                        }
                    ],
                }
            ],
        }
    )

    commands = _editor_commands(request.commands)

    assert len(commands) == 1
    assert isinstance(commands[0], ReplaceQuestionStructureCommand)
    assert commands[0].question_id == "Q11"
    assert [step.score for step in commands[0].parts[0].steps] == [2, 3]
