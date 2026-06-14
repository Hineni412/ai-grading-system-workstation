from __future__ import annotations

import ast
from pathlib import Path


WEB_APP = Path(__file__).resolve().parents[1] / "web_app.py"


def test_workflow_state_writer_runs_entire_body_under_shared_session_lock() -> None:
    module = ast.parse(WEB_APP.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "_write_session_workflow_state"
    )

    assert len(function.body) == 1
    lock_block = function.body[0]
    assert isinstance(lock_block, ast.With)
    assert len(lock_block.items) == 1
    lock_call = lock_block.items[0].context_expr
    assert isinstance(lock_call, ast.Call)
    assert isinstance(lock_call.func, ast.Name)
    assert lock_call.func.id == "get_answer_region_session_lock"
    assert ast.unparse(lock_call.args[0]) == "_session_work_dir(session_id)"
