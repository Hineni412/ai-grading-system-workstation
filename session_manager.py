from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterator, Mapping

from backend.config_generation.normalization import validate_generated_config


def _validate_image_semantic_inputs(
    question_blocks: list[dict[str, Any]],
    q_images: dict[str, Any] | None,
) -> None:
    image_qids = [
        str(block.get("question_id") or "").strip()
        for block in question_blocks
        if isinstance(block, dict) and str(block.get("semantic_source") or "").strip() == "images"
    ]
    if not image_qids:
        return
    if not isinstance(q_images, dict) or not q_images:
        raise ValueError("PDF 图片裁题状态已丢失，请重新点击“① 拆分试卷”后再生成评分标准。")

    import base64

    invalid_qids: list[str] = []
    for qid in image_qids:
        image_data = q_images.get(qid)
        question_image = image_data.get("question") if isinstance(image_data, dict) else None
        if not isinstance(question_image, str) or not question_image.strip():
            invalid_qids.append(qid)
            continue
        try:
            base64.b64decode(question_image, validate=True)
            answer_image = image_data.get("answer")
            if answer_image:
                base64.b64decode(str(answer_image), validate=True)
        except (ValueError, TypeError):
            invalid_qids.append(qid)
    if invalid_qids:
        raise ValueError(
            "PDF 图片裁题数据无效，未退回纯文本模式。请重新拆题："
            + ", ".join(invalid_qids)
        )



def iter_effective_rubric_items(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, Mapping[str, object], Mapping[str, object]]]:
    for item_ref, _parent_ref, raw_question, raw_item in iter_effective_rubric_item_refs(payload):
        yield item_ref, raw_question, raw_item


def iter_effective_rubric_item_refs(
    payload: Mapping[str, object],
) -> Iterator[tuple[str, str, Mapping[str, object], Mapping[str, object]]]:
    rubric = payload.get("rubric") if isinstance(payload.get("rubric"), Mapping) else payload
    questions = rubric.get("questions") if isinstance(rubric, Mapping) else None
    if not isinstance(questions, list):
        return
    for question_index, raw_question in enumerate(questions, start=1):
        if not isinstance(raw_question, Mapping):
            continue
        question_ref = str(
            raw_question.get("question_id")
            or raw_question.get("id")
            or raw_question.get("number")
            or f"Q{question_index}"
        ).strip()
        parts = raw_question.get("parts")
        emitted = False
        if isinstance(parts, list) and parts:
            for part_index, raw_part in enumerate(parts, start=1):
                if not isinstance(raw_part, Mapping):
                    continue
                part_ref = str(
                    raw_part.get("part_id")
                    or raw_part.get("question_id")
                    or f"{question_ref}.{part_index}"
                ).strip()
                emitted = True
                yield part_ref, question_ref, raw_question, raw_part
        if not emitted:
            yield question_ref, question_ref, raw_question, raw_question



def save_generated_config(upload_dir: Path, payload: dict[str, Any], ts: str) -> tuple[Path, Path]:
    upload_dir.mkdir(parents=True, exist_ok=True)
    validate_generated_config(payload)
    rubric_path = upload_dir / f"rubric_{ts}.json"
    answer_key_path = upload_dir / f"answer_key_{ts}.json"

    _write_generated_json_atomic(rubric_path, payload["rubric"])
    _write_generated_json_atomic(answer_key_path, payload["answer_key"])

    return rubric_path, answer_key_path


def _write_generated_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.parent / f".{path.name}.{os.urandom(8).hex()}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

