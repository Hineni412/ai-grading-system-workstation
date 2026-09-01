from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.teaching_prep.domain.errors import (
    TeachingPrepNotFoundError,
    TeachingPrepStateError,
    TeachingPrepValidationError,
)
from backend.teaching_prep.domain.models import SlideAnimationRun

if TYPE_CHECKING:
    from backend.teaching_prep.application.preparation_service import (
        TeachingPrepService,
    )


PAGE_LIMIT = 4
BILLED_LIMIT = 3
SCENE_LIMIT = 12
_MAX_IMAGE_BYTES = 15_000_000
_HTML_MARK = re.compile(r"[<>]")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

_HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>课堂动画</title>
<style>
:root { color-scheme: dark; }
html, body { margin: 0; height: 100%; background: #111827; color: #f9fafb;
  font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif; }
body { display: grid; grid-template-rows: auto 1fr auto; min-height: 100%; }
header, footer { padding: 12px 20px; }
header { display: flex; justify-content: space-between; gap: 12px; align-items: baseline;
  border-bottom: 1px solid #374151; }
h1 { margin: 0; font-size: 20px; }
#progress { margin: 0; color: #d1d5db; }
main { display: grid; place-items: center; padding: 16px; cursor: pointer; outline: none; }
#card { width: min(1100px, 100%); display: grid; gap: 12px; }
#slide { width: 100%; max-height: 68vh; object-fit: contain; background: #000;
  border-radius: 12px; }
h2 { margin: 0; font-size: 22px; }
#narration { margin: 0; font-size: 18px; line-height: 1.6; color: #e5e7eb; }
footer { color: #9ca3af; font-size: 14px; border-top: 1px solid #374151; }
</style>
</head>
<body>
<header>
  <h1 id="deck-title"></h1>
  <p id="progress"></p>
</header>
<main id="stage" tabindex="0">
  <div id="card">
    <img id="slide" alt="">
    <h2 id="scene-title"></h2>
    <p id="narration"></p>
  </div>
</main>
<footer>点击画面、空格或左右方向键切换。可离线打开本文件播放，不需要网络。</footer>
<script type="application/json" id="payload">__PAYLOAD__</script>
<script>
(function () {
  var data = JSON.parse(document.getElementById("payload").textContent);
  var scenes = data.scenes || [];
  var images = data.images || {};
  var index = 0;
  var timer = null;
  var titleEl = document.getElementById("deck-title");
  var progressEl = document.getElementById("progress");
  var imgEl = document.getElementById("slide");
  var sceneTitleEl = document.getElementById("scene-title");
  var narrationEl = document.getElementById("narration");
  titleEl.textContent = data.title || "课堂动画";
  function show(next) {
    if (!scenes.length) return;
    index = (next + scenes.length) % scenes.length;
    var scene = scenes[index];
    var page = String(scene.source_page);
    imgEl.src = images[page] || "";
    imgEl.alt = scene.title || ("第" + page + "页");
    sceneTitleEl.textContent = scene.title || "";
    narrationEl.textContent = scene.narration || "";
    progressEl.textContent = (index + 1) + " / " + scenes.length;
    if (timer) window.clearTimeout(timer);
    var wait = Number(scene.duration_ms) || 2500;
    timer = window.setTimeout(function () { show(index + 1); }, wait);
  }
  function nextScene(delta) { show(index + delta); }
  document.getElementById("stage").addEventListener("click", function () { nextScene(1); });
  document.addEventListener("keydown", function (event) {
    if (event.key === "ArrowRight" || event.key === " " || event.key === "Enter") {
      event.preventDefault();
      nextScene(1);
    } else if (event.key === "ArrowLeft") {
      event.preventDefault();
      nextScene(-1);
    }
  });
  show(0);
  document.getElementById("stage").focus();
})();
</script>
</body>
</html>
"""


def normalize_storyboard(
    raw: object,
    *,
    allowed_pages: Sequence[int],
) -> dict[str, object]:
    if not isinstance(raw, Mapping):
        raise TeachingPrepValidationError("animation storyboard must be an object")
    allowed = {int(page) for page in allowed_pages}
    title = _clean_story_text(raw.get("title"), "title", minimum=1, maximum=80)
    scenes_raw = raw.get("scenes")
    if not isinstance(scenes_raw, list) or not scenes_raw:
        raise TeachingPrepValidationError("animation needs at least one scene")
    if len(scenes_raw) > SCENE_LIMIT:
        raise TeachingPrepValidationError("animation has too many scenes")
    scenes: list[dict[str, object]] = []
    for item in scenes_raw:
        if not isinstance(item, Mapping):
            raise TeachingPrepValidationError("animation scene must be an object")
        source_page = _as_int(item.get("source_page"), "source_page")
        if source_page not in allowed:
            raise TeachingPrepValidationError(
                "animation scene page is outside the selected pages"
            )
        duration_ms = _as_int(item.get("duration_ms"), "duration_ms")
        if duration_ms < 800 or duration_ms > 12_000:
            raise TeachingPrepValidationError("animation scene duration is invalid")
        scene: dict[str, object] = {
            "title": _clean_story_text(
                item.get("title"), "scene_title", minimum=1, maximum=80
            ),
            "narration": _clean_story_text(
                item.get("narration"), "narration", minimum=1, maximum=400
            ),
            "duration_ms": duration_ms,
            "source_page": source_page,
        }
        highlight = item.get("highlight")
        if highlight is not None and str(highlight).strip():
            scene["highlight"] = _clean_story_text(
                highlight, "highlight", minimum=1, maximum=80
            )
        scenes.append(scene)
    return {"title": title, "scenes": scenes}


def render_slide_animation_html(
    *,
    storyboard: Mapping[str, object],
    images: Mapping[int, tuple[str, bytes]],
) -> str:
    image_map: dict[str, str] = {}
    for page, (mime_type, content) in images.items():
        if mime_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise TeachingPrepValidationError("animation preview type is invalid")
        encoded = base64.b64encode(content).decode("ascii")
        image_map[str(int(page))] = f"data:{mime_type};base64,{encoded}"
    payload = {
        "title": storyboard["title"],
        "scenes": storyboard["scenes"],
        "images": image_map,
    }
    encoded_json = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("<", "\\u003c")
    return _HTML_TEMPLATE.replace("__PAYLOAD__", encoded_json)


def start_slide_animation_run(
    host: TeachingPrepService,
    lesson_node_id: str,
    *,
    operation_id: str,
    confirmed: bool,
    material_link_id: str,
    page_indexes: Sequence[int],
) -> tuple[SlideAnimationRun, bool]:
    if not confirmed:
        raise TeachingPrepValidationError(
            "slide animation generation requires explicit confirmation"
        )
    if not _adapter_available(host.slide_animation_model_adapter):
        raise TeachingPrepValidationError(
            "slide animation model is not configured"
        )
    clean_lesson_id = _entity_id(lesson_node_id)
    clean_link_id = _entity_id(material_link_id)
    clean_operation_id = _token(operation_id)
    pages = _clean_page_indexes(page_indexes)
    link = _require_primary_ppt_link(host, clean_lesson_id, clean_link_id)
    units_by_index = {
        unit.unit_index: unit
        for unit in host.list_material_units(link.material_version_id)
        if link.start_unit <= unit.unit_index <= link.end_unit
    }
    for page in pages:
        unit = units_by_index.get(page)
        if unit is None:
            raise TeachingPrepValidationError(
                "selected animation page is outside the primary PPT range"
            )
        preview = host.material_preview_path(unit.id)
        if not preview.is_file():
            raise TeachingPrepValidationError(
                "selected animation page preview is unavailable"
            )
    request_hash = _digest(
        {
            "lesson_node_id": clean_lesson_id,
            "material_link_id": clean_link_id,
            "material_version_id": link.material_version_id,
            "page_indexes": list(pages),
        }
    )
    return host.slide_animation_runs.begin(
        lesson_node_id=clean_lesson_id,
        material_version_id=link.material_version_id,
        material_link_id=clean_link_id,
        operation_id=clean_operation_id,
        request_hash=request_hash,
        page_indexes=pages,
        billed_limit=BILLED_LIMIT,
    )


def process_slide_animation_run(
    host: TeachingPrepService,
    run_id: str,
    *,
    task_model_gateway: object | None = None,
) -> None:
    clean_run_id = _entity_id(run_id)
    run = host.slide_animation_runs.get(clean_run_id)
    if run.status != "running":
        return
    adapter = host.slide_animation_model_adapter
    if adapter is None:
        host.slide_animation_runs.fail(clean_run_id, "model_unavailable")
        return
    html_path: Path | None = None
    try:
        host.slide_animation_runs.mark_call_started(clean_run_id)
        pages, images, model_pages = _load_selected_pages(host, run)
        model_kwargs: dict[str, Any] = {
            "operation_id": run.operation_id,
            "page_payload": {
                "lesson_node_id": run.lesson_node_id,
                "material_link_id": run.material_link_id,
                "pages": model_pages,
                "page_images": [
                    {
                        "page_index": page,
                        "mime_type": mime_type,
                        "content": content,
                    }
                    for page, (mime_type, content) in images.items()
                ],
            },
        }
        if task_model_gateway is not None:
            model_kwargs["task_model_gateway"] = task_model_gateway
        raw = adapter.generate(**model_kwargs)
        storyboard = normalize_storyboard(raw, allowed_pages=pages)
        html = render_slide_animation_html(storyboard=storyboard, images=images)
        html_path = _write_html(host, run, html)
        relpath = html_path.relative_to(host.root.resolve(strict=False)).as_posix()
        digest = hashlib.sha256(html.encode("utf-8")).hexdigest()
        updated = host.slide_animation_runs.finish(
            run_id=clean_run_id,
            storyboard=storyboard,
            html_relpath=relpath,
            html_sha256=digest,
        )
        if updated is None and html_path is not None:
            html_path.unlink(missing_ok=True)
    except Exception as exc:
        if html_path is not None:
            html_path.unlink(missing_ok=True)
        current = host.slide_animation_runs.get(clean_run_id)
        if current.status == "running":
            host.slide_animation_runs.fail(clean_run_id, _model_error_code(exc))


def list_slide_animation_runs(
    host: TeachingPrepService,
    lesson_node_id: str,
) -> dict[str, object]:
    clean_id = _entity_id(lesson_node_id)
    host.list_material_links(clean_id)
    items = host.slide_animation_runs.list_for_lesson(clean_id)
    return {
        "lesson_node_id": clean_id,
        "items": items,
        "billed_count": host.slide_animation_runs.billed_count(clean_id),
        "billed_limit": BILLED_LIMIT,
        "page_limit": PAGE_LIMIT,
    }


def get_slide_animation_run(
    host: TeachingPrepService,
    run_id: str,
) -> SlideAnimationRun:
    return host.slide_animation_runs.get(_entity_id(run_id))


def cancel_slide_animation_run(
    host: TeachingPrepService,
    run_id: str,
) -> SlideAnimationRun:
    return host.slide_animation_runs.cancel(_entity_id(run_id))


def accept_slide_animation_run(
    host: TeachingPrepService,
    run_id: str,
    *,
    expected_revision: int,
) -> SlideAnimationRun:
    return host.slide_animation_runs.accept(
        _entity_id(run_id),
        expected_revision=int(expected_revision),
    )


def discard_slide_animation_run(
    host: TeachingPrepService,
    run_id: str,
    *,
    expected_revision: int,
) -> SlideAnimationRun:
    return host.slide_animation_runs.discard(
        _entity_id(run_id),
        expected_revision=int(expected_revision),
    )


def slide_animation_preview_path(
    host: TeachingPrepService,
    run_id: str,
) -> Path:
    run = host.slide_animation_runs.get(_entity_id(run_id))
    if run.status not in {"succeeded", "accepted"}:
        raise TeachingPrepStateError("animation preview is not ready")
    return _resolved_html(host, run)


def slide_animation_download_path(
    host: TeachingPrepService,
    run_id: str,
) -> Path:
    run = host.slide_animation_runs.get(_entity_id(run_id))
    if run.status != "accepted" or run.teacher_decision != "accepted":
        raise TeachingPrepStateError("animation download requires teacher acceptance")
    return _resolved_html(host, run)


def _load_selected_pages(
    host: TeachingPrepService,
    run: SlideAnimationRun,
) -> tuple[tuple[int, ...], dict[int, tuple[str, bytes]], list[dict[str, object]]]:
    units_by_index = {
        unit.unit_index: unit
        for unit in host.list_material_units(run.material_version_id)
    }
    images: dict[int, tuple[str, bytes]] = {}
    model_pages: list[dict[str, object]] = []
    total_bytes = 0
    for page in run.page_indexes:
        unit = units_by_index.get(page)
        if unit is None:
            raise TeachingPrepValidationError("animation page unit is missing")
        preview = host.material_preview_path(unit.id)
        content = preview.read_bytes()
        total_bytes += len(content)
        if total_bytes > _MAX_IMAGE_BYTES:
            raise TeachingPrepValidationError("animation preview payload is too large")
        mime_type = (
            "image/jpeg"
            if preview.suffix.lower() in {".jpg", ".jpeg"}
            else "image/webp"
            if preview.suffix.lower() == ".webp"
            else "image/png"
        )
        images[page] = (mime_type, content)
        model_pages.append(
            {
                "page_index": page,
                "unit_id": unit.id,
                "title": unit.title or "",
                "text_excerpt": unit.text_excerpt or "",
            }
        )
    return run.page_indexes, images, model_pages


def _write_html(
    host: TeachingPrepService,
    run: SlideAnimationRun,
    html: str,
) -> Path:
    outputs = host.paths["outputs"].resolve(strict=False)
    directory = outputs / run.lesson_node_id / "animations"
    directory.mkdir(parents=True, exist_ok=True)
    target = (directory / f"{run.id}.html").resolve(strict=False)
    try:
        target.relative_to(outputs)
    except ValueError as exc:
        raise TeachingPrepValidationError("animation output path is invalid") from exc
    staging = target.with_name(f".{run.id}.html.part")
    staging.write_bytes(html.encode("utf-8"))
    staging.replace(target)
    return target


def _resolved_html(host: TeachingPrepService, run: SlideAnimationRun) -> Path:
    if not run.html_relpath:
        raise TeachingPrepNotFoundError("animation file is unavailable")
    outputs = host.paths["outputs"].resolve(strict=False)
    target = (host.root / run.html_relpath).resolve(strict=False)
    try:
        target.relative_to(outputs)
    except ValueError as exc:
        raise TeachingPrepValidationError("animation file path is invalid") from exc
    if not target.is_file():
        raise TeachingPrepNotFoundError("animation file is unavailable")
    if run.html_sha256 and hashlib.sha256(target.read_bytes()).hexdigest() != run.html_sha256:
        raise TeachingPrepValidationError("animation file changed on disk")
    return target


def _require_primary_ppt_link(
    host: TeachingPrepService,
    lesson_node_id: str,
    material_link_id: str,
):
    for link in host.list_material_links(lesson_node_id):
        if link.id != material_link_id:
            continue
        if not link.is_active:
            raise TeachingPrepValidationError("primary PPT link is inactive")
        if link.purpose != "reference_ppt" or link.material_type != "pptx":
            raise TeachingPrepValidationError("animation pages must come from the primary PPT")
        return link
    raise TeachingPrepNotFoundError("primary PPT link was not found")


def _clean_page_indexes(page_indexes: Sequence[int]) -> tuple[int, ...]:
    pages: list[int] = []
    seen: set[int] = set()
    for raw in page_indexes:
        page = int(raw)
        if page < 1 or page in seen:
            raise TeachingPrepValidationError("animation page indexes are invalid")
        seen.add(page)
        pages.append(page)
    pages.sort()
    if not pages or len(pages) > PAGE_LIMIT:
        raise TeachingPrepValidationError("select 1 to 4 PPT pages for animation")
    return tuple(pages)


def _clean_story_text(
    value: object,
    field: str,
    *,
    minimum: int,
    maximum: int,
) -> str:
    text = _CONTROL_CHARS.sub("", str(value or "")).strip()
    if len(text) < minimum or len(text) > maximum:
        raise TeachingPrepValidationError(f"{field} is invalid")
    if _HTML_MARK.search(text):
        raise TeachingPrepValidationError(f"{field} cannot contain markup")
    lowered = text.casefold()
    if "javascript:" in lowered or "data:text/html" in lowered:
        raise TeachingPrepValidationError(f"{field} contains a blocked URL")
    return text


def _as_int(value: object, field: str) -> int:
    try:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            raise TypeError
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TeachingPrepValidationError(f"{field} is invalid") from exc


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _adapter_available(adapter: object | None) -> bool:
    if adapter is None:
        return False
    probe = getattr(adapter, "is_available", None)
    if not callable(probe):
        return True
    try:
        return bool(probe())
    except (OSError, ValueError):
        return False


def _model_error_code(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "model_timeout"
    if isinstance(exc, TeachingPrepValidationError):
        return "model_response_invalid"
    return "model_request_failed"


def _entity_id(value: str) -> str:
    from backend.teaching_prep.application.preparation_service import (
        _clean_entity_id,
    )

    return _clean_entity_id(value)


def _token(value: str) -> str:
    from backend.teaching_prep.application.preparation_service import _clean_token

    return _clean_token(value)


__all__ = [
    "BILLED_LIMIT",
    "PAGE_LIMIT",
    "accept_slide_animation_run",
    "cancel_slide_animation_run",
    "discard_slide_animation_run",
    "get_slide_animation_run",
    "list_slide_animation_runs",
    "normalize_storyboard",
    "process_slide_animation_run",
    "render_slide_animation_html",
    "slide_animation_download_path",
    "slide_animation_preview_path",
    "start_slide_animation_run",
]
