"""旧八上标签刷新。默认只读；--write 只供本次报价与备份已获授权后使用。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path)
    parser.add_argument('--question-ids', nargs='+', type=int, default=[])
    parser.add_argument('--write', action='store_true')
    parser.add_argument('--approved-preview', default='')
    parser.add_argument('--operation-id', default='')
    args = parser.parse_args(argv)
    if args.write and (not args.question_ids or not args.approved_preview or not args.operation_id):
        parser.error('--write 必须同时指定本次题号、--approved-preview 指纹和 --operation-id')
    if args.data_root is not None:
        os.environ['AI_GRADING_DATA_DIR'] = str(args.data_root.resolve())
    from path_manager import get_path_manager
    data_root = args.data_root.resolve() if args.data_root is not None else get_path_manager().data_root
    from question_bank.taxonomy.governance import TaxonomyGovernance
    from question_bank.services.label_refresh_service import (
        build_label_refresh_gateway, execute_label_refresh, preview_label_refresh,
    )
    db_path = data_root / 'databases' / 'question_bank.db'
    governance = TaxonomyGovernance(state_path=get_path_manager().taxonomy_state_path,
                                    knowledge_graph_db_path=db_path)
    preview = preview_label_refresh(db_path=db_path, data_root=data_root,
                                    governance=governance, question_ids=args.question_ids)
    if not args.write:
        print(json.dumps(preview, ensure_ascii=False, indent=2))
        return 0
    if preview['preview_fingerprint'] != args.approved_preview:
        parser.error('题库或标准已变化，请重新预览、报价与授权')
    from question_bank.services.ai_tagging_service import AITaggingService
    service = AITaggingService(taxonomy_governance=governance)
    result = execute_label_refresh(db_path=db_path, data_root=data_root, governance=governance,
        authorization={**preview, 'confirmed': True, 'request_limit': preview['planned_requests']},
        operation_id=args.operation_id, model_name=service.model,
        gateway=build_label_refresh_gateway(service, operation_id=args.operation_id))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['saved_count'] == len(preview['question_ids']) else 1


if __name__ == '__main__':
    raise SystemExit(main())
