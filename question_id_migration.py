"""Explicit, reversible migration of legacy question-id files to canonical form.

只供维护入口显式调用：把旧的评分依据/答案文件里的小问号一次性改写为规范号
（``Q<n>(P<m>)``）。页面加载和批改启动路径**不得**导入或调用本模块——运行时统一
使用 ``question_id_contract`` 的内存规范化，磁盘文件只在此处显式迁移。

安全保证：
- 只有当 rubric 与 answer key 规范化后的明细题号集合完全一致时才迁移；否则原样保留
  两个源文件，不产生备份，避免把不匹配的配置写坏。
- 迁移时先分别复制带时间戳的备份，再对每个文件写规范化临时文件、``fsync`` 后原子替换。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from question_id_contract import (
    QuestionIdCatalog,
    QuestionIdContractError,
    canonicalize_question_document,
)


@dataclass(frozen=True, slots=True)
class QuestionIdMigrationReport:
    rubric_backup: Path
    answer_key_backup: Path
    changed_files: tuple[Path, ...]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: dict) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(directory))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def _backup_and_atomically_replace(
    rubric_path: Path,
    answer_key_path: Path,
    rubric: dict,
    answer_key: dict,
    backup_dir: Path,
) -> QuestionIdMigrationReport:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    rubric_backup = backup_dir / f"{rubric_path.stem}_{stamp}{rubric_path.suffix}.bak"
    answer_key_backup = backup_dir / f"{answer_key_path.stem}_{stamp}{answer_key_path.suffix}.bak"
    shutil.copy2(rubric_path, rubric_backup)
    shutil.copy2(answer_key_path, answer_key_backup)

    _atomic_write_json(rubric_path, rubric)
    _atomic_write_json(answer_key_path, answer_key)
    return QuestionIdMigrationReport(
        rubric_backup=rubric_backup,
        answer_key_backup=answer_key_backup,
        changed_files=(rubric_path, answer_key_path),
    )


def migrate_question_documents(
    rubric_path: Path,
    answer_key_path: Path,
    backup_dir: Path,
) -> QuestionIdMigrationReport:
    rubric_path = Path(rubric_path)
    answer_key_path = Path(answer_key_path)
    backup_dir = Path(backup_dir)

    rubric = canonicalize_question_document(_read_json(rubric_path))
    answer_key = canonicalize_question_document(_read_json(answer_key_path))
    if (
        QuestionIdCatalog.from_document(rubric).detail_ids
        != QuestionIdCatalog.from_document(answer_key).detail_ids
    ):
        raise QuestionIdContractError("rubric and answer key question ids differ")
    return _backup_and_atomically_replace(
        rubric_path, answer_key_path, rubric, answer_key, backup_dir
    )
