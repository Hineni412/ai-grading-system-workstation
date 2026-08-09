from __future__ import annotations

import argparse
import getpass
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.class_teacher.legacy_vault_conversion import (  # noqa: E402
    LegacyVaultConversionError,
    convert_legacy_vault,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "把一个旧班主任加密数据库转换为新的明文数据库副本。"
            "本工具不会覆盖、替换或删除原数据库。"
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="旧 student_affairs.db 的明确路径",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="工作区外尚不存在的新输出文件路径",
    )
    parser.add_argument(
        "--credential-kind",
        choices=("password", "pin", "recovery_key"),
        required=True,
        help="旧库当前可用的凭据种类",
    )
    parser.add_argument(
        "--protection-database",
        type=Path,
        help="PIN 模式下明确指定旧 class_teacher_work.db",
    )
    return parser


def _read_credential(kind: str) -> str:
    prompts = {
        "password": "请输入旧模块密码：",
        "pin": "请输入旧 6 位 PIN：",
        "recovery_key": "请输入旧恢复密钥：",
    }
    return getpass.getpass(prompts[kind])


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    credential = _read_credential(str(arguments.credential_kind))
    try:
        result = convert_legacy_vault(
            arguments.source,
            arguments.output,
            credential_kind=arguments.credential_kind,
            credential=credential,
            protection_database=arguments.protection_database,
        )
    except LegacyVaultConversionError as exc:
        print(
            json.dumps(
                {
                    "completed": False,
                    "code": exc.code,
                    "message": exc.message,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    except Exception:
        # The offline command must not expose local paths, database rows, or
        # credential-adjacent exception details even for an unexpected fault.
        print(
            json.dumps(
                {
                    "completed": False,
                    "code": "legacy_conversion_unexpected_failure",
                    "message": "转换未完成，旧数据库和既有输出没有被覆盖",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "completed": True,
                "converted_objects": result.converted_objects,
                "output": str(result.output_path),
                "output_sha256": result.output_sha256,
                "source_sha256": result.source_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
