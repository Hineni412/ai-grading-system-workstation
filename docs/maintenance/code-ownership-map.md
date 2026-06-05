# Code Ownership Map

This document identifies maintenance hotspots without changing runtime code.

## Large Files

| File | Approx Lines | Responsibility | Future Split Target |
|---|---:|---|---|
| `web_app.py` | 6642 | Streamlit UI, workflow orchestration, uploads, exports | UI shell plus service calls |
| `pages/题库管理.py` | 2576 | Question-bank UI and import workflow | Question-bank page components |
| `session_manager.py` | 2139 | Rubric and answer-key generation | Config generation service |
| `db_manager.py` | 1903 | Grading database access | Student/session/result repositories |
| `scanner.py` | 967 | Scan grouping, PDF page render, name recognition | Scan input service |

## Zero-Invasive Maintenance Approach

Current phase adds documentation and standalone tools only.

Future refactors should follow these rules:

- Keep existing public function names until tests prove replacement behavior.
- Move one responsibility at a time.
- Add characterization tests before moving code.
- Keep old wrappers for one release after extraction.
- Do not change database records or stored path formats during refactors.

## Future Non-Zero-Invasive Targets

These require explicit approval because they touch functional implementation files:

- Replace direct upload writes in `web_app.py` with a content-addressed file store.
- Replace path-based enhanced image names in `image_preprocessor.py` with content-hash names.
- Move report page images to temporary directories in `original_paper_exporter.py`.
- Add backup retention to `db_manager.py`.
