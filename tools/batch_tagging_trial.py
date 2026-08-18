"""Real-data trial: tag N existing question-bank questions via the batch channel.

Mirrors the production tagging pipeline (context loading + taxonomy planning +
AITaggingService.analyze_questions) but never writes results back to the
database.  Reads real questions from the question bank DB; the API key comes
from the configured profile and is never printed.

Run from the repository root with the bundled runtime:

    set LLM_BATCH_ENABLED=true
    set LLM_BATCH_MODEL=ep-bi-xxxxxxxxxxxx-xxxxx
    runtime\\python\\python.exe tools\\batch_tagging_trial.py [--count 10] [--workers 10]
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.jobs.tagging_sync import _load_tagging_candidates, _plan_taxonomy
from question_bank.services.ai_tagging_service import AITaggingService

DB_PATH = Path("user_data/databases/question_bank.db")


def _pick_question_ids(count: int) -> list[int]:
    with sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT id FROM questions WHERE is_deleted = 0 "
            "AND question_text IS NOT NULL AND LENGTH(question_text) > 0 "
            "ORDER BY id LIMIT ?",
            (count,),
        ).fetchall()
    return [int(row[0]) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--workers", type=int, default=10)
    args = parser.parse_args()

    question_ids = _pick_question_ids(args.count)
    print(f"selected {len(question_ids)} questions: {question_ids}")
    if not question_ids:
        print("ERROR: no questions found in the question bank.")
        return 2

    service = AITaggingService()
    llm_settings = getattr(service.llm_client, "settings", None)
    batch_on = bool(
        getattr(llm_settings, "batch_enabled", False)
        and getattr(llm_settings, "batch_model", None)
    )
    print(f"batch channel: {'ON -> ' + str(llm_settings.batch_model) if batch_on else 'OFF (online!)'}")
    if not batch_on:
        print("ERROR: batch channel is not enabled; set LLM_BATCH_ENABLED/LLM_BATCH_MODEL.")
        return 2

    contexts, complete_ids, unavailable_ids = _load_tagging_candidates(
        DB_PATH,
        question_ids,
        force_question_ids=set(question_ids),
    )
    print(f"contexts built: {len(contexts)} (already tagged: {len(complete_ids)}, unavailable: {unavailable_ids})")

    contracts, revision = _plan_taxonomy(
        service,
        service.taxonomy_governance,
        contexts=contexts,
    )
    print(f"taxonomy contracts planned: {len(contracts)} (revision {revision})")

    started = time.monotonic()
    results = service.analyze_questions(
        contexts,
        taxonomy_contracts=contracts,
        max_workers=args.workers,
        progress_callback=lambda done, total, qid, res: print(
            f"  [{done}/{total}] question {qid}: "
            f"{'OK' if res.ok else 'FAILED'}"
            + ("" if res.ok else f" -> {str(res.error)[:120]}"),
            flush=True,
        ),
        allow_batch_fallback=False,
        quality_retry_limit=1,
        enable_review=False,
    )
    elapsed = time.monotonic() - started

    ok_count = sum(1 for r in results.values() if r.ok)
    print(f"\nfinished in {elapsed:.1f}s: {ok_count}/{len(results)} succeeded")
    for qid, res in results.items():
        if res.ok and res.analysis is not None:
            kp = getattr(res.analysis, "knowledge_points", None)
            print(f"  q{qid}: model={res.model_name} quality={res.quality_status} kp={str(kp)[:80]}")
        elif not res.ok:
            print(f"  q{qid}: FAILED quality={res.quality_status} error={str(res.error)[:160]}")
    return 0 if ok_count == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
