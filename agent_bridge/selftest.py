"""Bridge mechanics self-test — no real DB, no real pipeline.

Simulates one pipeline call against BridgeLLMClient, then writes a response
file the way the assistant will, and asserts the call returns it.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_bridge.bridge_client import BridgeLLMClient

PROMPT = (
    "You are recognizing answers.\n"
    "RESPONSE_SCHEMA_JSON: {}\n"
    'BATCH_MANIFEST_JSON: {"schema_version":2,"mode":"objective_paper_recognition",'
    '"session_id":4,"paper_key":"paper_001_student_59_test","student_id":59,'
    '"request_index":1,"target_question_ids":["Q1"],"pages":[],"atlas_path":"x"}\n'
)


def main() -> int:
    work = Path(tempfile.mkdtemp(prefix="bridge_selftest_"))
    try:
        client = BridgeLLMClient(work)
        usage_seen: list[dict] = []

        def usage_callback(completion, kwargs=None):
            usage_seen.append({"usage": getattr(completion, "usage", None)})

        result_holder: dict = {}

        def call():
            result_holder["response"] = client.json_from_images_once(
                PROMPT,
                [b"\xff\xd8\xff\xe0fake-jpeg-bytes"],
                model="agent-bridge",
                usage_callback=usage_callback,
            )

        thread = threading.Thread(target=call, daemon=True)
        thread.start()

        req_dir = None
        deadline = time.time() + 15
        while time.time() < deadline:
            dirs = [
                p
                for p in (work / "requests").iterdir()
                if p.is_dir() and (p / "request.json").exists()
            ]
            if dirs:
                req_dir = dirs[0]
                break
            time.sleep(0.2)
        assert req_dir is not None, "request directory was not created"
        request = json.loads((req_dir / "request.json").read_text(encoding="utf-8"))
        assert request["request_key"].startswith("obj_001_"), request["request_key"]
        assert request["manifest"]["paper_key"] == "paper_001_student_59_test"
        assert (req_dir / "atlas_0.jpg").exists()
        assert "BATCH_MANIFEST_JSON" in (req_dir / "prompt_static.txt").read_text(encoding="utf-8")

        payload = {
            "paper_key": "paper_001_student_59_test",
            "student_id": 59,
            "answers": [
                {
                    "question_id": "Q1",
                    "question_type": "choice",
                    "recognized_answer": "A",
                    "confidence": 0.97,
                    "need_review": False,
                    "review_reason": "",
                }
            ],
        }
        (req_dir / "response.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        thread.join(timeout=15)
        assert not thread.is_alive(), "client did not return after response.json"
        assert result_holder["response"] == payload
        assert usage_seen, "usage_callback was not invoked"
        assert (req_dir / "served.json").exists()

        # Second attempt for the same key must NOT reuse responses/<key>.json
        # blindly: it waits for its own per-attempt response.json.
        key = request["request_key"]
        (client.responses_dir / f"{key}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

        def call2():
            result_holder["response2"] = client.json_from_images_once(
                PROMPT, [b"\xff\xd8\xff\xe0fake"], usage_callback=None
            )

        t2 = threading.Thread(target=call2, daemon=True)
        t2.start()
        time.sleep(2.5)
        assert t2.is_alive(), "first-attempt precomputed response was reused on retry"
        req_dir2 = sorted(
            p for p in (work / "requests").iterdir() if p.is_dir()
        )[-1]
        (req_dir2 / "response.json").write_text(
            json.dumps({"ok": True}), encoding="utf-8"
        )
        t2.join(timeout=10)
        assert result_holder["response2"] == {"ok": True}

        # Fresh client (simulated restart) honours precomputed responses.
        client2 = BridgeLLMClient(work)
        client2._key_attempts.clear()
        resp = client2.json_from_images_once(PROMPT, [b"\xff\xd8\xff\xe0f"])
        assert resp == payload, resp

        print("selftest OK")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
