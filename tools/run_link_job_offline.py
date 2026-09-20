"""Offline driver for the evidence-point knowledge link job.

The link model is executed out-of-process (the operator/assistant acts as the
LLM). The tool drives ``run_knowledge_link_job`` with a file-backed gateway:

* ``requests/batch_NNNN.json`` — written for every batch that has no response
  yet; each contains the frozen evidence-point fields and the enumerated
  linkable candidates (skills + section keys) for that batch's questions.
* ``responses/batch_NNNN.json`` — fill these in with
  ``{question_id: [{"part_id": ..., "evidence_point_id": ...,
  "links": [{"fine_term_id": ..., "fine_term_name": ..., "role": ...}]}]}``
  and re-run the tool; batches with a response file are validated and written.

Re-running is idempotent: ``missing_only`` skips points already linked under
the release; ``regenerate`` rewrites the job's own rows.

Usage (portable python):

    runtime\\python\\python.exe tools/run_link_job_offline.py --out output/link_job/run1 --all
    runtime\\python\\python.exe tools/run_link_job_offline.py --out output/link_job/run1 --questions 17,23,42 --mode missing_only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.jobs.knowledge_link_job import run_knowledge_link_job


class _OfflineContext:
    """Minimal JobContext stand-in for the offline driver."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.job_id = 0
        self.job_type = "knowledge_link"

    def report(self, progress: float, stage: str, detail: str = "") -> None:
        print(f"  [{progress:5.1%}] {stage} {detail}")

    def raise_if_cancelled(self) -> None:
        return None


class _PendingResponse(Exception):
    def __init__(self, path: Path) -> None:
        super().__init__(f"response pending: {path}")
        self.path = path


def _file_gateway(requests_dir: Path, responses_dir: Path, counter: list[int]):
    def gateway(request: Mapping[str, Any]) -> Mapping[int, Any]:
        counter[0] += 1
        # Content-addressed names stay stable across re-runs even when
        # missing_only shrinks the pending set and reshuffles batches.
        digest = hashlib.sha256(
            json.dumps(request, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        name = f"batch_{digest}.json"
        request_path = requests_dir / name
        response_path = responses_dir / name
        if not request_path.exists():
            request_path.write_text(
                json.dumps(request, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if response_path.exists():
            raw = json.loads(response_path.read_text(encoding="utf-8"))
            return {int(key): value for key, value in raw.items()}
        raise _PendingResponse(response_path)

    return gateway


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="全库范围")
    group.add_argument("--questions", help="逗号分隔的 question_id 列表")
    group.add_argument("--questions-file", help="每行一个 question_id 的文件")
    parser.add_argument("--mode", choices=["missing_only", "regenerate"],
                        default="missing_only")
    parser.add_argument("--release", default="", help="graph_release_id（默认当前激活发布）")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--out", required=True, help="输出目录（requests/responses 子目录）")
    parser.add_argument("--db", default="user_data/databases/question_bank.db")
    parser.add_argument("--data-root", default="user_data")
    args = parser.parse_args()

    if args.questions:
        question_ids = [int(item) for item in args.questions.split(",") if item.strip()]
    elif args.questions_file:
        question_ids = [
            int(line) for line in Path(args.questions_file).read_text(
                encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        question_ids = None

    out_dir = Path(args.out)
    requests_dir = out_dir / "requests"
    responses_dir = out_dir / "responses"
    requests_dir.mkdir(parents=True, exist_ok=True)
    responses_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "mode": args.mode,
        "batch_size": args.batch_size,
        **({"question_ids": question_ids} if question_ids is not None else {}),
        **({"graph_release_id": args.release} if args.release else {}),
    }
    counter = [0]
    context = _OfflineContext(payload)
    summary = run_knowledge_link_job(
        context=context,
        question_bank_db_path=Path(args.db),
        data_root=Path(args.data_root),
        link_gateway=_file_gateway(requests_dir, responses_dir, counter),
    )
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    pending = len(list(requests_dir.glob("batch_*.json"))) - len(
        list(responses_dir.glob("batch_*.json"))
    )
    print("\n=== 链接任务结果 ===")
    print(f"  待处理题: {summary['questions_pending']} / {summary['questions_total']}")
    print(f"  已链接: {summary['questions_linked']}  失败: {summary['questions_failed']}")
    print(f"  写入链接: {summary['links_written']}")
    print(f"  未决响应文件: {max(pending, 0)}（填充 {responses_dir} 后重跑）")
    print(f"  汇总: {out_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
