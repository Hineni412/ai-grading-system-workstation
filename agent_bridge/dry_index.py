import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_bridge.run_session4 import _current_scan_batch_id, _write_papers_index, BRIDGE_DIR, SESSION_ID
from backend.repositories.grading_database import open_grading_repositories
from path_manager import get_path_manager
pm = get_path_manager()
batch_id = _current_scan_batch_id(pm.exams_dir)
work_dir = pm.templates_dir / f"session_{SESSION_ID}"
exams_dir = pm.exams_dir / f"session_{SESSION_ID}" / "scan_batches" / batch_id / "files"
db = open_grading_repositories(pm.databases_dir / "grading_system.db")
out = _write_papers_index(work_dir, exams_dir, db)
index = json.loads(out.read_text(encoding="utf-8"))
print("batch:", batch_id)
print("papers:", len(index))
missing = [k for k, v in index.items() if not v["front"] or not Path(v["front"]).exists() or not v["back"] or not Path(v["back"]).exists()]
print("missing_images:", missing)
dup = len(index) - len({v["student_id"] for v in index.values()})
print("duplicate_student_ids:", dup)
first = next(iter(index.items()))
print("sample:", first[0], first[1]["student_name"], first[1]["student_id"])
