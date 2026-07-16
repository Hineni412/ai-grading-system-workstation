from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _pinned_versions(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        package, version = line.split("==", 1)
        pins[package.lower()] = version
    return pins


def test_parallel_test_dependencies_are_test_only_and_fully_pinned() -> None:
    runtime_requirements = _pinned_versions(PROJECT_ROOT / "requirements.txt")
    test_requirements = _pinned_versions(PROJECT_ROOT / "requirements-test.txt")
    constraints = _pinned_versions(PROJECT_ROOT / "constraints.txt")

    assert "pytest-xdist" not in runtime_requirements
    assert "execnet" not in runtime_requirements
    assert test_requirements == {
        "execnet": "2.1.2",
        "pytest": "9.1.1",
        "pytest-xdist": "3.8.0",
    }
    assert constraints["execnet"] == test_requirements["execnet"]
    assert constraints["pytest"] == test_requirements["pytest"]
    assert constraints["pytest-xdist"] == test_requirements["pytest-xdist"]
