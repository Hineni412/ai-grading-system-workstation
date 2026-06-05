from __future__ import annotations

import subprocess


def is_allowed_tracked_user_data(path: str) -> bool:
    normalized = path.replace("\\", "/")
    if not normalized.startswith("user_data/"):
        return True
    return normalized.endswith("/.gitkeep") or normalized == "user_data/.gitkeep"


def tracked_user_data_files() -> list[str]:
    result = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "user_data"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def main() -> int:
    blocked = [path for path in tracked_user_data_files() if not is_allowed_tracked_user_data(path)]
    if not blocked:
        print("OK: no tracked runtime user data")
        return 0
    print("Tracked runtime user data should be removed from Git index:")
    for path in blocked[:200]:
        print(path)
    if len(blocked) > 200:
        print(f"... and {len(blocked) - 200} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
