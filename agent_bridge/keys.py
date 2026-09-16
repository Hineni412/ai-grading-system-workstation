import json, sys
from pathlib import Path
reqs = Path(r"agent_bridge/session_4/requests")
for d in sorted(p for p in reqs.iterdir() if p.is_dir()):
    rf = d / "request.json"
    if not rf.exists() or (d / "served.json").exists() or (d / "response.json").exists():
        continue
    m = json.loads(rf.read_text(encoding="utf-8")).get("manifest") or {}
    mode = m.get("mode")
    if mode == "objective_paper_recognition":
        print(f"{d.name}\n  KEY={m['paper_key']}\n  SID={m['student_id']} NAME={m.get('student_name')}")
    elif mode == "hybrid_major_batch":
        print(f"{d.name}\n  QID={m.get('question_id')} BATCH={m.get('batch_index')}")
        for it in m.get("items", []):
            print(f"    item={it.get('item_index')} KEY={it.get('paper_key')} SID={it.get('student_id')} NAME={it.get('student_name')} targets={it.get('target_detail_question_ids')}")
