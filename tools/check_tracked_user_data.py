from __future__ import annotations

import subprocess
import re
from pathlib import PurePosixPath


PRIVATE_PREFIXES = (
    'user_data/', 'backups/', 'logs/', 'output/', 'outputs/', 'runtime/',
    'dist/', 'build/', 'node_modules/', '.test-runs/', '.codex_artifacts/',
    '.p35t/', '.codex-review/', 'scratch/', 'frontend/node_modules/',
    'frontend/dist/', 'frontend/test-results/', 'frontend/playwright-report/',
)


def is_allowed_tracked_path(path: str) -> bool:
    normalized = path.replace('\\', '/').casefold()
    if normalized.startswith(PRIVATE_PREFIXES):
        return False
    name = PurePosixPath(normalized).name
    if name == 'api_profiles.json' or name.startswith('api_profiles.json.'):
        return False
    if name.endswith(('.db', '.sqlite', '.sqlite3', '.pem', '.key')):
        return False
    if re.search(r'\.(db|sqlite|sqlite3)[-.](wal|shm|journal|bak|backup)$', name):
        return False
    return not (name.startswith('.env') and name not in {
        '.env.example', '.env.sample', '.env.template',
    })


def tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return [path for path in result.stdout.split('\0') if path]


def main() -> int:
    blocked = [path for path in tracked_files() if not is_allowed_tracked_path(path)]
    if not blocked:
        print("OK: no tracked private data, credential files or generated runtime files")
        return 0
    print("Private or generated files should be removed from Git index:")
    for path in blocked[:200]:
        print(repr(path))
    if len(blocked) > 200:
        print(f"... and {len(blocked) - 200} more")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
