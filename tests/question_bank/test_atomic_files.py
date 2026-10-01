from pathlib import Path
from unittest.mock import Mock

import pytest

from question_bank import atomic_files


def test_replace_publishes_complete_file(tmp_path: Path) -> None:
    source, destination = tmp_path / "new.tmp", tmp_path / "state.json"
    source.write_bytes(b"new complete state")
    destination.write_bytes(b"old state")
    atomic_files.replace_with_retry(source, destination)
    assert destination.read_bytes() == b"new complete state"
    assert not source.exists()


@pytest.mark.parametrize("locks", [0, 1, 5, 11])
def test_replace_retries_temporary_locks(monkeypatch, locks: int) -> None:
    replace = Mock(side_effect=[PermissionError("locked")] * locks + [None])
    sleep = Mock()
    monkeypatch.setattr(atomic_files.os, "replace", replace)
    monkeypatch.setattr(atomic_files.time, "sleep", sleep)
    source, destination = Path("new.tmp"), Path("state.json")
    atomic_files.replace_with_retry(source, destination)
    assert replace.call_count == locks + 1
    replace.assert_called_with(source, destination)
    assert [call.args[0] for call in sleep.call_args_list] == [
        min(0.05 * (attempt + 1), 0.4) for attempt in range(locks)
    ]


def test_replace_raises_original_error_after_three_seconds(monkeypatch) -> None:
    error = PermissionError("still locked")
    replace, sleep = Mock(side_effect=error), Mock()
    monkeypatch.setattr(atomic_files.os, "replace", replace)
    monkeypatch.setattr(atomic_files.time, "sleep", sleep)
    with pytest.raises(PermissionError) as raised:
        atomic_files.replace_with_retry(Path("new.tmp"), Path("state.json"))
    assert raised.value is error
    assert replace.call_count == 12
    assert sleep.call_count == 11
    assert sum(call.args[0] for call in sleep.call_args_list) == pytest.approx(3)


def test_replace_does_not_retry_other_io_errors(monkeypatch) -> None:
    replace, sleep = Mock(side_effect=FileNotFoundError("missing")), Mock()
    monkeypatch.setattr(atomic_files.os, "replace", replace)
    monkeypatch.setattr(atomic_files.time, "sleep", sleep)
    with pytest.raises(FileNotFoundError):
        atomic_files.replace_with_retry(Path("new.tmp"), Path("state.json"))
    replace.assert_called_once()
    sleep.assert_not_called()
