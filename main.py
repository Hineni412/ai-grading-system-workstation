from __future__ import annotations

import os
from pathlib import Path

from ai_grader import AIGrader
from db_manager import DBManager
from llm_client import LLMClient, LLMSettings
from report import ReportGenerator
from scanner import Scanner


def load_llm_settings_from_env() -> LLMSettings:
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        raise EnvironmentError("请先设置环境变量 LLM_API_KEY")

    return LLMSettings(
        api_key=api_key,
        base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
        ocr_model=os.getenv("LLM_OCR_MODEL", "gpt-4o-mini"),
        grading_model=os.getenv("LLM_GRADING_MODEL", "gpt-4o"),
        config_model=os.getenv("LLM_CONFIG_MODEL", "gpt-4o"),
        config_api_key=os.getenv("LLM_CONFIG_API_KEY", api_key),
        config_base_url=os.getenv("LLM_CONFIG_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")),
    )


def ensure_required_paths(exams_dir: Path, rubric_path: Path, answer_key_path: Path, reports_dir: Path) -> None:
    if not exams_dir.exists():
        raise FileNotFoundError(f"目录不存在: {exams_dir}")
    if not rubric_path.exists():
        raise FileNotFoundError(f"文件不存在: {rubric_path}")
    if not answer_key_path.exists():
        raise FileNotFoundError(f"文件不存在: {answer_key_path}")
    reports_dir.mkdir(parents=True, exist_ok=True)


def run_pipeline() -> None:
    base_dir = Path(__file__).resolve().parent
    try:
        from path_manager import get_path_manager
        pm = get_path_manager()
        exams_dir = pm.exams_dir
        reports_dir = pm.reports_dir
        db_path = pm.db_path
    except Exception:
        exams_dir = base_dir / "exams"
        reports_dir = base_dir / "reports"
        db_path = base_dir / "grading_system.db"
    rubric_path = base_dir / "config" / "rubric.json"
    answer_key_path = base_dir / "config" / "answer_key.json"

    ensure_required_paths(exams_dir, rubric_path, answer_key_path, reports_dir)
    llm_settings = load_llm_settings_from_env()
    llm_client = LLMClient(llm_settings)

    scanner = Scanner(exams_dir=exams_dir, llm_client=llm_client, ocr_model=llm_settings.ocr_model)
    grader = AIGrader(
        rubric_path=rubric_path,
        answer_key_path=answer_key_path,
        llm_client=llm_client,
        grading_model=llm_settings.grading_model,
    )
    db_manager = DBManager(db_path=db_path)
    reporter = ReportGenerator(db_path=db_path, reports_dir=reports_dir)

    db_manager.initialize()

    paper_groups = scanner.scan()
    if not paper_groups:
        print("[INFO] 没有可批改的有效试卷组。")
        return

    print(f"[INFO] 检测到 {len(paper_groups)} 组有效试卷，开始批改...")
    for idx, group in enumerate(paper_groups, start=1):
        print(f"[INFO] ({idx}/{len(paper_groups)}) 正在批改: {group.student_name}")
        try:
            result = grader.grade(group)
            db_manager.save_result(group, result)
            print(
                f"[OK] 完成: {result.student_name} - 得分 {result.student_score}/{result.total_score}"
                + (" [需人工复核]" if result.needs_human_review else "")
            )
        except Exception as exc:
            print(f"[ERROR] 批改失败: front={group.front_image.name}, back={group.back_image.name}, error={exc}")

    try:
        report_path = reporter.export()
        print(f"[OK] 报表已导出: {report_path}")
    except Exception as exc:
        print(f"[WARNING] 报表导出失败: {exc}")


if __name__ == "__main__":
    run_pipeline()
