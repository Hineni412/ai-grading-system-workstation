"""Build the checkout frontend only when its last successful build is stale."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from backend.api.frontend import FrontendDistributionError, validate_frontend_dist


STAMP_NAME = ".startup-build.json"


def _file_state(paths: list[Path], root: Path) -> dict[str, list[int]]:
    result = {}
    for path in sorted(set(paths)):
        if path.is_file():
            stat = path.stat()
            result[path.relative_to(root).as_posix()] = [stat.st_size, stat.st_mtime_ns]
    return result


def _inputs(project: Path) -> dict[str, list[int]]:
    frontend = project / "frontend"
    paths = [
        path for path in frontend.iterdir()
        if path.is_file() and (
            path.suffix in {".json", ".ts", ".js", ".mjs", ".cjs", ".html"}
            or path.name.startswith(".env") or path.name == ".npmrc"
        )
    ]
    for directory in (frontend / "src", frontend / "public", frontend / "scripts",
                      project / "components" / "answer_region_editor"):
        if directory.is_dir():
            paths.extend(directory.rglob("*"))
    # npm refreshes its installed-dependency inventory after install/update/ci.
    paths.extend([frontend / "node_modules" / ".package-lock.json", Path(__file__)])
    modules = frontend / "node_modules"
    if modules.is_dir():
        paths.extend(modules.glob("*/package.json"))
        paths.extend(modules.glob("@*/*/package.json"))
        paths.extend(modules.glob(".bin/*"))
    # __file__ belongs to this checkout in production; fixtures use another root.
    return _file_state([p for p in paths if p.is_relative_to(project)], project)


def _outputs(dist: Path) -> dict[str, list[int]]:
    validate_frontend_dist(dist)
    return _file_state([p for p in dist.rglob("*") if p.name != STAMP_NAME], dist)


def ensure_frontend(project: Path) -> bool:
    """Return True after rebuilding; fail closed if a build does not succeed."""
    project = project.resolve()
    frontend = project / "frontend"
    dist = frontend / "dist"
    if not (frontend / "package.json").is_file():
        validate_frontend_dist(dist)
        return False
    stamp = dist / STAMP_NAME
    inputs = _inputs(project)
    try:
        previous = json.loads(stamp.read_text(encoding="utf-8"))
        if previous == {"version": 1, "inputs": inputs, "outputs": _outputs(dist)}:
            print("Frontend unchanged; using the last successful build.", flush=True)
            return False
    except (OSError, ValueError, FrontendDistributionError):
        pass

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if not npm:
        raise RuntimeError("Frontend rebuild required, but Node.js/npm was not found.")
    # A failed or interrupted build must never acquire a valid startup stamp.
    stamp.unlink(missing_ok=True)
    print("Frontend changed or incomplete; building the latest frontend...", flush=True)
    subprocess.run([npm, "run", "build"], cwd=frontend, check=True)
    outputs = _outputs(dist)
    if _inputs(project) != inputs:
        raise RuntimeError("Frontend inputs changed during the build; run again.")
    stamp.write_text(json.dumps({"version": 1, "inputs": inputs, "outputs": outputs}), encoding="utf-8")
    return True


def main() -> None:
    started = time.perf_counter()
    try:
        ensure_frontend(Path(__file__).resolve().parents[1])
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Frontend preparation failed: {exc}", flush=True)
        raise SystemExit(1) from exc
    print(f"Frontend preparation: {time.perf_counter() - started:.2f}s", flush=True)


if __name__ == "__main__":
    main()
