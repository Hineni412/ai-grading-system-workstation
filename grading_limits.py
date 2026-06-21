from __future__ import annotations

from typing import Any


OBJECTIVE_BATCH_SIZE_MIN = 1
OBJECTIVE_BATCH_SIZE_MAX = 15

SUBJECTIVE_MAJOR_BATCH_SIZE_MIN = 1
SUBJECTIVE_MAJOR_BATCH_SIZE_MAX = 20

PRECHECK_WORKERS_MIN = 1
PRECHECK_WORKERS_MAX = 32

FULL_PAPER_WORKERS_MIN = 1
FULL_PAPER_WORKERS_MAX = 200

HYBRID_INFLIGHT_WORKERS_MIN = 1
HYBRID_INFLIGHT_WORKERS_MAX = 1000

GRADING_RPM_MIN = 1
GRADING_RPM_MAX = 10000


def bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        resolved = int(value) if value not in (None, "") else int(default)
    except (TypeError, ValueError):
        resolved = int(default)
    return max(minimum, min(maximum, resolved))
