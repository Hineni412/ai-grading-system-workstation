from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from urllib.request import urlopen


def _runtime_data_dir() -> Path:
    if getattr(sys, "frozen", False):
        # Portable builds must keep all user data beside the executable,
        # even if the developer machine has AI_GRADING_DATA_DIR set.
        return Path(sys.executable).resolve().parent / "data"
    existing = os.getenv("AI_GRADING_DATA_DIR")
    if existing:
        return Path(existing).resolve()
    local_appdata = os.getenv("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata) / "AIGradingSystem"
    return Path(tempfile.gettempdir()) / "AIGradingSystem"


def main() -> None:
    try:
        import streamlit.web.bootstrap as bootstrap
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Streamlit is not installed. Please run pip install -r requirements.txt first.") from exc

    # --- Use PathManager when running from source; fall back for frozen builds ---
    try:
        from path_manager import get_path_manager
        pm = get_path_manager()
        data_home = pm.data_root
        pm.ensure_directories()
    except Exception:
        data_home = _runtime_data_dir()
        data_home.mkdir(parents=True, exist_ok=True)
        for relative_dir in (
            "exams",
            "reports",
            "config/uploaded",
            "templates",
            "annotated",
            "logs",
        ):
            (data_home / relative_dir).mkdir(parents=True, exist_ok=True)

    os.environ["AI_GRADING_DATA_DIR"] = str(data_home)
    os.environ["STREAMLIT_GLOBAL_DEVELOPMENT_MODE"] = "false"
    os.environ["STREAMLIT_SERVER_HEADLESS"] = "true"
    os.environ["STREAMLIT_BROWSER_GATHER_USAGE_STATS"] = "false"
    os.environ.setdefault("STREAMLIT_LOGGER_LEVEL", "info")

    # Import after AI_GRADING_DATA_DIR is set because web_app resolves paths at import time.
    import web_app  # noqa: F401
    import streamlit.config as streamlit_config

    streamlit_config.set_option("global.developmentMode", False, "desktop launcher")
    streamlit_config.set_option("server.headless", True, "desktop launcher")
    streamlit_config.set_option("browser.gatherUsageStats", False, "desktop launcher")

    launcher_path = data_home / "_streamlit_entry.py"
    launcher_path.write_text("from web_app import main\nmain()\n", encoding="utf-8")

    url = "http://localhost:8501"
    should_open_browser = os.getenv("AI_GRADING_OPEN_BROWSER", "1").strip().lower() not in {"0", "false", "no"}
    if should_open_browser:
        _open_browser_once_when_ready(data_home, url)

    flag_options = {
        "global.developmentMode": False,
        "server.headless": True,
        "server.port": 8501,
        "browser.serverPort": 8501,
        "server.address": "localhost",
        "browser.gatherUsageStats": False,
    }
    sys.argv = ["streamlit", "run", str(launcher_path)]
    try:
        bootstrap.run(str(launcher_path), False, [], flag_options)
    except Exception:  # noqa: BLE001
        error_path = data_home / "logs" / "desktop_launcher_error.log"
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        raise


def _open_browser_once_when_ready(data_home: Path, url: str) -> None:
    lock_path = data_home / "logs" / "browser_open.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if lock_path.exists() and time.time() - lock_path.stat().st_mtime < 12:
            return
        lock_path.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        return

    def worker() -> None:
        for _ in range(80):
            try:
                with urlopen(f"{url}/_stcore/health", timeout=0.5) as response:
                    if response.status == 200:
                        os.startfile(url)  # noqa: S606  # Windows desktop launcher.
                        return
            except Exception:
                time.sleep(0.25)

    threading.Thread(target=worker, name="open-browser-once", daemon=True).start()


if __name__ == "__main__":
    main()
