"""File-backed stand-in for the visual grading model.

The hybrid grading pipeline calls ``json_from_images_once`` /
``json_from_images_with_options`` / ``json_from_images`` on the injected
client.  Instead of performing a paid network request, this client writes
each request's prompt, manifest and atlas images into ``requests/<dir>/``
and blocks until a response JSON file appears:

* ``requests/<dir>/response.json`` — per-attempt answer (always honoured);
* ``responses/<request_key>.json`` — reusable answer keyed by the manifest
  (honoured on the first attempt of that key only, so a pipeline retry gets
  a fresh per-attempt file instead of re-consuming a rejected response).

Everything downstream — response validation, programmatic objective scoring,
teacher-lock merge, run ledger and DB persistence — stays inside the real
pipeline.  This module only replaces the bytes the model would have sent.
"""
from __future__ import annotations

import itertools
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

_MANIFEST_MARKER = "BATCH_MANIFEST_JSON:"
_POLL_SECONDS = 1.0


class BridgeCancelled(RuntimeError):
    """Raised inside a worker when the CANCEL file appears mid-request."""


def _image_ext(blob: bytes) -> str:
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    return ".bin"


def _extract_manifest(*texts: str | None) -> dict[str, Any] | None:
    for text in texts:
        if not text or _MANIFEST_MARKER not in text:
            continue
        tail = text.split(_MANIFEST_MARKER, 1)[1].lstrip()
        try:
            obj, _ = json.JSONDecoder().raw_decode(tail)
        except ValueError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _safe(value: Any) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value or "")).strip("_")


def _request_key(manifest: dict[str, Any] | None, index: int) -> str:
    if isinstance(manifest, dict):
        mode = manifest.get("mode")
        if mode == "objective_paper_recognition":
            return (
                f"obj_{int(manifest.get('request_index') or index):03d}_"
                f"{_safe(manifest.get('paper_key'))[:24]}"
            )
        if mode == "hybrid_major_batch":
            return (
                f"major_{_safe(manifest.get('question_id'))}_"
                f"{int(manifest.get('batch_index') or index):03d}"
            )
        if mode == "objective_batch_recognition":
            return (
                f"objbatch_{_safe(manifest.get('question_id'))}_"
                f"{int(manifest.get('batch_index') or index):03d}"
            )
    return f"req_{index:04d}"


class BridgeLLMClient:
    """Drop-in replacement for ``llm_client.LLMClient`` used by the pipeline."""

    def __init__(
        self,
        work_dir: Path | str,
        *,
        grading_model: str = "agent-bridge",
        policy_profile: dict[str, Any] | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.requests_dir = self.work_dir / "requests"
        self.responses_dir = self.work_dir / "responses"
        self.cancel_file = self.work_dir / "CANCEL"
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)
        self.run_tag = time.strftime("%Y%m%d_%H%M%S")
        self.settings = SimpleNamespace(
            ocr_model=grading_model,
            grading_model=grading_model,
            config_model=grading_model,
            policy_profile=dict(policy_profile or {}),
        )
        self._counter = itertools.count(1)
        self._key_attempts: dict[str, int] = {}
        self._lock = threading.Lock()

    # -- client seams used by the grading pipeline -------------------------

    def json_from_images_once(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        extra_kwargs: dict[str, Any] | None = None,
        use_config_client: bool = False,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        usage_callback: Any = None,
    ) -> dict[str, Any]:
        return self._serve(
            prompt=prompt,
            dynamic_prompt=dynamic_prompt,
            image_blobs=image_blobs,
            static_image_blobs=static_image_blobs,
            model=model,
            system_prompt=system_prompt,
            usage_callback=usage_callback,
        )

    def json_from_images_with_options(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        usage_callback: Any = None,
        extra_kwargs: dict[str, Any] | None = None,
        use_config_client: bool = False,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        allow_gateway_retry: bool = False,
        request_kind: Any = None,
        image_compression_memo: dict[str, bytes] | None = None,
    ) -> dict[str, Any]:
        return self._serve(
            prompt=prompt,
            dynamic_prompt=dynamic_prompt,
            image_blobs=image_blobs,
            static_image_blobs=static_image_blobs,
            model=model,
            system_prompt=system_prompt,
            usage_callback=usage_callback,
        )

    def json_from_images(
        self,
        prompt: str,
        image_blobs: list[bytes],
        model: str | None = None,
        system_prompt: str | None = None,
        usage_callback: Any = None,
        static_image_blobs: list[bytes] | None = None,
        dynamic_prompt: str | None = None,
        allow_gateway_retry: bool = False,
        request_kind: Any = None,
        extra_kwargs: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._serve(
            prompt=prompt,
            dynamic_prompt=dynamic_prompt,
            image_blobs=image_blobs,
            static_image_blobs=static_image_blobs,
            model=model,
            system_prompt=system_prompt,
            usage_callback=usage_callback,
        )

    def json_from_text(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("bridge: text-only request is not part of hybrid grading")

    def json_from_text_once(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("bridge: text-only request is not part of hybrid grading")

    # -- request lifecycle ---------------------------------------------------

    def _serve(
        self,
        *,
        prompt: str | None,
        dynamic_prompt: str | None,
        image_blobs: list[bytes] | None,
        static_image_blobs: list[bytes] | None,
        model: str | None,
        system_prompt: str | None,
        usage_callback: Any,
    ) -> dict[str, Any]:
        with self._lock:
            index = next(self._counter)
        manifest = _extract_manifest(dynamic_prompt, prompt)
        request_key = _request_key(manifest, index)
        with self._lock:
            attempt = self._key_attempts.get(request_key, 0) + 1
            self._key_attempts[request_key] = attempt

        req_dir = self.requests_dir / f"{self.run_tag}_{index:04d}_{request_key}"
        req_dir.mkdir(parents=True, exist_ok=True)

        image_names: list[str] = []
        for i, blob in enumerate(static_image_blobs or []):
            name = f"static_{i}{_image_ext(blob)}"
            (req_dir / name).write_bytes(blob)
            image_names.append(name)
        for i, blob in enumerate(image_blobs or []):
            name = f"atlas_{i}{_image_ext(blob)}"
            (req_dir / name).write_bytes(blob)
            image_names.append(name)

        (req_dir / "prompt_static.txt").write_text(prompt or "", encoding="utf-8")
        (req_dir / "prompt_dynamic.txt").write_text(dynamic_prompt or "", encoding="utf-8")
        (req_dir / "system_prompt.txt").write_text(system_prompt or "", encoding="utf-8")
        (req_dir / "request.json").write_text(
            json.dumps(
                {
                    "request_key": request_key,
                    "attempt": attempt,
                    "model": model,
                    "images": image_names,
                    "manifest": manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

        candidates = [req_dir / "response.json"]
        if attempt == 1:
            candidates.append(self.responses_dir / f"{request_key}.json")

        while True:
            if self.cancel_file.exists():
                raise BridgeCancelled(f"cancelled while serving {request_key}")
            for path in candidates:
                if not path.is_file():
                    continue
                try:
                    response = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(response, dict):
                    if usage_callback is not None:
                        try:
                            usage_callback(
                                SimpleNamespace(
                                    usage={
                                        "prompt_tokens": 0,
                                        "completion_tokens": 0,
                                        "total_tokens": 0,
                                    }
                                ),
                                {"model": model},
                            )
                        except Exception:
                            pass
                    (req_dir / "served.json").write_text(
                        json.dumps({"source": str(path)}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    return response
            time.sleep(_POLL_SECONDS)
