"""Smoke test for the batch-inference channel (e.g. Volcengine Ark batch endpoint).

Sends one minimal grading-kind request through the batch channel and reports
latency and token usage.  The API key is read from the configured active
profile store and is never printed.

Run from the repository root with the bundled runtime, e.g.:

    set LLM_BATCH_ENABLED=true
    set LLM_BATCH_MODEL=ep-bi-xxxxxxxxxxxx-xxxxx
    runtime\\python\\python.exe tools\\batch_inference_smoke.py [--with-image]

Optional overrides: LLM_BATCH_BASE_URL, LLM_BATCH_API_KEY.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.jobs.default_handlers import _active_llm_settings
from llm_client import LLMClient


def _tiny_image_png() -> bytes:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (320, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.text((20, 40), "1+1=?", fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-image",
        action="store_true",
        help="also send one tiny image request to verify base64 images on the batch endpoint",
    )
    args = parser.parse_args()

    settings = _active_llm_settings()
    if settings is None:
        print("ERROR: no active LLM configuration found (api key missing).")
        return 2
    if not settings.batch_enabled or not settings.batch_model:
        print(
            "ERROR: batch inference is not enabled. Set LLM_BATCH_ENABLED=true "
            "and LLM_BATCH_MODEL=<batch endpoint id>."
        )
        return 2

    batch_base_url = settings.batch_base_url or f"{settings.base_url}/batch"
    print(f"online base_url : {settings.base_url}")
    print(f"batch  base_url : {batch_base_url}")
    print(f"batch  model    : {settings.batch_model}")
    print(f"api key         : configured ({len(settings.api_key)} chars, not shown)")

    client = LLMClient(settings)
    if not client.batch_enabled:
        print("ERROR: LLMClient did not enable the batch channel.")
        return 2

    usage_records: list[object] = []

    def run_case(name: str, images: list[bytes]) -> bool:
        print(f"\n[{name}] sending request ... (batch may take minutes)")
        started = time.monotonic()
        try:
            result = client.json_from_images(
                '只输出一个 JSON 对象：{"ok": true}',
                images,
                usage_callback=lambda res, _kwargs: usage_records.append(
                    getattr(res, "usage", None)
                    if not isinstance(res, dict)
                    else res.get("usage")
                ),
            )
        except Exception as exc:
            elapsed = time.monotonic() - started
            print(f"[{name}] FAILED after {elapsed:.1f}s: {type(exc).__name__}: {exc}")
            return False
        elapsed = time.monotonic() - started
        print(f"[{name}] OK in {elapsed:.1f}s, parsed result: {result}")
        if usage_records:
            print(f"[{name}] usage: {usage_records[-1]}")
        return True

    ok = run_case("text-only", [])
    if ok and args.with_image:
        ok = run_case("tiny-image", [_tiny_image_png()])

    print("\nSMOKE", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
