from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Sequence

from question_bank.services.skill_migration_service import (
    SkillMigrationConfig,
    SkillMigrationService,
)
from question_bank.services.skill_catalog_service import SkillCatalogService


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="统一训练技能目录迁移工具")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for command in ("dry-run", "apply"):
        command_parser = subparsers.add_parser(command)
        command_parser.add_argument("--grading-db", type=Path, required=True)
        command_parser.add_argument("--question-bank-db", type=Path, required=True)
        command_parser.add_argument("--report-dir", type=Path, required=True)
        command_parser.add_argument("--gold-file", type=Path)
        command_parser.add_argument("--batch-id", required=command == "apply")

    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--question-bank-db", type=Path, required=True)
    rollback.add_argument("--batch-id", required=True)
    set_mode = subparsers.add_parser("set-mode")
    set_mode.add_argument("--question-bank-db", type=Path, required=True)
    set_mode.add_argument("--mode", choices=("legacy", "shadow", "skill"), required=True)
    set_mode.add_argument("--batch-id")
    set_mode.add_argument("--reason", required=True)
    set_mode.add_argument("--actor", default="local-operator")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = SkillMigrationService()
    if args.command == "rollback":
        report = service.rollback(args.question_bank_db, args.batch_id)
    elif args.command == "set-mode":
        catalog = SkillCatalogService(args.question_bank_db)
        previous = catalog.get_read_mode()
        catalog.set_read_mode(
            args.mode,
            actor=args.actor,
            reason=args.reason,
            batch_id=args.batch_id,
        )
        report = {
            "status": "succeeded",
            "previous_mode": previous,
            "current_mode": catalog.get_read_mode(),
            "batch_id": args.batch_id,
        }
    else:
        batch_id = args.batch_id or f"dry-run-{datetime.now():%Y%m%d-%H%M%S}"
        config = SkillMigrationConfig(
            grading_db=args.grading_db,
            question_bank_db=args.question_bank_db,
            report_dir=args.report_dir,
            batch_id=batch_id,
            gold_file=args.gold_file,
        )
        report = service.dry_run(config) if args.command == "dry-run" else service.apply(config)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0 if report.get("status") in {"ready", "succeeded", "rolled_back"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
