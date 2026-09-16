import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(".").resolve()))
from backend.repositories.compat import open_grading_repositories
from path_manager import get_path_manager, resolve_stored_file_path
from objective_batch_recognition_service import build_objective_question_specs
pm = get_path_manager()
db = open_grading_repositories(pm.databases_dir / "grading_system.db")
session = db.get_grading_session(4)
rubric = json.loads(resolve_stored_file_path(session["rubric_path"], data_root=pm.data_root).read_text(encoding="utf-8"))
ak = json.loads(resolve_stored_file_path(session["answer_key_path"], data_root=pm.data_root).read_text(encoding="utf-8"))
for spec in build_objective_question_specs("4", rubric, ak):
    print(spec.question_id, spec.question_type, "std=", json.dumps(spec.standard_answer, ensure_ascii=False))
