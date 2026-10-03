"""Show paired recommendations with question content read in place, without exports."""
from __future__ import annotations

import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from question_bank.services.question_read_service import QuestionBankReadService


def comparison_question_ids(report: dict) -> set[int]:
    ids = {int(case["id"]) for case in report.get("manual_review", {}).get("cases", [])}
    for scope in report["scopes"]:
        for paper in scope["papers"]:
            for version in ("before", "after"):
                ids.update(int(item["id"]) for item in paper[version]["items"])
            for gap in paper["after"].get("gaps", []):
                if gap.get("candidate"):
                    ids.add(int(gap["candidate"]))
        for groups in scope.get("groups", []):
            for group in groups:
                ids.update(int(qid) for qid in group["question_ids"])
    return ids


def question_content(service, question_id: int) -> dict | None:
    """Reuse the domain reader's controlled Word HTML and question-only blocks."""
    questions = service.get_questions([question_id])
    if not questions:
        return None
    question = questions[0]
    blocks = question.get("rich_content", {}).get("question_blocks", [])
    fields = ("kind", "text", "segments", "rows", "html", "asset_urls")
    return {
        "id": int(question["id"]),
        "question_type": question.get("question_type"),
        "text": question.get("question_text") or "",
        "blocks": [{key: block.get(key) for key in fields} for block in blocks],
    }


def make_handler(directory: Path, service):
    directory = directory.resolve()
    report = json.loads((directory / "comparison.json").read_text(encoding="utf-8"))
    allowed = comparison_question_ids(report)
    vendor = PROJECT_ROOT / "frontend/node_modules/katex/dist"
    question_route = re.compile(r"/question-content/(\d+)")
    asset_route = re.compile(r"/api/question-bank/questions/(\d+)/assets/(\d+)")
    vendor_route = re.compile(r"/vendor/katex/(katex\.min\.(?:js|css)|contrib/auto-render\.min\.js|fonts/[A-Za-z0-9_-]+\.(?:woff2?|ttf))")

    class ReportHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            # Do not log question bodies, private paths, or domain exceptions.
            pass

        def _send(self, status, body, content_type):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, value):
            self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def _file(self, path, content_type):
            if not path.is_file():
                self._json(404, {"error": "内容暂时无法读取"})
                return
            self._send(200, path.read_bytes(), content_type)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path in ("/", "/training-comparison.html"):
                self._file(directory / "training-comparison.html", "text/html; charset=utf-8")
                return
            if path == "/question-content.js":
                self._file(Path(__file__).with_name("training_endpoint_question_content.js"),
                           "text/javascript; charset=utf-8")
                return
            vendor_match = vendor_route.fullmatch(path)
            if vendor_match:
                relative = vendor_match[1]
                suffix = Path(relative).suffix
                media = {".js": "text/javascript", ".css": "text/css", ".woff2": "font/woff2",
                         ".woff": "font/woff", ".ttf": "font/ttf"}[suffix]
                self._file(vendor / relative, media)
                return
            question_match = question_route.fullmatch(path)
            asset_match = asset_route.fullmatch(path)
            if not (question_match or asset_match):
                self._json(404, {"error": "本页没有此内容"})
                return
            match = question_match or asset_match
            question_id = int(match[1])
            if question_id not in allowed:
                self._json(404, {"error": "题目不属于本次对比"})
                return
            try:
                if question_match:
                    content = question_content(service, question_id)
                    self._json(200 if content else 404, content or {"error": "题目暂时无法读取"})
                else:
                    resolved = service.resolve_asset(question_id, int(match[2]))
                    self._file(resolved.path, resolved.media_type)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self._json(503, {"error": "题库内容暂时无法读取，请稍后重试"})

    return ReportHandler


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--port", type=int, default=12029)
    args = parser.parse_args()
    data_root = PROJECT_ROOT / "user_data"
    reader = QuestionBankReadService(data_root / "databases/question_bank.db", data_root=data_root)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(args.directory, reader))
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/training-comparison.html",
                      "question_source": "read_only_in_place"}), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
