from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from integration.result_cache import ResultCache


def test_hits_return_isolated_copies() -> None:
    cache = ResultCache(limit=4)
    first = cache.get_or_compute("k", lambda: {"items": [1, 2]})
    first["items"].append(99)
    again = cache.get_or_compute("k", lambda: {"items": []})
    assert again == {"items": [1, 2]}


def test_concurrent_callers_share_one_computation() -> None:
    cache = ResultCache(limit=4)
    calls = 0
    entered = threading.Event()
    release = threading.Event()

    def compute() -> dict:
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=10)
        return {"value": 7}

    with ThreadPoolExecutor(max_workers=4) as pool:
        waiter = pool.submit(cache.get_or_compute, "k", compute)
        assert entered.wait(timeout=10)
        followers = [
            pool.submit(cache.get_or_compute, "k", compute)
            for _ in range(3)
        ]
        release.set()
        results = [waiter.result(timeout=10)] + [
            future.result(timeout=10) for future in followers
        ]

    assert calls == 1
    assert all(result == {"value": 7} for result in results)


def test_failed_computation_releases_waiters_and_is_not_cached() -> None:
    cache = ResultCache(limit=4)
    attempts = 0

    def flaky() -> dict:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return {"ok": True}

    with pytest.raises(RuntimeError, match="boom"):
        cache.get_or_compute("k", flaky)
    assert cache.get_or_compute("k", flaky) == {"ok": True}
    assert attempts == 2


def test_lru_eviction_bounds_entries() -> None:
    cache = ResultCache(limit=2)
    cache.get_or_compute("a", lambda: 1)
    cache.get_or_compute("b", lambda: 2)
    cache.get_or_compute("c", lambda: 3)
    assert len(cache._entries) == 2
    assert "a" not in cache._entries
