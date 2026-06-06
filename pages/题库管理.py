from __future__ import annotations

import html
import logging
import re
import textwrap
from pathlib import Path
from typing import Any

import streamlit as st

from export_names import safe_filename_fragment
from question_bank.database.paths import project_data_root, question_bank_db_path
from question_bank.importers.batch_importer import (
    PaperMetadata,
    ScannedPaper,
    import_scanned_papers,
    scan_paper_folder,
)
from question_bank.models.tag_schema import TagAnalysis, TaggingContext
from question_bank.services.ai_tagging_service import (
    ABILITY_TAG_OPTIONS,
    AITaggingService,
    CURRICULUM_CHAPTERS,
    KNOWLEDGE_POINT_OPTIONS,
    MATH_MODEL_OPTIONS,
    METHOD_TAG_OPTIONS,
    STUDENT_LEVELS,
    is_auto_saveable_result,
)
from question_bank.services.question_service import QuestionService, has_complete_analysis_tags
from question_bank.services.question_frequency_service import (
    FrequencyMetrics,
    QuestionFrequencyService,
    frequency_summary,
)
from question_bank.services.question_preview_display import (
    PreviewDensity,
    image_display_width,
    resolve_preview_density,
)
from question_bank.services.rich_content_backfill_service import backfill_missing_rich_content
from question_bank.services.similarity_service import SimilarityPlan, build_ai_upload_similarity_plan
from question_bank.services.local_file_dialog import get_local_file_dialog
from question_bank.parsers.type_detector import detect_question_type


LOGGER = logging.getLogger(__name__)
SCAN_ROWS_KEY = "qb_scan_rows"
IMPORT_DIALOG_OPEN_KEY = "qb_import_dialog_open"
IMPORT_SUCCESS_MESSAGE_KEY = "qb_import_success_message"
AI_RESULTS_KEY = "qb_ai_tag_results"
SHOW_FILTERED_QUESTIONS_KEY = "qb_show_filtered_questions"
LAST_AI_SUMMARY_KEY = "qb_last_ai_tagging_summary"
BASKET_KEY = "qb_question_basket"
BASKET_PANEL_KEY = "qb_basket_panel_open"
SIMILARITY_REVIEW_KEY = "qb_ai_similarity_review"
PREVIEW_DENSITY_KEY = "qb_preview_density"
PREVIEW_IMAGE_SCALE_KEY = "qb_preview_image_scale"
PREVIEW_DENSITY_OPTIONS = ("紧凑", "舒适")
TAG_FILTER_CONFIG = (
    ("knowledge_point", "知识点"),
    ("method", "思想方法"),
    ("ability", "数学能力"),
    ("model", "数学模型"),
    ("error_type", "易错点"),
    ("exam_scope", "教材章节"),
)
TAG_GROUP_LABELS = {
    "knowledge_point": "知识点",
    "method": "思想方法",
    "ability": "数学能力",
    "model": "数学模型",
    "error_type": "易错点",
    "prerequisite": "前置知识",
    "exam_scope": "教材章节",
    "teaching_stage": "教学阶段",
    "student_level": "适合层次",
}
IMAGE_MARKER_PATTERN = re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
EDITED_TAG_FIELDS = {
    "knowledge_points",
    "method_tags",
    "ability_tags",
    "math_model_tags",
    "error_prone_points",
    "prerequisite_points",
    "textbook_chapter",
    "teaching_stage",
    "suitable_student_level",
}
ANALYSIS_TAG_TYPES = {
    "knowledge_point",
    "method",
    "ability",
    "model",
    "error_type",
    "prerequisite",
    "exam_scope",
    "teaching_stage",
    "student_level",
    "canonical_knowledge_id",
}


def _tagging_context(question: dict, service: QuestionService | None = None) -> TaggingContext:
    existing_tags = [tag["tag_value"] for tag in question.get("tags", []) if tag.get("tag_value")]
    return TaggingContext(
        question_text=question["question_text"],
        answer_text=question.get("answer_text"),
        question_number=question.get("question_number"),
        question_type=question.get("question_type"),
        grade=question.get("grade"),
        semester=question.get("semester"),
        exam_type=question.get("exam_type"),
        district=question.get("district"),
        has_images=bool(question.get("has_images") or question.get("needs_image_review") or question.get("image_paths")),
        corpus_stats=_corpus_overview(service) if service is not None else {},
        existing_tags=existing_tags,
    )


def _scan_row(item: ScannedPaper) -> dict[str, Any]:
    metadata = item.metadata
    return {
        "导入": True,
        "文件": Path(item.source_file).name,
        "格式": item.file_type.upper(),
        "年份": metadata.year or "",
        "省份": metadata.province or "",
        "城市": metadata.city or "",
        "地区": metadata.district or "",
        "考试类型": metadata.exam_type or "",
        "年级": metadata.grade or "",
        "学期": metadata.semester or "",
        "路径": item.source_file,
    }


def _row_to_scanned_paper(row: dict[str, Any]) -> ScannedPaper:
    return ScannedPaper(
        source_file=str(row.get("路径") or ""),
        file_type=str(row.get("格式") or "").lower(),
        metadata=PaperMetadata(
            year=_cell_text(row.get("年份")),
            province=_cell_text(row.get("省份")),
            city=_cell_text(row.get("城市")),
            district=_cell_text(row.get("地区")),
            exam_type=_cell_text(row.get("考试类型")),
            grade=_cell_text(row.get("年级")),
            semester=_cell_text(row.get("学期")),
        ),
    )


def _render_import_area(service: QuestionService, raw_papers_dir: Path) -> None:
    action_cols = st.columns([1, 4])
    with action_cols[0]:
        open_import_dialog = st.button("导入真题", type="primary", use_container_width=True)
    with action_cols[1]:
        st.caption("从本地文件夹扫描 PDF / DOCX，扫描结果只保存在当前页面会话；不影响原 AI 阅卷流程。")
    if open_import_dialog:
        st.session_state[IMPORT_DIALOG_OPEN_KEY] = True
    if st.session_state.get(IMPORT_DIALOG_OPEN_KEY):
        _render_import_dialog(service, raw_papers_dir)


def _render_rich_content_tools(service: QuestionService) -> None:
    with st.expander("Word 公式与图片修复", expanded=False):
        st.caption(
            "为已经入库的 DOCX 题目补生成本地侧车文件，用于组卷导出时尽量保留 Word 原生公式和图片。"
            "不会修改题库数据库，也不会改动原始试卷文件。"
        )
        overwrite = st.checkbox("覆盖已有侧车文件", value=False, key="qb_rich_backfill_overwrite")
        if st.button("回填缺失的 Word 富文本", key="qb_rich_backfill_run", type="secondary"):
            with st.spinner("正在扫描已入库 DOCX 题目并补生成侧车文件..."):
                from importlib import import_module, reload

                backfill_module = reload(import_module("question_bank.services.rich_content_backfill_service"))
                result = backfill_module.backfill_missing_rich_content(
                    service.db_path,
                    overwrite=overwrite,
                    update_preview_text=True,
                )
            metric_cols = st.columns(6)
            metric_cols[0].metric("扫描题目", result.scanned_questions)
            metric_cols[1].metric("成功回填", result.created_sidecars)
            metric_cols[2].metric("已有跳过", result.skipped_existing)
            metric_cols[3].metric("未匹配题号", result.skipped_no_match)
            metric_cols[4].metric("源文件缺失", result.skipped_missing_source)
            metric_cols[5].metric("预览更新", getattr(result, "updated_preview_texts", 0))
            if result.failed_sources:
                st.warning(f"有 {result.failed_sources} 个 DOCX 源文件解析失败，详情已写入日志。")
            if result.errors:
                with st.expander("查看错误摘要", expanded=False):
                    for message in result.errors[:20]:
                        st.code(message)
            if result.created_sidecars:
                st.success("回填完成。之后从这些题目组卷导出 Word 时，会优先使用可编辑公式和原图关系。")
            else:
                st.info("没有生成新的侧车文件。可能已经回填过，或当前题目没有可匹配的 DOCX 源文件。")


@st.dialog("批量导入真题", width="large")
def _render_import_dialog(service: QuestionService, raw_papers_dir: Path) -> None:
    from question_bank.importers.batch_importer import infer_metadata_from_filename, ScannedPaper
    local_dialog = get_local_file_dialog()

    header_cols = st.columns([1, 4])
    with header_cols[0]:
        if st.button("关闭", key="qb_import_dialog_close", use_container_width=True):
            st.session_state[IMPORT_DIALOG_OPEN_KEY] = False
            st.rerun()
    with header_cols[1]:
        success_message = st.session_state.get(IMPORT_SUCCESS_MESSAGE_KEY)
        if success_message:
            st.success(str(success_message))

    st.markdown("##### 📁 本地窗口多选/扫描")
    st.caption("您可以点击下方按钮，直接在 Windows 文件窗口多选文件，或者选择包含试卷的文件夹。")
    if not local_dialog.available:
        st.error(f"{local_dialog.message} 请确认本机运行包已包含 Tkinter 依赖后重启应用。")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button(
            "📁 弹出窗口：多选文件 (Ctrl多选)",
            type="primary",
            use_container_width=True,
            key="btn_tk_files",
            disabled=not local_dialog.available,
        ):
            root = None
            try:
                root = local_dialog.tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                file_paths = local_dialog.filedialog.askopenfilenames(
                    title="选择试卷文件 (可按住 Ctrl 键多选)",
                    filetypes=[("试卷文件", "*.docx;*.pdf"), ("Word 文档", "*.docx"), ("PDF 文件", "*.pdf"), ("所有文件", "*.*")]
                )
                if file_paths:
                    scanned_files = [
                        ScannedPaper(
                            source_file=str(Path(p)),
                            file_type=Path(p).suffix.lower().lstrip("."),
                            metadata=infer_metadata_from_filename(Path(p).name)
                        )
                        for p in file_paths
                    ]
                    st.session_state[SCAN_ROWS_KEY] = [_scan_row(item) for item in scanned_files]
                    st.success(f"已成功加载并推断 {len(scanned_files)} 个试卷文件的元数据，请在下方确认或编辑。")
                    st.rerun()
            except Exception as e:
                st.error(f"无法打开文件选择框：{e}")
            finally:
                if root is not None:
                    root.destroy()
    with col2:
        if st.button(
            "📂 弹出窗口：选择一整个文件夹",
            type="primary",
            use_container_width=True,
            key="btn_tk_dir",
            disabled=not local_dialog.available,
        ):
            root = None
            try:
                root = local_dialog.tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                dir_path = local_dialog.filedialog.askdirectory(title="选择包含试卷的文件夹")
                if dir_path:
                    scanned_files = scan_paper_folder(dir_path)
                    st.session_state[SCAN_ROWS_KEY] = [_scan_row(item) for item in scanned_files]
                    st.success(f"已扫描并加载文件夹下 {len(scanned_files)} 个试卷文件，请在下方确认或编辑。")
                    st.rerun()
            except Exception as e:
                st.error(f"无法打开文件夹选择框：{e}")
            finally:
                if root is not None:
                    root.destroy()

    rows = [_normalize_scan_row(row) for row in st.session_state.get(SCAN_ROWS_KEY, [])]
    st.session_state[SCAN_ROWS_KEY] = rows
    if not rows:
        st.caption("💡 提示：使用上方按钮打开本地窗口，选择试卷文件或试卷文件夹后开始载入。")
        return

        st.markdown("#### 待导入试卷列表")
    edited_rows = st.data_editor(
        rows,
        key="qb_scan_editor",
        width="stretch",
        hide_index=True,
        column_config={
            "导入": st.column_config.CheckboxColumn("导入", help="勾选表示纳入本次调试范围、批量修改和导入。"),
            "路径": st.column_config.TextColumn("路径", disabled=True),
            "文件": st.column_config.TextColumn("文件", disabled=True),
            "格式": st.column_config.TextColumn("格式", disabled=True),
        },
    )

    st.caption("系统会优先使用文件名推断的年份、省份、城市、地区、考试类型、年级和学期；你也可以直接在表格里修改。")
    batch_cols = st.columns([1, 1, 1, 1, 1, 1, 1, 1])
    with batch_cols[0]:
        batch_year = st.text_input("批量年份", placeholder="2025")
    with batch_cols[1]:
        batch_province = st.text_input("批量省份", placeholder="广东省")
    with batch_cols[2]:
        batch_city = st.text_input("批量城市", placeholder="深圳市")
    with batch_cols[3]:
        batch_region = st.text_input("批量地区", placeholder="南山区")
    with batch_cols[4]:
        batch_exam_type = st.text_input("批量考试类型", placeholder="期中 / 期末 / 中考")
    with batch_cols[5]:
        batch_grade = st.text_input("批量年级", placeholder="九年级")
    with batch_cols[6]:
        batch_semester = st.text_input("批量学期", placeholder="上学期")
    with batch_cols[7]:
        st.caption(" ")
        apply_metadata = st.button("应用到已勾选")

    action_cols = st.columns([1, 1, 1.1, 1.1])
    with action_cols[0]:
        remove_unchecked = st.button("从扫描列表移除未勾选")
    with action_cols[1]:
        import_requested = st.button("确定导入", type="primary")
    with action_cols[2]:
        tag_after_import = st.checkbox("导入后自动打标签", value=True, key="qb_tag_after_import")
    with action_cols[3]:
        skip_tagged_after_import = st.checkbox("跳过已标注题", value=True, key="qb_skip_tagged_after_import")

    edited_rows = [dict(row) for row in edited_rows]
    if apply_metadata:
        updated_rows = _apply_batch_metadata(
            edited_rows,
            year=batch_year,
            province=batch_province,
            city=batch_city,
            district=batch_region,
            exam_type=batch_exam_type,
            grade=batch_grade,
            semester=batch_semester,
        )
        st.session_state[SCAN_ROWS_KEY] = updated_rows
        st.rerun()

    if remove_unchecked:
        remaining_rows = [row for row in edited_rows if bool(row.get("导入"))]
        removed = len(edited_rows) - len(remaining_rows)
        st.session_state[SCAN_ROWS_KEY] = remaining_rows
        st.success(f"已从本次扫描列表移除 {removed} 个文件；本地文件未删除。")
        st.rerun()

    if import_requested:
        selected_for_import = [_row_to_scanned_paper(row) for row in edited_rows if bool(row.get("导入"))]
        if not selected_for_import:
            st.warning("请先勾选至少一个要导入的文件。")
            return
        try:
            import_result = import_scanned_papers(
                selected_for_import,
                service.db_path,
            )
            st.session_state["qb_last_import_result"] = import_result
            st.session_state[IMPORT_SUCCESS_MESSAGE_KEY] = (
                f"导入完成：已导入 {import_result.imported_papers} 份试卷，新增 {import_result.question_count} 道题。"
            )
            if tag_after_import and import_result.question_count:
                imported_sources = {
                    item.source_file
                    for item in import_result.files
                    if item.status in {"imported", "needs_review", "needs_ocr"}
                }
                imported_questions = [
                    question
                    for question in service.query_questions()
                    if str(question.get("source_file") or "") in imported_sources
                ]
                question_ids = _question_ids_for_tagging(imported_questions, skip_tagged=skip_tagged_after_import)
                if question_ids:
                    max_workers, requests_per_minute = _tagging_runtime_limits()
                    _run_ai_tagging_for_ids(
                        service,
                        question_ids,
                        allow_manual_overwrite=False,
                        max_workers=max_workers,
                        requests_per_minute=requests_per_minute,
                    )
            st.success("导入完成。关闭窗口后可在题目列表查看。")
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Question bank import page failed for selected files")
            st.error(f"导入失败：{exc}")


def _apply_batch_metadata(
    rows: list[dict[str, Any]],
    *,
    year: str,
    province: str,
    city: str,
    district: str,
    exam_type: str,
    grade: str,
    semester: str,
) -> list[dict[str, Any]]:
    replacements = {
        "年份": _cell_text(year),
        "省份": _cell_text(province),
        "城市": _cell_text(city),
        "地区": _cell_text(district),
        "考试类型": _cell_text(exam_type),
        "年级": _cell_text(grade),
        "学期": _cell_text(semester),
    }
    return [
        {
            **row,
            **{key: value for key, value in replacements.items() if value and bool(row.get("导入"))},
        }
        for row in rows
    ]


def _normalize_scan_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    normalized.pop("选择", None)
    normalized.setdefault("导入", True)
    return normalized


def _render_import_result(service: QuestionService) -> None:
    import_result = st.session_state.get("qb_last_import_result")
    if import_result is None:
        return
    metrics = st.columns(5)
    metrics[0].metric("导入试卷", import_result.imported_papers)
    metrics[1].metric("题目数量", import_result.question_count)
    metrics[2].metric("答案匹配", import_result.answer_match_count)
    metrics[3].metric("需复核", import_result.review_count)
    metrics[4].metric("重复跳过", import_result.skipped_duplicate_files)
    if import_result.files:
        st.dataframe(
            [
                {
                    "文件": Path(item.source_file).name,
                    "状态": item.status,
                    "题目": item.question_count,
                    "答案匹配": item.answer_match_count,
                    "需复核": item.review_count,
                    "说明": item.message or "",
                }
                for item in import_result.files
            ],
            width="stretch",
            hide_index=True,
        )
        suspicious_results = [
            (item, _missing_question_numbers_for_import_item(service, item))
            for item in import_result.files
            if item.status in {"imported", "needs_review", "needs_ocr"} and item.question_count not in (0, 20)
        ]
        suspicious_results = [(item, missing) for item, missing in suspicious_results if missing]
        if suspicious_results:
            st.warning("题数异常：部分试卷不是 20 题，请复核导入结果。")
            for item, missing in suspicious_results:
                st.caption(f"{Path(item.source_file).name}：缺失题号 {', '.join(missing)}")
    failed_results = [item for item in import_result.files if item.status == "failed"]
    if failed_results:
        st.warning("部分文件导入失败，已记录日志。")
        for item in failed_results:
            st.caption(f"{Path(item.source_file).name}：{item.message or '未知错误'}")


def _missing_question_numbers_for_import_item(service: QuestionService, item: Any) -> list[str]:
    if int(getattr(item, "question_count", 0) or 0) <= 0:
        return []
    from question_bank.database.schema import connect

    with connect(service.db_path) as conn:
        rows = conn.execute(
            """
            SELECT question_number
            FROM questions
            WHERE source_file = ?
              AND COALESCE(is_deleted, 0) = 0
            """,
            (str(getattr(item, "source_file", "") or ""),),
        ).fetchall()
    numbers = {str(row["question_number"]) for row in rows if str(row["question_number"] or "").isdigit()}
    if not numbers:
        return []
    return [str(number) for number in range(1, 21) if str(number) not in numbers]


def _render_paper_list(service: QuestionService) -> None:
    st.subheader("已入库试卷与管理")
    papers = service.list_papers()
    if not papers:
        st.info("当前题库还没有入库试卷。")
        return

    # Fetch tagging statistics per paper for display
    from question_bank.database.schema import connect
    total_counts = {}
    tagged_counts = {}
    try:
        with connect(service.db_path) as conn:
            # Total questions per paper
            total_counts = {
                row["paper_id"]: row["cnt"]
                for row in conn.execute(
                    "SELECT paper_id, COUNT(*) AS cnt FROM questions WHERE COALESCE(is_deleted, 0) = 0 GROUP BY paper_id"
                ).fetchall()
            }
            # Tagged questions per paper (has at least one valid analysis tag)
            tagged_counts = {
                row["paper_id"]: row["cnt"]
                for row in conn.execute(
                    """
                    SELECT q.paper_id, COUNT(DISTINCT q.id) AS cnt
                    FROM questions q
                    JOIN question_tags t ON t.question_id = q.id
                    WHERE COALESCE(q.is_deleted, 0) = 0
                      AND t.tag_type IN ('knowledge_point', 'method', 'ability', 'model', 'error_type', 'exam_scope')
                      AND COALESCE(t.tag_value, '') <> ''
                    GROUP BY q.paper_id
                    """
                ).fetchall()
            }
    except Exception as e:
        LOGGER.exception("Failed to query paper tagging stats: %s", e)

    rows = []
    for item in papers:
        paper_id = item["id"]
        total = total_counts.get(paper_id, 0)
        tagged = tagged_counts.get(paper_id, 0)
        pct = f"{tagged / total:.0%}" if total > 0 else "0%"
        progress_str = f"📊 {tagged}/{total} ({pct})"
        
        rows.append({
            "选择": False,
            "ID": paper_id,
            "标题": item.get("title") or "",
            "年份": item.get("year") or "",
            "省份": item.get("province") or "",
            "城市": item.get("city") or "",
            "地区": item.get("district") or "",
            "考试类型": item.get("exam_type") or "",
            "年级": item.get("grade") or "",
            "学期": item.get("semester") or "",
            "标签进度": progress_str,
            "本地文件": item.get("source_file") or "",
        })

    edited = st.data_editor(
        rows,
        key="qb_paper_list_editor",
        width="stretch",
        hide_index=True,
        disabled=["ID", "标题", "年份", "省份", "城市", "地区", "考试类型", "年级", "学期", "标签进度", "本地文件"],
        column_config={"选择": st.column_config.CheckboxColumn("选择", help="勾选要进行批量操作的试卷。")},
    )

    selected_ids = [int(row["ID"]) for row in edited if bool(row.get("选择"))]
    
    col1, col2 = st.columns([1, 2], gap="large")
    
    with col1:
        st.markdown("##### 🗑️ 试卷管理")
        st.caption("从题库中移除选中的试卷及所含题目。该操作不会删除本地物理文件。")
        btn_delete = st.button("❌ 批量从题库移除选中试卷", type="secondary", use_container_width=True, key="btn_bulk_delete_papers")
        if btn_delete:
            if not selected_ids:
                st.warning("请先勾选要操作的试卷。")
            else:
                removed = 0
                for pid in selected_ids:
                    if service.delete_paper(pid):
                        removed += 1
                st.success(f"已成功从题库移除 {removed} 份试卷。")
                st.rerun()
                
    with col2:
        st.markdown("##### 🏷️ 批量 AI 打标签")
        st.caption("对勾选试卷下的题目进行批量打标签。默认跳过已标注题目。")
        
        cols = st.columns([1.2, 2])
        with cols[0]:
            skip_tagged = st.checkbox("跳过已标注", value=True, key="qb_paper_tag_skip_tagged_unified")
        with cols[1]:
            st.caption("并发与 RPM 使用左侧“题库打标签大模型 API 配置”。")
            
        btn_tagging = st.button("🚀 开始为选中试卷批量打标签", type="primary", use_container_width=True, key="btn_bulk_tag_papers")
        if btn_tagging:
            if not selected_ids:
                st.warning("请先勾选要操作的试卷。")
            else:
                selected_questions = service.query_questions(paper_ids=selected_ids)
                question_ids = _question_ids_for_tagging(selected_questions, skip_tagged=skip_tagged)
                if not question_ids:
                    st.info("选中的试卷中没有需要打标签的题目。")
                else:
                    max_workers, requests_per_minute = _tagging_runtime_limits()
                    _run_ai_tagging_for_ids(
                        service,
                        question_ids,
                        allow_manual_overwrite=False,
                        max_workers=max_workers,
                        requests_per_minute=requests_per_minute,
                    )
                    st.rerun()


def _render_ai_tag_results(service: QuestionService, questions: list[dict], allow_manual_overwrite: bool) -> None:
    question_by_id = {str(item["id"]): item for item in questions}
    results = st.session_state.get(AI_RESULTS_KEY, {})
    visible_result_ids = [item_id for item_id in results if item_id in question_by_id]
    if not visible_result_ids:
        return

    st.markdown("#### 待确认 AI 标签")
    for item_id in visible_result_ids:
        payload = results[item_id]
        question = question_by_id[item_id]
        label = f"{_source_label(question)} · {_short_text(question.get('question_text'), 40)}"
        with st.expander(label, expanded=False):
            if not payload.get("ok") or payload.get("analysis") is None:
                st.error(f"分析失败：{payload.get('error') or '未知错误'}")
                continue
            if payload.get("mock_mode"):
                st.warning("当前结果来自 mock 模式。若要实测 API，请先确认侧边栏 API 配置已保存，然后清空旧结果重新分析。")
            original_analysis = TagAnalysis.from_dict(payload["analysis"])
            model_name = _cell_text(payload.get("model_name")) or "未知模型"
            quality_status = _cell_text(payload.get("quality_status")) or "complete"
            confidence_pct = f"{original_analysis.confidence * 100:.0f}%"
            st.caption(f"模型：{model_name} · 置信度：{confidence_pct} · 状态：{_quality_status_label(quality_status)}")
            quality_notes = [str(item) for item in (payload.get("quality_notes") or []) if str(item).strip()]
            if quality_notes:
                st.info("；".join(quality_notes[:4]))
            st.markdown("**题干摘要**")
            st.write(question.get("question_text") or "")
            tag_cols = st.columns([1, 1])
            with tag_cols[0]:
                knowledge_points = _tag_multiselect(
                    "知识点",
                    KNOWLEDGE_POINT_OPTIONS,
                    original_analysis.knowledge_points,
                    key=f"qb_ai_knowledge_{item_id}",
                )
                ability_tags = _tag_multiselect(
                    "数学能力",
                    ABILITY_TAG_OPTIONS,
                    original_analysis.ability_tags,
                    key=f"qb_ai_ability_{item_id}",
                )
                error_points = _tag_multiselect(
                    "易错点",
                    tuple(original_analysis.error_prone_points),
                    original_analysis.error_prone_points,
                    key=f"qb_ai_errors_{item_id}",
                )
            with tag_cols[1]:
                method_tags = _tag_multiselect(
                    "思想方法",
                    METHOD_TAG_OPTIONS,
                    original_analysis.method_tags,
                    key=f"qb_ai_methods_{item_id}",
                )
                model_tags = _tag_multiselect(
                    "数学模型",
                    MATH_MODEL_OPTIONS,
                    original_analysis.math_model_tags,
                    key=f"qb_ai_models_{item_id}",
                )
                prerequisite_points = _tag_multiselect(
                    "前置知识",
                    KNOWLEDGE_POINT_OPTIONS,
                    original_analysis.prerequisite_points,
                    key=f"qb_ai_prerequisites_{item_id}",
                )

            meta_cols = st.columns([1, 1, 1])
            with meta_cols[0]:
                difficulty_score = st.number_input(
                    "难度",
                    min_value=1,
                    max_value=10,
                    step=1,
                    value=original_analysis.difficulty,
                    key=f"qb_ai_difficulty_{item_id}",
                )
            with meta_cols[1]:
                teaching_stage = st.text_input(
                    "教学阶段",
                    value=original_analysis.teaching_stage,
                    key=f"qb_ai_stage_{item_id}",
                )
            with meta_cols[2]:
                student_level = st.selectbox(
                    "适合层次",
                    options=_ensure_options(STUDENT_LEVELS, [original_analysis.suitable_student_level]),
                    index=_option_index(_ensure_options(STUDENT_LEVELS, [original_analysis.suitable_student_level]), original_analysis.suitable_student_level),
                    key=f"qb_ai_level_{item_id}",
                )
            chapter_options = _ensure_options(CURRICULUM_CHAPTERS, [original_analysis.textbook_chapter])
            textbook_chapter = st.selectbox(
                "北师大版2024教材章节",
                options=chapter_options,
                index=_option_index(chapter_options, original_analysis.textbook_chapter),
                key=f"qb_ai_chapter_{item_id}",
            )
            reason = st.text_area(
                "AI分析理由 / 教学提示",
                value=original_analysis.reason or "",
                height=90,
                key=f"qb_ai_reason_{item_id}",
            )
            if st.button("保存标签", key=f"qb_ai_save_{item_id}", type="primary"):
                accepted_analysis = TagAnalysis.from_dict(
                    {
                        **original_analysis.to_dict(),
                        "knowledge_points": knowledge_points,
                        "method_tags": method_tags,
                        "ability_tags": ability_tags,
                        "math_model_tags": model_tags,
                        "difficulty": difficulty_score,
                        "error_prone_points": error_points,
                        "prerequisite_points": prerequisite_points,
                        "textbook_chapter": textbook_chapter,
                        "teaching_stage": teaching_stage,
                        "suitable_student_level": student_level,
                        "reason": reason,
                        "confidence": original_analysis.confidence,
                    }
                )
                _save_tag_analysis(
                    service,
                    item_id,
                    original_analysis,
                    accepted_analysis,
                    allow_manual_overwrite,
                    model_name=payload.get("model_name"),
                    confidence=accepted_analysis.confidence,
                )


def _save_tag_analysis(
    service: QuestionService,
    item_id: str,
    original_analysis: TagAnalysis,
    accepted: TagAnalysis,
    allow_manual_overwrite: bool,
    model_name: str | None = None,
    confidence: float | None = None,
) -> None:
    try:
        edited_fields = set()
        for field_name in (
            "knowledge_points",
            "method_tags",
            "ability_tags",
            "math_model_tags",
            "error_prone_points",
            "prerequisite_points",
            "textbook_chapter",
            "teaching_stage",
            "suitable_student_level",
            "reason",
        ):
            if accepted.to_dict().get(field_name) != original_analysis.to_dict().get(field_name):
                edited_fields.add(field_name)
        saved = service.save_tag_analysis(
            int(item_id),
            accepted,
            overwrite_manual=allow_manual_overwrite,
            edited_fields=edited_fields,
            model_name=model_name,
            confidence=confidence,
        )
        if saved:
            st.success("标签已保存。")
        else:
            st.error("题目不存在或已删除，未保存。")
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to save AI tag analysis for question %s", item_id)
        st.error(f"保存标签失败：{exc}")


@st.dialog("编辑题目内容", width="large")
def _edit_question_dialog(service: QuestionService, question_id: int):
    q = service.get_question(question_id)
    if q is None:
        st.error("题目不存在")
        return
        
    edit_number = st.text_input("题号", value=q.get("question_number") or "")
    type_options = ["选择题", "多选题", "填空题", "解答题", "解答题（计算）", "解答题（证明）", "解答题（画图）"]
    current_qtype = q.get("question_type") or "选择题"
    default_idx = type_options.index(current_qtype) if current_qtype in type_options else 0
    edit_type = st.selectbox("题型", type_options, index=default_idx)
    edit_text = st.text_area("题干内容", value=q.get("question_text") or "", height=150)
    edit_answer = st.text_area("参考答案", value=q.get("answer_text") or "", height=100)
    
    from question_bank.models.question import QuestionUpdate
    
    save_cols = st.columns(2)
    with save_cols[0]:
        if st.button("保存修改", type="primary", use_container_width=True):
            try:
                update_obj = QuestionUpdate(
                    paper_id=q.get("paper_id"),
                    question_number=edit_number.strip(),
                    question_type=edit_type,
                    question_text=edit_text.strip(),
                    answer_text=edit_answer.strip() or None,
                    source_file=q.get("source_file"),
                    page_range=q.get("page_range"),
                    image_paths=q.get("image_paths", []),
                    difficulty=q.get("difficulty"),
                    needs_review=bool(q.get("needs_review")),
                    has_images=bool(q.get("has_images")),
                    needs_image_review=bool(q.get("needs_image_review")),
                )
                success = service.update_question(question_id, update_obj)
                if success:
                    st.success("题目修改成功！")
                    st.rerun()
                else:
                    st.error("保存失败")
            except Exception as e:
                st.error(f"保存出错: {e}")
    with save_cols[1]:
        if st.button("取消", use_container_width=True):
            st.rerun()
def _render_local_tagging_api_config() -> None:
    from api_profiles import load_api_profiles, save_api_profiles
    from llm_client import normalize_openai_base_url
    from path_manager import get_path_manager
    import os

    pm = get_path_manager()
    profiles_path = pm.api_profiles_path
    
    profiles = load_api_profiles(profiles_path)
    saved_profile = profiles[-1] if profiles else {}

    # Initialize session state keys for tagging if not present
    if "tagging_api_key_input" not in st.session_state:
        st.session_state.tagging_api_key_input = str(saved_profile.get("tagging_api_key") or os.getenv("QUESTION_BANK_TAGGING_API_KEY", ""))
    if "tagging_base_url_input" not in st.session_state:
        st.session_state.tagging_base_url_input = str(saved_profile.get("tagging_base_url") or os.getenv("QUESTION_BANK_TAGGING_BASE_URL", "https://api.openai.com/v1"))
    if "tagging_model_input" not in st.session_state:
        st.session_state.tagging_model_input = str(saved_profile.get("tagging_model") or os.getenv("QUESTION_BANK_TAGGING_MODEL", "gpt-4o-mini"))
    if "tagging_max_workers_input" not in st.session_state:
        st.session_state.tagging_max_workers_input = int(saved_profile.get("tagging_max_workers", 4))
    if "tagging_requests_per_minute_input" not in st.session_state:
        st.session_state.tagging_requests_per_minute_input = int(saved_profile.get("tagging_requests_per_minute", 1000))
    if "tagging_thinking_input" not in st.session_state:
        st.session_state.tagging_thinking_input = bool(saved_profile.get("tagging_thinking", False))
    
    # Force tagging_enabled_input to True in st.session_state so it is always active
    st.session_state.tagging_enabled_input = True
    if "tagging_review_enabled_input" not in st.session_state:
        st.session_state.tagging_review_enabled_input = bool(saved_profile.get("tagging_review_enabled", False))
    if "tagging_review_api_key_input" not in st.session_state:
        st.session_state.tagging_review_api_key_input = str(saved_profile.get("tagging_review_api_key") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_API_KEY", ""))
    if "tagging_review_base_url_input" not in st.session_state:
        st.session_state.tagging_review_base_url_input = str(saved_profile.get("tagging_review_base_url") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_BASE_URL", st.session_state.tagging_base_url_input))
    if "tagging_review_model_input" not in st.session_state:
        st.session_state.tagging_review_model_input = str(saved_profile.get("tagging_review_model") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_MODEL", ""))

    # Sync variables to process environment for the tagging service to access seamlessly
    os.environ["QUESTION_BANK_TAGGING_API_KEY"] = st.session_state.tagging_api_key_input
    os.environ["QUESTION_BANK_TAGGING_BASE_URL"] = st.session_state.tagging_base_url_input
    os.environ["QUESTION_BANK_TAGGING_MODEL"] = st.session_state.tagging_model_input
    os.environ["QUESTION_BANK_TAGGING_MAX_WORKERS"] = str(st.session_state.tagging_max_workers_input)
    os.environ["QUESTION_BANK_TAGGING_REQUESTS_PER_MINUTE"] = str(st.session_state.tagging_requests_per_minute_input)
    os.environ["QUESTION_BANK_TAGGING_THINKING"] = "1" if st.session_state.tagging_thinking_input else "0"
    review_enabled_now = bool(st.session_state.tagging_review_enabled_input and _cell_text(st.session_state.tagging_review_model_input))
    os.environ["QUESTION_BANK_TAGGING_REVIEW_MODEL"] = st.session_state.tagging_review_model_input if review_enabled_now else ""
    os.environ["QUESTION_BANK_TAGGING_REVIEW_API_KEY"] = st.session_state.tagging_review_api_key_input if review_enabled_now else ""
    os.environ["QUESTION_BANK_TAGGING_REVIEW_BASE_URL"] = st.session_state.tagging_review_base_url_input if review_enabled_now else ""
    st.session_state.tagging_enabled = True

    # Render inside sidebar!
    with st.sidebar:
        st.markdown("#### 🏷️ 题库打标签大模型 API 配置")
        
        # Check if saved profile has key/url/model configured
        config_is_ready = bool(
            _cell_text(st.session_state.get("tagging_api_key_input"))
            and _cell_text(st.session_state.get("tagging_base_url_input"))
            and _cell_text(st.session_state.get("tagging_model_input"))
        )
        if not config_is_ready:
            st.warning("必须先在下方配置并保存打标签 API，才能进行批量打标签。请填写 API Key、Base URL、模型并保存。")
        else:
            st.info("当前已启用专属打标签模型配置；批量打标签会统一使用这里保存的 API、并发和 RPM。")
            
        st.caption("您可以配置性价比高、高并发的第三方大模型（如 gpt-4o-mini, o3-mini 等）来单独进行题库分析。")
        
        tagging_api_key = st.text_input("打标签 API Key", type="password", key="tagging_api_key_input")
        tagging_base_url = st.text_input("打标签 API Base URL", key="tagging_base_url_input")
        tagging_model = st.text_input("打标签模型名称", key="tagging_model_input")
        tagging_max_workers = st.number_input("打标签最大并发数", min_value=1, max_value=64, step=1, key="tagging_max_workers_input")
        tagging_requests_per_minute = st.number_input("打标签 RPM 上限", min_value=1, step=10, key="tagging_requests_per_minute_input")
        tagging_thinking = st.checkbox("开启 Thinking 模式", key="tagging_thinking_input", help="开启后，将启用深度思维链推理，特别适用于 DeepSeek-R1 / o1 / o3-mini 等推理模型，显著提高标签和分析质量。")
        st.divider()
        tagging_review_enabled = st.checkbox("启用低置信度复核模型", key="tagging_review_enabled_input", help="只在主模型低置信或核心标签冲突时调用，不会全量双模型。")
        tagging_review_api_key = st.text_input("复核模型 API Key", type="password", key="tagging_review_api_key_input", disabled=not tagging_review_enabled)
        tagging_review_base_url = st.text_input("复核模型 API Base URL", key="tagging_review_base_url_input", disabled=not tagging_review_enabled)
        tagging_review_model = st.text_input("复核模型名称", placeholder="例如 deepseek-chat / deepseek-reasoner", key="tagging_review_model_input", disabled=not tagging_review_enabled)

        if st.button("💾 保存打标签配置", use_container_width=True, key="save_local_tagging_api_settings", type="primary"):
            # Load fresh profiles to avoid overwriting newer changes
            profiles = load_api_profiles(profiles_path)
            if not profiles:
                profiles = [{"name": "default"}]
            
            # Update last profile
            profiles[-1]["tagging_api_key"] = str(tagging_api_key).strip()
            profiles[-1]["tagging_base_url"] = normalize_openai_base_url(str(tagging_base_url).strip() or "https://api.openai.com/v1")
            profiles[-1]["tagging_model"] = str(tagging_model).strip() or "gpt-4o-mini"
            profiles[-1]["tagging_max_workers"] = int(tagging_max_workers)
            profiles[-1]["tagging_requests_per_minute"] = int(tagging_requests_per_minute)
            profiles[-1]["tagging_thinking"] = bool(tagging_thinking)
            profiles[-1]["tagging_enabled"] = True
            profiles[-1]["tagging_review_enabled"] = bool(tagging_review_enabled)
            profiles[-1]["tagging_review_api_key"] = str(tagging_review_api_key).strip()
            profiles[-1]["tagging_review_base_url"] = normalize_openai_base_url(str(tagging_review_base_url).strip() or str(tagging_base_url).strip() or "https://api.openai.com/v1")
            profiles[-1]["tagging_review_model"] = str(tagging_review_model).strip()

            save_api_profiles(profiles_path, profiles)

            # Sync immediately
            st.session_state.tagging_enabled = True
            st.session_state.tagging_enabled_input = True
            os.environ["QUESTION_BANK_TAGGING_API_KEY"] = str(tagging_api_key).strip()
            os.environ["QUESTION_BANK_TAGGING_BASE_URL"] = normalize_openai_base_url(str(tagging_base_url).strip() or "https://api.openai.com/v1")
            os.environ["QUESTION_BANK_TAGGING_MODEL"] = str(tagging_model).strip() or "gpt-4o-mini"
            os.environ["QUESTION_BANK_TAGGING_MAX_WORKERS"] = str(tagging_max_workers)
            os.environ["QUESTION_BANK_TAGGING_REQUESTS_PER_MINUTE"] = str(tagging_requests_per_minute)
            os.environ["QUESTION_BANK_TAGGING_THINKING"] = "1" if tagging_thinking else "0"
            os.environ["QUESTION_BANK_TAGGING_REVIEW_MODEL"] = str(tagging_review_model).strip() if tagging_review_enabled else ""
            os.environ["QUESTION_BANK_TAGGING_REVIEW_API_KEY"] = str(tagging_review_api_key).strip() if tagging_review_enabled else ""
            os.environ["QUESTION_BANK_TAGGING_REVIEW_BASE_URL"] = normalize_openai_base_url(str(tagging_review_api_key).strip() or str(tagging_base_url).strip() or "https://api.openai.com/v1") if tagging_review_enabled else ""

            st.success("🏷️ 专属打标签 API 配置已保存并立即生效！")
            st.rerun()


def _tagging_config_ready() -> bool:
    return bool(
        st.session_state.get("tagging_enabled")
        and _cell_text(st.session_state.get("tagging_api_key_input"))
        and _cell_text(st.session_state.get("tagging_base_url_input"))
        and _cell_text(st.session_state.get("tagging_model_input"))
    )


def _tagging_runtime_limits() -> tuple[int, int]:
    max_workers = int(st.session_state.get("tagging_max_workers_input", 4) or 4)
    requests_per_minute = int(st.session_state.get("tagging_requests_per_minute_input", 1000) or 1000)
    return max(1, max_workers), max(1, requests_per_minute)


def _warn_missing_tagging_config() -> None:
    st.warning("必须先在左侧配置并保存打标签 API，才能进行批量打标签。")


def _render_pagination_controls(
    *,
    state_key: str,
    current_page: int,
    total_pages: int,
    total_count: int,
    key_prefix: str,
) -> None:
    if total_pages <= 1:
        st.caption(f"第 1 / 1 页 (共 {total_count} 道题)")
        return

    items = _pagination_items(current_page, total_pages)
    cols = st.columns(len(items))
    for index, item in enumerate(items):
        with cols[index]:
            if item == "...":
                st.markdown("<div style='text-align:center; line-height:2.4rem;'>…</div>", unsafe_allow_html=True)
                continue
            target_page, label = item
            disabled = target_page == current_page
            if st.button(label, key=f"{key_prefix}_{label}_{index}", disabled=disabled, use_container_width=True):
                st.session_state[state_key] = target_page
                st.rerun()
    st.caption(f"第 {current_page} / {total_pages} 页 (共 {total_count} 道题)")


def _pagination_items(current_page: int, total_pages: int) -> list[tuple[int, str] | str]:
    page_numbers: list[int]
    if total_pages <= 7:
        page_numbers = list(range(1, total_pages + 1))
    else:
        visible = {1, total_pages, current_page - 1, current_page, current_page + 1}
        if current_page <= 3:
            visible.update({2, 3, 4})
        if current_page >= total_pages - 2:
            visible.update({total_pages - 3, total_pages - 2, total_pages - 1})
        page_numbers = [page for page in sorted(visible) if 1 <= page <= total_pages]

    items: list[tuple[int, str] | str] = [
        (1, "首页"),
        (max(1, current_page - 1), "上一页"),
    ]
    previous_page = 0
    for page in page_numbers:
        if previous_page and page - previous_page > 1:
            items.append("...")
        items.append((page, str(page)))
        previous_page = page
    items.extend(
        [
            (min(total_pages, current_page + 1), "下一页"),
            (total_pages, "尾页"),
        ]
    )
    return items


def _preview_display_settings() -> tuple[PreviewDensity, int]:
    st.session_state.setdefault(PREVIEW_DENSITY_KEY, "紧凑")
    st.session_state.setdefault(PREVIEW_IMAGE_SCALE_KEY, 90)
    density = resolve_preview_density(st.session_state.get(PREVIEW_DENSITY_KEY))
    try:
        image_scale = int(st.session_state.get(PREVIEW_IMAGE_SCALE_KEY, 90) or 90)
    except (TypeError, ValueError):
        image_scale = 90
    return density, max(70, min(130, image_scale))


def _render_preview_display_controls() -> None:
    st.session_state.setdefault(PREVIEW_DENSITY_KEY, "紧凑")
    st.session_state.setdefault(PREVIEW_IMAGE_SCALE_KEY, 90)
    with st.popover("显示设置", use_container_width=True):
        st.radio("预览密度", list(PREVIEW_DENSITY_OPTIONS), horizontal=True, key=PREVIEW_DENSITY_KEY)
        st.slider("图片缩放", min_value=70, max_value=130, step=5, key=PREVIEW_IMAGE_SCALE_KEY)
        st.caption("只调整网页预览的字号和图片显示大小，不改变题目解析和导出内容。")


def _render_questions_v2(service: QuestionService) -> None:
    st.subheader("题目列表")
    
    # 1. Render API config expander at the top of questions list section
    _render_local_tagging_api_config()
    st.write("---")
    
    # 2. Get selected chapter and filter panel (Spans 100% full screen width!)
    selected_chapter = st.session_state.get("qb_curriculum_chapter_filter") or ""
    show_questions, filters = _question_filter_panel(service, selected_chapter)
    if not show_questions:
        st.info("💡 请在上方设置筛选条件，并点击“显示筛选题目”以查看题库题目列表。")
        return

    sort_mode = str(filters.pop("_sort_mode", "综合排序"))
    
    # Query total count for pagination
    total_count = service.count_questions(**filters)
    
    # Paginate
    page_size = 20
    total_pages = max((total_count + page_size - 1) // page_size, 1)
    
    if "qb_current_page" not in st.session_state or st.session_state["qb_current_page"] > total_pages:
        st.session_state["qb_current_page"] = 1
        
    current_page = st.session_state["qb_current_page"]
    _render_pagination_controls(
        state_key="qb_current_page",
        current_page=current_page,
        total_pages=total_pages,
        total_count=total_count,
        key_prefix="qb_top",
    )

    offset = (current_page - 1) * page_size
    questions = service.query_questions(**filters, limit=page_size, offset=offset, sort_mode=sort_mode)
    
    _render_ai_tagging_summary()
    if not questions:
        st.info("当前筛选条件下暂无题目。")
        return

    st.caption(f"当前页显示 {len(questions)} 道题 / 总计 {total_count} 道。")
    st.markdown("##### 批量操作")
    if st.button("❌ 批量软删除当前页题目", type="secondary", use_container_width=True):
        if service.batch_delete_questions([int(q["id"]) for q in questions if q.get("id")]):
            st.success("批量操作成功")
            st.rerun()
    
    st.markdown("#### AI 标签分析")
    st.caption("默认分析并保存当前页筛选出来的题目；没有 OPENAI_API_KEY 时自动使用 mock 模式。")
    ai_cols = st.columns([1.2, 1, 1])
    with ai_cols[0]:
        allow_manual_overwrite = st.checkbox("允许覆盖人工标签", key="qb_allow_manual_tag_overwrite")
    with ai_cols[1]:
        analyze_current = st.button("AI 分析当前页题目并保存", type="secondary")
    with ai_cols[2]:
        st.caption("分析完成后会直接写入题库。")
    max_workers, requests_per_minute = _tagging_runtime_limits()
    clear_ai_results = st.button("清空上次分析摘要", key="qb_clear_ai_results")
    st.caption("并发与 RPM 使用左侧“题库打标签大模型 API 配置”。")
    if clear_ai_results:
        st.session_state.pop(AI_RESULTS_KEY, None)
        st.session_state.pop(LAST_AI_SUMMARY_KEY, None)
        st.session_state.pop(SIMILARITY_REVIEW_KEY, None)
        st.rerun()

    if analyze_current:
        if not questions:
            st.warning("当前没有题目可分析。")
        else:
            plan = build_ai_upload_similarity_plan(questions)
            if plan.review_groups:
                st.session_state[SIMILARITY_REVIEW_KEY] = plan.to_dict()
                st.warning("发现一批中高相似题，请先确认哪些题要上传给 AI 打标签。")
                st.rerun()
            else:
                _run_ai_tagging_for_ids(
                    service,
                    plan.upload_question_ids,
                    allow_manual_overwrite=allow_manual_overwrite,
                    max_workers=int(max_workers),
                    requests_per_minute=int(requests_per_minute),
                    similarity_plan=plan,
                )
                st.rerun()

    confirmed_ids = _render_similarity_review_panel(questions)
    if confirmed_ids is not None:
        plan = SimilarityPlan.from_dict(st.session_state.get(SIMILARITY_REVIEW_KEY, {}))
        _run_ai_tagging_for_ids(
            service,
            confirmed_ids,
            allow_manual_overwrite=allow_manual_overwrite,
            max_workers=int(max_workers),
            requests_per_minute=int(requests_per_minute),
            similarity_plan=plan,
        )
        st.session_state.pop(SIMILARITY_REVIEW_KEY, None)
    _render_filter_summary_popover(questions)
    st.divider()
    preview_cols = st.columns([3.4, 1])
    with preview_cols[0]:
        preview_mode = st.radio("预览视图", ["教师视角 (显示解析与难度)", "学生视角 (最真实的答题排版)"], index=0, horizontal=True, key="qb_preview_view")
    with preview_cols[1]:
        _render_preview_display_controls()
    _render_question_cards(service, questions, offset=offset, preview_mode=preview_mode)
    
    st.divider()
    _render_pagination_controls(
        state_key="qb_current_page",
        current_page=current_page,
        total_pages=total_pages,
        total_count=total_count,
        key_prefix="qb_bottom",
    )
                
    # Deleted questions recovery panel
    st.divider()
    with st.expander("🗑️ 已删除内容恢复 (试卷 / 题目)"):
        deleted_papers = [p for p in service.list_papers(include_deleted=True) if p.get("import_status") == "deleted"]
        deleted_count = service.count_questions(is_deleted=True)
        
        if deleted_papers:
            st.markdown("##### ♻️ 已删除试卷一键恢复")
            st.caption("恢复试卷后，该试卷下的全部题目也将自动恢复可见。")
            for dp in deleted_papers:
                dp_cols = st.columns([3, 1])
                with dp_cols[0]:
                    st.markdown(f"**试卷 ID: {dp['id']} · {dp.get('title') or '未命名试卷'}**")
                    st.caption(f"包含题目数：{dp.get('question_count') or 0} 道")
                with dp_cols[1]:
                    if st.button("一键恢复试卷", key=f"restore_paper_{dp['id']}", type="primary", use_container_width=True):
                        if service.restore_paper(int(dp["id"])):
                            st.success(f"已成功恢复试卷“{dp.get('title')}”及其全部题目！")
                            st.rerun()
            st.divider()
            
        st.markdown("##### ♻️ 已删除单题恢复")
        if deleted_count == 0:
            st.info("当前没有被软删除的独立题目。")
        else:
            cols_action = st.columns([2, 1])
            with cols_action[0]:
                st.write(f"最近被删除的题目 (共 {deleted_count} 道):")
            with cols_action[1]:
                if st.button("♻️ 一键恢复全部单题", key="qb_restore_all_single_questions", use_container_width=True):
                    del_qs = service.query_questions(is_deleted=True, limit=5000)
                    del_ids = [int(q["id"]) for q in del_qs]
                    if del_ids:
                        restored = service.batch_restore_questions(del_ids)
                        st.success(f"已成功一键恢复 {restored} 道题目！")
                        st.rerun()
            
            deleted_qs = service.query_questions(is_deleted=True, limit=50)
            for dq in deleted_qs:
                dq_cols = st.columns([3, 1])
                with dq_cols[0]:
                    st.markdown(f"**ID: {dq['id']} · {_source_label(dq)} · 题型: {dq.get('question_type') or '-'} · 题号: {dq.get('question_number') or '-'}**")
                    st.caption(_short_text(dq.get("question_text"), 100))
                with dq_cols[1]:
                    if st.button("恢复", key=f"restore_q_{dq['id']}", use_container_width=True):
                        if service.restore_question(int(dq["id"])):
                            st.success(f"已成功恢复题目 {dq['id']}")
                            st.rerun()


def _render_similarity_review_panel(questions: list[dict[str, Any]]) -> list[int] | None:
    payload = st.session_state.get(SIMILARITY_REVIEW_KEY)
    if not payload:
        return None
    plan = SimilarityPlan.from_dict(payload)
    question_by_id = {int(item["id"]): item for item in questions if item.get("id")}

    st.warning(
        f"相似题预检：{int(plan.high_threshold * 100)}% 以上已自动只保留代表题；"
        f"{int(plan.review_threshold * 100)}%-{int(plan.high_threshold * 100)}% 的题目需要你确认。"
    )
    if plan.high_duplicate_groups:
        with st.expander(f"已自动跳过 {sum(len(group.question_ids) - 1 for group in plan.high_duplicate_groups)} 道高度相似题", expanded=False):
            for group in plan.high_duplicate_groups:
                st.caption(
                    f"相似度最高 {group.max_similarity:.0%}：保留题目 {group.representative_id}，"
                    f"跳过 {', '.join(str(item) for item in group.question_ids if item != group.representative_id)}"
                )

    selected_ids = list(plan.upload_question_ids)
    st.markdown("#### 中高相似题确认")
    for index, group in enumerate(plan.review_groups, start=1):
        with st.container(border=True):
            st.markdown(f"**相似题组 {index}：最高相似度 {group.max_similarity:.0%}**")
            cols = st.columns(min(len(group.question_ids), 3))
            for col_index, question_id in enumerate(group.question_ids[:3]):
                question = question_by_id.get(question_id)
                with cols[col_index]:
                    st.caption(f"题目 {question_id} · {_source_label(question or {})}")
                    st.write(_short_text((question or {}).get("question_text"), 120))
            if len(group.question_ids) > 3:
                st.caption(f"另有 {len(group.question_ids) - 3} 道同组相似题未展开。")
            choice = st.radio(
                "上传策略",
                options=("只上传代表题", "本组全部上传"),
                index=0,
                horizontal=True,
                key=f"qb_similarity_choice_{index}_{'_'.join(str(item) for item in group.question_ids)}",
            )
            if choice == "只上传代表题":
                selected_ids = [item for item in selected_ids if item not in group.question_ids or item == group.representative_id]

    action_cols = st.columns([1, 1, 2])
    with action_cols[0]:
        if st.button("确认并开始 AI 分析", type="primary", key="qb_similarity_confirm"):
            return selected_ids
    with action_cols[1]:
        if st.button("取消本次分析", key="qb_similarity_cancel"):
            st.session_state.pop(SIMILARITY_REVIEW_KEY, None)
            st.rerun()
    return None


def _question_ids_for_tagging(questions: list[dict[str, Any]], *, skip_tagged: bool) -> list[int]:
    ids: list[int] = []
    for question in questions:
        if skip_tagged and _has_analysis_tags(question):
            continue
        try:
            ids.append(int(question["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return ids


def _has_analysis_tags(question: dict[str, Any]) -> bool:
    return has_complete_analysis_tags(question)


def _ai_result_payload(result) -> dict[str, Any]:
    return {
        "ok": bool(result.ok),
        "mock_mode": bool(result.mock_mode),
        "error": result.error,
        "analysis": result.analysis.to_dict() if result.analysis is not None else None,
        "model_name": result.model_name,
        "quality_status": result.quality_status,
        "quality_notes": list(result.quality_notes or []),
    }


def _quality_status_label(status: str) -> str:
    return {
        "complete": "可自动保存",
        "low_confidence": "低置信待确认",
        "invalid": "不完整待确认",
        "conflict": "标签冲突待确认",
    }.get(str(status or ""), str(status or "未知"))


def _run_ai_tagging_for_ids(
    service: QuestionService,
    question_ids: list[int],
    *,
    allow_manual_overwrite: bool,
    max_workers: int,
    requests_per_minute: int,
    similarity_plan: SimilarityPlan | None = None,
) -> None:
    if not question_ids:
        st.warning("相似题过滤后没有需要上传 AI 的题目。")
        return
    contexts: dict[int, TaggingContext] = {}
    saved_ids: list[int] = []
    failed_items: list[str] = []
    pending_results = dict(st.session_state.get(AI_RESULTS_KEY, {}))
    reused_count = 0
    for selected_id in question_ids:
        question = service.get_question(selected_id)
        if question is None:
            continue
        duplicate = service.find_exact_duplicate_tag_analysis(selected_id)
        if duplicate is not None:
            analysis, model_name = duplicate
            try:
                saved = service.save_tag_analysis(
                    selected_id,
                    analysis,
                    overwrite_manual=allow_manual_overwrite,
                    model_name=model_name,
                    confidence=analysis.confidence,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to reuse duplicate AI tag analysis for question %s", selected_id)
                failed_items.append(f"题目 {selected_id}：复用完整标签失败 {exc}")
            else:
                if saved:
                    saved_ids.append(selected_id)
                    reused_count += 1
                    pending_results.pop(str(selected_id), None)
                    continue
        contexts[selected_id] = _tagging_context(question, service)
    if not contexts:
        if saved_ids or failed_items:
            st.session_state[AI_RESULTS_KEY] = pending_results
            st.session_state[LAST_AI_SUMMARY_KEY] = {
                "total": len(question_ids),
                "saved": len(saved_ids),
                "reviewed": 0,
                "pending": 0,
                "reused": reused_count,
                "failed": failed_items,
                "mock_mode": False,
                "skipped_duplicates": _similarity_skipped_notes(similarity_plan, []),
            }
            st.success(f"已复用已有完整标签 {reused_count} 道题。")
        else:
            st.warning("未找到可分析的题目。")
        return
    if not _tagging_config_ready():
        st.session_state[AI_RESULTS_KEY] = pending_results
        st.session_state[LAST_AI_SUMMARY_KEY] = {
            "total": len(question_ids),
            "saved": len(saved_ids),
            "reviewed": 0,
            "pending": 0,
            "reused": reused_count,
            "failed": failed_items,
            "mock_mode": False,
            "skipped_duplicates": _similarity_skipped_notes(similarity_plan, list(contexts)) if similarity_plan else [],
        }
        _warn_missing_tagging_config()
        return

    tagging_service = AITaggingService()
    progress_bar = st.progress(0, text=f"AI 打标签进度：0/{len(contexts)}")
    status_box = st.empty()
    reviewed_count = 0
    pending_count = 0

    def _handle_progress(done: int, total: int, question_id: int, result) -> None:
        nonlocal reviewed_count, pending_count
        progress_bar.progress(done / max(total, 1), text=f"AI 打标签进度：{done}/{total}")
        if result.model_name and "+" in str(result.model_name):
            reviewed_count += 1
        if is_auto_saveable_result(result):
            try:
                saved = service.save_tag_analysis(
                    question_id,
                    result.analysis,
                    overwrite_manual=allow_manual_overwrite,
                    model_name=result.model_name,
                    confidence=result.analysis.confidence if result.analysis is not None else None,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Failed to auto-save AI tag analysis for question %s", question_id)
                failed_items.append(f"题目 {question_id}：保存失败 {exc}")
                status_box.caption(f"已完成 {done}/{total}，题目 {question_id} 保存失败。")
                return
            if saved:
                saved_ids.append(question_id)
                pending_results.pop(str(question_id), None)
                status_box.caption(f"已完成 {done}/{total}，刚保存题目 {question_id}。")
            else:
                failed_items.append(f"题目 {question_id}：题目不存在或已删除")
                status_box.caption(f"已完成 {done}/{total}，题目 {question_id} 未保存。")
        elif result.ok and result.analysis is not None:
            pending_results[str(question_id)] = _ai_result_payload(result)
            pending_count += 1
            notes = "；".join(str(item) for item in (result.quality_notes or [])[:2])
            status_box.caption(f"已完成 {done}/{total}，题目 {question_id} 进入待确认（{result.quality_status}{'：' + notes if notes else ''}）。")
        else:
            failed_items.append(f"题目 {question_id}：{result.error or 'AI 分析失败'}")
            status_box.caption(f"已完成 {done}/{total}，题目 {question_id} 分析失败。")

    tagging_service.analyze_questions(
        contexts,
        max_workers=max_workers,
        requests_per_minute=requests_per_minute,
        progress_callback=_handle_progress,
    )
    skipped_duplicates = _similarity_skipped_notes(similarity_plan, list(contexts)) if similarity_plan else []
    st.session_state[AI_RESULTS_KEY] = pending_results
    st.session_state[LAST_AI_SUMMARY_KEY] = {
        "total": len(question_ids),
        "saved": len(saved_ids),
        "reviewed": reviewed_count,
        "pending": pending_count,
        "reused": reused_count,
        "failed": failed_items,
        "mock_mode": tagging_service.mock_mode,
        "skipped_duplicates": skipped_duplicates,
    }


def _similarity_skipped_notes(plan: SimilarityPlan | None, analyzed_ids: list[int]) -> list[str]:
    if plan is None:
        return []
    notes: list[str] = []
    uploaded = set(analyzed_ids)
    for group in [*plan.high_duplicate_groups, *plan.review_groups]:
        skipped = [item for item in group.question_ids if item not in uploaded]
        if skipped:
            notes.append(
                f"相似度 {group.max_similarity:.0%}：保留题目 {group.representative_id}，跳过 {', '.join(str(item) for item in skipped)}"
            )
    return notes


def _question_filter_panel(service: QuestionService, selected_chapter: str) -> tuple[bool, dict[str, Any]]:
    st.markdown(
        """
        <style>
        div[data-testid="stVerticalBlock"] .qb-filter-panel {
            border: 1px solid #e5edf5;
            background: #ffffff;
            padding: 14px 18px 10px;
            margin: 4px 0 16px;
            box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04);
        }
        .qb-filter-title {
            font-size: 0.95rem;
            font-weight: 700;
            color: #334155;
            margin-bottom: 8px;
        }
        </style>
        <div class="qb-filter-panel"><div class="qb-filter-title">筛选题目</div></div>
        """,
        unsafe_allow_html=True,
    )
    all_questions = service.query_questions()

    question_type_options = _options_from_questions(all_questions, "question_type")
    year_options = _options_from_questions(all_questions, "year", reverse=True)
    exam_type_options = _options_from_questions(all_questions, "exam_type")

    # 1. 题型
    selected_question_type_label = _single_filter_row(
        "题型",
        ["全部", "选择题", "多选题", "填空题", "解答题", "解答题（计算）", "解答题（证明）", "解答题（画图）", *[x for x in question_type_options if x not in ("选择题", "多选题", "填空题", "解答题", "解答题（计算）", "解答题（证明）", "解答题（画图）", "未知")]],
        key="qb_filter_question_type",
    )
    # 2. 难度
    selected_difficulty_label = _single_filter_row(
        "难度",
        ["全部", "易", "较易", "中档", "较难", "难"],
        key="qb_filter_difficulty",
    )
    # 3. 是否打标签
    selected_tag_status = _single_filter_row(
        "是否打标签",
        ["全部", "已打标签", "未打标签"],
        key="qb_filter_tag_status",
    )
    # 4. 来源
    selected_exam_types = _single_value_filter_row(
        "来源",
        ["全部", "期末", "期中", "阶段", "月考", "中考", "模拟", *[x for x in exam_type_options if x not in ("期末", "期中", "阶段", "月考", "中考", "模拟")]],
        key="qb_filter_exam_type",
    )
    # 5. 年份
    selected_years = _single_value_filter_row(
        "年份",
        ["全部", *year_options[:8]],
        key="qb_filter_year",
    )
    # 6. 排序
    sort_mode = _single_filter_row(
         "排序",
         ["综合排序", "题库新增", "试题难度", "考频排序"],
         key="qb_filter_sort",
     )

    # Bottom layout
    bottom_cols = st.columns([1.8, 1.8, 1.8, 1.0, 1.0])
    with bottom_cols[0]:
        chapter_opts = ["全部", *CURRICULUM_CHAPTERS]
        current_ch = st.session_state.get("qb_curriculum_chapter_filter") or "全部"
        ch_idx = chapter_opts.index(current_ch) if current_ch in chapter_opts else 0
        selected_ch = st.selectbox("按教材章节筛选", chapter_opts, index=ch_idx, key="qb_filter_curriculum_chapter")
        if selected_ch == "全部":
            st.session_state["qb_curriculum_chapter_filter"] = ""
        else:
            st.session_state["qb_curriculum_chapter_filter"] = selected_ch
    with bottom_cols[1]:
        papers = service.list_papers()
        paper_names = ["全部"]
        paper_id_map = {}
        for p in papers:
            name = f"{p.get('year') or ''} {p.get('title') or ''}".strip() or f"试卷 {p['id']}"
            paper_names.append(name)
            paper_id_map[name] = p["id"]
        selected_paper_name = st.selectbox("按试卷筛选", paper_names, key="qb_filter_paper")
        selected_paper_id = paper_id_map.get(selected_paper_name)
    with bottom_cols[2]:
        keyword_filter = st.text_input("关键词", placeholder="输入试题关键词", key="qb_filter_keyword")
    with bottom_cols[3]:
        st.caption(" ")
        if st.button("显示筛选题目", type="primary", key="qb_apply_question_filters", use_container_width=True):
            st.session_state[SHOW_FILTERED_QUESTIONS_KEY] = True
            st.rerun()
    with bottom_cols[4]:
        st.caption(" ")
        if st.button("隐藏题目", key="qb_hide_question_results", use_container_width=True):
            st.session_state[SHOW_FILTERED_QUESTIONS_KEY] = False
            st.rerun()

    selected_tags: dict[str, list[str]] = {}
    if selected_chapter:
        selected_tags["exam_scope"] = [selected_chapter]

    question_types = _question_types_for_label(selected_question_type_label, question_type_options)
    filters: dict[str, Any] = {
        "keyword": keyword_filter or None,
        "difficulty_range": _difficulty_range_for_label(selected_difficulty_label),
        "question_types": question_types or None,
        "years": [selected_years] if selected_years else None,
        "exam_types": [selected_exam_types] if selected_exam_types else None,
        "paper_ids": [selected_paper_id] if selected_paper_id is not None else None,
        "tag_filters": selected_tags or None,
        "tag_status": selected_tag_status,
        "_sort_mode": sort_mode,
    }
    return bool(st.session_state.get(SHOW_FILTERED_QUESTIONS_KEY, False)), filters


def _render_question_bank_overview(service: QuestionService) -> None:
    stats = _question_bank_overview_stats(service)
    st.markdown("#### 题库总览")
    metric_cols = st.columns(4)
    metric_cols[0].metric("入库试卷", stats["paper_count"])
    metric_cols[1].metric("题目总数", stats["total_questions"])
    metric_cols[2].metric("已打标签", stats["tagged_questions"])
    metric_cols[3].metric("待打标签", stats["untagged_questions"])
    if stats["total_questions"]:
        rate = stats["tagged_questions"] / max(stats["total_questions"], 1)
        st.progress(rate, text=f"标签覆盖率：{rate:.0%}")
    st.info("先在上方选择筛选条件并点击“显示筛选题目”，即可展开题目列表；也可以在左侧教材树选择章节。")


def _question_bank_overview_stats(service: QuestionService) -> dict[str, int]:
    if hasattr(service, "overview_stats"):
        try:
            return service.overview_stats()
        except Exception:  # noqa: BLE001
            LOGGER.exception("Question bank overview_stats failed; falling back to query-based stats")
    questions = service.query_questions()
    tagged = sum(1 for question in questions if _has_analysis_tags(question))
    papers = service.list_papers()
    return {
        "total_questions": len(questions),
        "tagged_questions": tagged,
        "untagged_questions": max(len(questions) - tagged, 0),
        "paper_count": len(papers),
    }


def _curriculum_tree_filter() -> str:
    selected = str(st.session_state.get("qb_curriculum_chapter_filter") or "")
    st.markdown("#### 教材章节")
    if st.button("全部章节", key="qb_curriculum_all", use_container_width=True, type="primary" if not selected else "secondary"):
        st.session_state["qb_curriculum_chapter_filter"] = ""
        st.rerun()

    groups = _curriculum_chapter_groups()
    for volume, chapters in groups.items():
        expanded = bool(selected and selected in chapters)
        with st.expander(volume, expanded=expanded):
            for chapter in chapters:
                chapter_label = _short_chapter_label(chapter)
                button_type = "primary" if chapter == selected else "secondary"
                if st.button(chapter_label, key=f"qb_curriculum_{safe_filename_fragment(chapter, 'chapter')}", use_container_width=True, type=button_type):
                    st.session_state["qb_curriculum_chapter_filter"] = chapter
                    st.rerun()
    if selected:
        st.caption(f"当前：{selected}")
    return selected


def _curriculum_chapter_groups() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for chapter in CURRICULUM_CHAPTERS:
        text = _cell_text(chapter)
        parts = text.split(" ", 1)
        volume = parts[0] if parts else text
        groups.setdefault(volume, []).append(text)
    return groups


def _short_chapter_label(chapter: str) -> str:
    text = _cell_text(chapter)
    parts = text.split(" ", 1)
    return parts[1] if len(parts) > 1 else text


def _single_filter_row(label: str, options: list[str], *, key: str) -> str:
    normalized = _unique_options(options)
    value = st.pills(label, normalized, default=normalized[0], key=key)
    return str(value or normalized[0])


def _single_value_filter_row(label: str, options: list[str], *, key: str) -> str:
    normalized = _unique_options(options)
    value = st.pills(label, normalized, default=normalized[0], key=key)
    text = str(value or normalized[0])
    return "" if text == "全部" else text


def _unique_options(options: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in options:
        text = _cell_text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result or ["全部"]


def _options_from_questions(questions: list[dict[str, Any]], field: str, *, reverse: bool = False) -> list[str]:
    values = [_cell_text(item.get(field)) for item in questions]
    values = [value for value in values if value]
    return sorted(set(values), reverse=reverse)


def _difficulty_range_for_label(label: str) -> tuple[int, int] | None:
    return {
        "易": (1, 2),
        "较易": (3, 4),
        "中档": (5, 6),
        "较难": (7, 8),
        "难": (9, 10),
    }.get(str(label or ""))


def _question_types_for_label(label: str, raw_options: list[str]) -> list[str]:
    mapping = {
        "选择题": ["choice", "single_choice", "选择题"],
        "多选题": ["multiple_choice", "multi_choice", "多选题"],
        "填空题": ["fill_blank", "blank", "填空题"],
        "解答题": ["solution", "calculation", "proof", "comprehensive", "general_solution", "解答题", "解答题（计算）", "解答题（证明）", "解答题（画图）"],
    }
    text = str(label or "")
    if text == "全部":
        return []
    return [value for value in mapping.get(text, [text]) if value in raw_options or value == text]


def _sort_question_results(questions: list[dict[str, Any]], sort_mode: str) -> list[dict[str, Any]]:
    if sort_mode == "试题难度":
        return sorted(questions, key=lambda item: _numeric_value(item.get("difficulty")), reverse=True)
    if sort_mode == "题库新增":
        return sorted(questions, key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return questions


def _numeric_value(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _frequency_badge_html(metrics: FrequencyMetrics) -> str:
    summary = frequency_summary(metrics)
    if not summary:
        return ""
    return (
        '<span class="qb-badge qb-badge-medium" title="考频仅统计期中、期末和中考">'
        f"{html.escape(summary)}</span>"
    )


def _render_ai_tagging_summary() -> None:
    summary = st.session_state.get(LAST_AI_SUMMARY_KEY)
    if not summary:
        return
    failed_items = summary.get("failed") or []
    skipped_duplicates = summary.get("skipped_duplicates") or []
    saved = int(summary.get("saved") or 0)
    total = int(summary.get("total") or 0)
    reviewed = int(summary.get("reviewed") or 0)
    pending = int(summary.get("pending") or 0)
    reused = int(summary.get("reused") or 0)
    mode_note = "mock 模式" if summary.get("mock_mode") else "API 模式"
    summary_text = f"已自动保存 {saved}/{total}（{mode_note}），复用 {reused}，二审 {reviewed}，待确认 {pending}"
    if failed_items:
        with st.expander(f"上次 AI 打标签：{summary_text}，有 {len(failed_items)} 条失败", expanded=False):
            for item in failed_items[:30]:
                st.caption(str(item))
            if len(failed_items) > 30:
                st.caption(f"其余 {len(failed_items) - 30} 条失败已省略。")
    else:
        st.success(f"上次 AI 打标签：{summary_text}。")
    if skipped_duplicates:
        with st.expander(f"相似题自动跳过：{len(skipped_duplicates)} 组", expanded=False):
            for item in skipped_duplicates:
                st.caption(str(item))


def _render_basket_panel(service: QuestionService, *, compact: bool) -> None:
    basket_ids = _basket_ids()
    if not compact:
        toolbar_cols = st.columns([4, 1])
        with toolbar_cols[1]:
            if st.button(f"🧺 试题篮 {len(basket_ids)}", key="qb_open_basket_panel_empty", use_container_width=True):
                st.session_state[BASKET_PANEL_KEY] = True
                st.rerun()
        if not st.session_state.get(BASKET_PANEL_KEY):
            return
    with st.container(border=True):
        panel_cols = st.columns([2, 1])
        with panel_cols[0]:
            st.markdown(f"#### 试题篮（{len(basket_ids)}）")
        with panel_cols[1]:
            if st.button("收起", key="qb_close_basket_panel", use_container_width=True):
                st.session_state[BASKET_PANEL_KEY] = False
                st.rerun()
        if not basket_ids:
            st.info("还没有加入题目。")
            return
        questions = [service.get_question(question_id) for question_id in basket_ids]
        questions = [question for question in questions if question is not None]
        for question in questions:
            st.markdown(f"**{_source_label(question)}**")
            st.caption(_short_text(question.get("question_text"), 90))
            cols = st.columns(2)
            with cols[0]:
                if st.button("移除", key=f"qb_panel_remove_{question['id']}", use_container_width=True):
                    _remove_from_basket(int(question["id"]))
                    st.rerun()
            with cols[1]:
                st.caption(f"题号：{question.get('question_number') or '-'}")
            st.divider()
        if st.button("进入组卷页", type="primary", key="qb_go_assembly", use_container_width=True):
            _go_to_assembly_composition()


def _go_to_assembly_composition() -> None:
    basket_ids = _basket_ids()
    st.session_state["qb_assembly_order"] = basket_ids
    st.session_state["assembly_page"] = "composition"
    st.switch_page("pages/组卷.py")


def _basket_ids() -> list[int]:
    values = st.session_state.setdefault(BASKET_KEY, [])
    deduped: list[int] = []
    for value in values:
        try:
            question_id = int(value)
        except (TypeError, ValueError):
            continue
        if question_id not in deduped:
            deduped.append(question_id)
    st.session_state[BASKET_KEY] = deduped
    return deduped


def _sync_basket_query_params() -> None:
    should_clear_query = False
    basket_state = _query_param("qb_basket")
    if basket_state == "open":
        st.session_state[BASKET_PANEL_KEY] = True
    elif basket_state == "closed":
        st.session_state[BASKET_PANEL_KEY] = False

    remove_id = _query_param("qb_remove")
    if remove_id:
        try:
            _remove_from_basket(int(remove_id))
            st.session_state[BASKET_PANEL_KEY] = True
            should_clear_query = True
        except ValueError:
            pass

    if _query_param("qb_clear"):
        _clear_basket()
        st.session_state[BASKET_PANEL_KEY] = True
        should_clear_query = True

    if _query_param("qb_go") == "assembly":
        _go_to_assembly_composition()
    if should_clear_query:
        st.query_params.clear()


def _query_param(name: str) -> str:
    value = st.query_params.get(name, "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _render_sidebar_basket(service: QuestionService) -> None:
    basket_ids = _basket_ids()
    with st.sidebar:
        st.markdown("### 🧺 试题篮")
        if not basket_ids:
            st.info("尚未选定试题。在下方题目列表点击“加入试题篮”进行选题。")
            return
        
        st.success(f"已选取 {len(basket_ids)} 道题目")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🧹 一键清空", key="sidebar_clear_basket", use_container_width=True):
                _clear_basket()
                st.success("已清空试题篮！")
                st.rerun()
        with col2:
            if st.button("🚀 开始组卷", key="sidebar_go_assembly", type="primary", use_container_width=True):
                _go_to_assembly_composition()
                
        st.divider()
        questions = [service.get_question(qid) for qid in basket_ids]
        questions = [q for q in questions if q is not None]
        for idx, q in enumerate(questions, start=1):
            with st.container(border=True):
                st.markdown(f"**第 {idx} 题 · {_source_label(q)}**")
                st.caption(_short_text(q.get("question_text"), 60))
                if st.button("移除此题", key=f"sidebar_remove_{q['id']}", use_container_width=True):
                    _remove_from_basket(int(q["id"]))
                    st.rerun()



def _render_basket_launcher(key: str) -> bool:
    count = len(_basket_ids())
    st.markdown(
        f"""
        <div style="display:flex; justify-content:flex-end; margin-bottom:0.25rem;">
          <div style="position:relative; width:3.25rem; height:3.25rem;">
            <div style="
              width:3.25rem; height:3.25rem; border-radius:999px;
              border:1px solid rgba(49, 51, 63, 0.18);
              display:flex; align-items:center; justify-content:center;
              font-size:1.45rem; background:#fff;
              box-shadow:0 2px 10px rgba(49, 51, 63, 0.08);">🧺</div>
            <div style="
              position:absolute; right:-0.15rem; top:-0.15rem;
              min-width:1.35rem; height:1.35rem; padding:0 0.3rem;
              border-radius:999px; background:#ef4444; color:#fff;
              font-size:0.8rem; line-height:1.35rem; text-align:center;
              font-weight:700;">{count}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    return st.button("打开试题篮", key=key, use_container_width=True)


def _add_to_basket(question_id: int) -> None:
    basket_ids = _basket_ids()
    if question_id not in basket_ids:
        basket_ids.append(question_id)
    st.session_state[BASKET_KEY] = basket_ids


def _remove_from_basket(question_id: int) -> None:
    st.session_state[BASKET_KEY] = [item for item in _basket_ids() if item != int(question_id)]


def _clear_basket() -> None:
    st.session_state[BASKET_KEY] = []
    st.session_state["qb_assembly_order"] = []


def _render_questions(service: QuestionService) -> None:
    st.subheader("题目列表")
    filter_cols = st.columns([2, 1.2])
    with filter_cols[0]:
        keyword_filter = st.text_input("按关键词筛选", key="qb_keyword_filter")
    with filter_cols[1]:
        difficulty_range = st.slider("难度区间", min_value=1, max_value=10, value=(1, 10), step=1)
    questions = service.query_questions(
        keyword=keyword_filter or None,
        difficulty_range=difficulty_range if difficulty_range != (1, 10) else None,
    )
    if not questions:
        st.info("当前筛选条件下暂无题目。")
        return

    st.markdown("#### AI 标签分析")
    st.caption("默认勾选当前筛选结果里的全部题目；如需控制 API 调用量，可以先取消不想分析的行。没有 OPENAI_API_KEY 时自动使用 mock 模式。")
    ai_cols = st.columns([1.4, 1])
    with ai_cols[0]:
        allow_manual_overwrite = st.checkbox("允许覆盖人工标签", key="qb_allow_manual_tag_overwrite")
    with ai_cols[1]:
        analyze_selected = st.button("AI 分析选中题目", type="secondary")
    max_workers, requests_per_minute = _tagging_runtime_limits()
    clear_ai_results = st.button("清空待确认结果", key="qb_clear_ai_results")
    st.caption("并发与 RPM 使用左侧“题库打标签大模型 API 配置”。")
    if clear_ai_results:
        st.session_state[AI_RESULTS_KEY] = {}
        st.rerun()

    selection_rows = [
        {
            "分析": True,
            "ID": item["id"],
            "来源": _source_label(item),
            "题干摘要": _short_text(item.get("question_text"), 70),
            "难度": item.get("difficulty") or "",
            "标签状态": _tag_status(item),
        }
        for item in questions
    ]
    edited_selection = st.data_editor(
        selection_rows,
        key="qb_ai_selection_editor",
        width="stretch",
        hide_index=True,
        disabled=["ID", "来源", "题干摘要", "难度", "标签状态"],
        column_config={"分析": st.column_config.CheckboxColumn("分析")},
    )
    selected_question_ids = [int(row["ID"]) for row in edited_selection if bool(row.get("分析"))]
    if analyze_selected:
        if not selected_question_ids:
            st.warning("请先勾选至少一道题目。")
        elif not _tagging_config_ready():
            _warn_missing_tagging_config()
        else:
            tagging_service = AITaggingService()
            results = dict(st.session_state.get(AI_RESULTS_KEY, {}))
            contexts: dict[int, TaggingContext] = {}
            for selected_id in selected_question_ids:
                question = service.get_question(selected_id)
                if question is not None:
                    contexts[selected_id] = _tagging_context(question, service)
            st.info(
                f"开始批量分析 {len(contexts)} 道题，"
                f"并发 {int(max_workers)}，每分钟请求上限 {int(requests_per_minute)}。"
            )
            batch_results = tagging_service.analyze_questions(
                contexts,
                max_workers=int(max_workers),
                requests_per_minute=int(requests_per_minute),
            )
            for selected_id, result in batch_results.items():
                results[str(selected_id)] = _ai_result_payload(result)
            st.session_state[AI_RESULTS_KEY] = results

    _render_ai_tag_results(service, questions, allow_manual_overwrite)

    _render_filter_summary_popover(questions)
    _render_question_cards(service, questions)


def _source_label(item: dict[str, Any]) -> str:
    parts = [
        _cell_text(item.get("year")),
        _cell_text(item.get("district")),
        _cell_text(item.get("exam_type")),
    ]
    inferred = " ".join(part for part in parts if part)
    return inferred or _cell_text(item.get("paper_title")) or Path(_cell_text(item.get("source_file"))).name or "未知来源"


def _render_filter_summary_popover(questions: list[dict]) -> None:
    right_cols = st.columns([4, 1])
    with right_cols[1]:
        with st.popover(f"浏览筛选结果（{len(questions)}）", use_container_width=True):
            st.dataframe(
                [
                    {
                        "来源": _source_label(item),
                        "题型": item.get("question_type") or "",
                        "难度": item.get("difficulty") or "",
                        "标签状态": _tag_status(item),
                    }
                    for item in questions
                ],
                width="stretch",
                hide_index=True,
            )


def _render_question_cards(
    service: QuestionService,
    questions: list[dict],
    *,
    offset: int = 0,
    preview_mode: str = "教师视角 (显示解析与难度)",
) -> None:
    st.markdown("#### 题目详情")
    frequencies = QuestionFrequencyService(service.db_path).metrics_for_questions(
        [int(item["id"]) for item in questions]
    )
    for order, item in enumerate(questions, start=1):
        _render_premium_question_card_qb(
            order + offset,
            item,
            preview_mode,
            service,
            frequency=frequencies.get(int(item["id"])),
        )
        st.divider()


def _render_premium_question_card_qb(
    index: int,
    question: dict[str, Any],
    preview_mode: str,
    service: QuestionService,
    *,
    frequency: FrequencyMetrics | None = None,
) -> None:
    is_teacher = "教师" in preview_mode
    q_id = int(question["id"])
    density, image_scale = _preview_display_settings()
    frequency = frequency or FrequencyMetrics(available=False)
    
    # 1. Action columns in header (Outside A4 card, to look crisp)
    header_cols = st.columns([5.0, 1.0])
    with header_cols[0]:
        # Morandi badges
        badges_html = ""
        if is_teacher:
            diff_val = 0
            try:
                diff_val = float(question.get("difficulty") or 0)
            except ValueError:
                pass
            if diff_val > 0:
                if diff_val <= 3:
                    badge_class = "qb-badge qb-badge-easy"
                    badge_label = f"易 (难度 {diff_val:.1f})"
                elif diff_val <= 7:
                    badge_class = "qb-badge qb-badge-medium"
                    badge_label = f"中 (难度 {diff_val:.1f})"
                else:
                    badge_class = "qb-badge qb-badge-hard"
                    badge_label = f"难 (难度 {diff_val:.1f})"
                badges_html += f'<span class="{badge_class}">{badge_label}</span>'
                
            badges_html += _frequency_badge_html(frequency)
                
        source_label = html.escape(_source_label(question))
        st.markdown(f'<div class="qb-paper-header" style="border:none; margin:0; padding:0;"><span class="qb-paper-title-tag">第 {index} 题 · {source_label}</span><div>{badges_html}</div></div>', unsafe_allow_html=True)
    
    with header_cols[1]:
        if st.button("📝 编辑内容", key=f"qb_edit_btn_{q_id}", use_container_width=True):
            _edit_question_dialog(service, q_id)
            
    # 2. Card body (White paper layout inside native Streamlit container with border - FULL WIDTH!)
    with st.container(border=True):
        # Render question text at 100% full width
        q_text = question.get("question_text") or ""
        q_rich = _safe_html_format(IMAGE_MARKER_PATTERN.sub("", q_text).strip())
        st.markdown(
            f'<div class="qb-rich-text" style="line-height: {density.line_height}; font-size: {density.body_font_rem:.2f}rem; color: #0f172a; font-family: \'Times New Roman\', SimSun, serif; width: 100%;">{q_rich}</div>',
            unsafe_allow_html=True
        )
        
        # Render images
        image_paths = _dedupe_paths(
            [
                *(question.get("image_paths") or []),
                *_image_paths_from_text(question.get("question_text") or ""),
            ]
        )
        if image_paths:
            _render_images(image_paths, image_scale_percent=image_scale)
        elif question.get("has_images") or question.get("needs_image_review"):
            st.info("原文件包含图片，但暂未能精确绑定到本题。请打开本地原卷对照复核。")
            
        if is_teacher:
            with st.expander("🔑 查看参考答案与解析", expanded=False):
                ans_text = question.get("answer_text") or "暂无填写的参考答案"
                ans_rich = _safe_html_format(IMAGE_MARKER_PATTERN.sub("", ans_text).strip())
                
                teacher_box_html = f"""<div class="qb-teacher-box" style="margin-top: 5px; width: 100%; font-size: {density.teacher_box_font_rem:.2f}rem;">
<div class="qb-teacher-title">🔑 教师参考答案</div>
<div class="qb-rich-text" style="font-size: {density.answer_font_rem:.2f}rem; line-height: {density.line_height}; color: #1e3a8a; margin-bottom: 8px;">{ans_rich}</div>
</div>"""
                st.markdown(teacher_box_html, unsafe_allow_html=True)
                
                ans_images = _image_paths_from_text(ans_text)
                if ans_images:
                    _render_images(ans_images, image_scale_percent=image_scale)
                    
        # Elegant separator between question body and metadata footer
        st.markdown(f'<div style="border-top: 1px dashed #cbd5e1; margin: {density.separator_margin_px}px 0;"></div>', unsafe_allow_html=True)
        
        # Horizontal Footer (3-column premium layout)
        col_attr, col_tags_footer, col_reason = st.columns([1.0, 1.8, 1.2], gap="medium")
        
        with col_attr:
            st.markdown('<span style="font-size: 0.85rem; color: #64748b; font-weight: 600; display: block; margin-bottom: 6px;">📝 试题属性</span>', unsafe_allow_html=True)
            
            # 1. 题型
            q_type = question.get("question_type") or "未知"
            if q_type in ("未知", "解答题"):
                detected_type = detect_question_type(question.get("question_text") or "", q_type)
                if detected_type != q_type:
                    q_type = detected_type
                    try:
                        service.update_question_type(q_id, detected_type)
                        question["question_type"] = detected_type
                    except Exception:
                        pass
            
            # 2. 难度
            diff_val = 0
            try:
                diff_val = float(question.get("difficulty") or 0)
            except ValueError:
                pass
            diff_text = "未标注"
            diff_class = "qb-badge-gray"
            if diff_val > 0:
                if diff_val <= 3:
                    diff_text = f"易 ({diff_val:.1f})"
                    diff_class = "qb-badge-easy"
                elif diff_val <= 7:
                    diff_text = f"中 ({diff_val:.1f})"
                    diff_class = "qb-badge-medium"
                else:
                    diff_text = f"难 ({diff_val:.1f})"
                    diff_class = "qb-badge-hard"
                    
            attrs_html = f"""<div style="display: flex; flex-wrap: wrap; gap: 4px; align-items: center;">
<span class="qb-badge qb-badge-gray" style="margin-bottom: 4px;">{html.escape(q_type)}</span>
<span class="qb-badge {diff_class}" style="margin-bottom: 4px;">{diff_text}</span>"""
            attrs_html += _frequency_badge_html(frequency)
            attrs_html += "</div>"
            st.markdown(attrs_html, unsafe_allow_html=True)
            
        with col_tags_footer:
            st.markdown('<span style="font-size: 0.85rem; color: #64748b; font-weight: 600; display: block; margin-bottom: 6px;">🏷️ AI 属性标签</span>', unsafe_allow_html=True)
            
            # Parse AI tags
            kp_tags = []
            ability_tags = []
            method_tags = []
            model_tags = []
            error_tags = []
            chapter_tag = ""
            student_level_tag = ""
            tag_model_names = []
            tag_confidences = []
            
            for t in question.get("tags", []):
                tt = t.get("tag_type")
                tv = t.get("tag_value")
                if not tv:
                    continue
                model_name = _cell_text(t.get("model_name"))
                if model_name and model_name not in tag_model_names:
                    tag_model_names.append(model_name)
                try:
                    confidence_value = float(t.get("confidence"))
                    tag_confidences.append(confidence_value)
                except (TypeError, ValueError):
                    pass
                if tt == "knowledge_point":
                    kp_tags.append(tv)
                elif tt == "ability":
                    ability_tags.append(tv)
                elif tt == "method":
                    method_tags.append(tv)
                elif tt == "model":
                    model_tags.append(tv)
                elif tt in ("error_type", "error_prone", "error_prone_point"):
                     error_tags.append(tv)
                elif tt == "exam_scope":
                    chapter_tag = tv
                elif tt == "student_level":
                    student_level_tag = tv
            
            tag_groups_html = []
            if chapter_tag:
                tag_groups_html.append(f'<span class="qb-badge qb-badge-blue" style="margin-bottom: 4px;">{html.escape(chapter_tag)}</span>')
            if kp_tags:
                tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-green" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in kp_tags))
            if method_tags:
                tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-orange" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in method_tags))
            if ability_tags:
                tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-purple" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in ability_tags))
            if model_tags:
                tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-magenta" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in model_tags))
            if error_tags:
                tag_groups_html.append("".join(f'<span class="qb-badge qb-badge-red" style="margin-bottom: 4px;">{html.escape(x)}</span>' for x in error_tags))
            if student_level_tag:
                tag_groups_html.append(f'<span class="qb-badge qb-badge-gray" style="margin-bottom: 4px;">🎯 {html.escape(student_level_tag)}</span>')
                
            if tag_groups_html:
                st.markdown('<div style="display: flex; flex-wrap: wrap; gap: 4px;">' + "".join(tag_groups_html) + '</div>', unsafe_allow_html=True)
                meta_bits = []
                if tag_model_names:
                    meta_bits.append("模型：" + " / ".join(tag_model_names[:2]))
                if tag_confidences:
                    meta_bits.append(f"置信度：{max(tag_confidences) * 100:.0f}%")
                if meta_bits:
                    st.caption(" · ".join(meta_bits))
            else:
                st.caption("💡 暂无 AI 属性标签。")
                
        with col_reason:
            st.markdown('<span style="font-size: 0.85rem; color: #64748b; font-weight: 600; display: block; margin-bottom: 6px;">💡 教学诊断与提示</span>', unsafe_allow_html=True)
            reason_text = question.get("reason")
            if reason_text:
                reason_html = f"""<div style="font-size: 0.82rem; color: #475569; line-height: 1.5; background-color: #f8fafc; border-left: 3px solid #3b82f6; padding: 6px 8px; border-radius: 0 4px 4px 0; max-height: 120px; overflow-y: auto; white-space: pre-wrap;">{html.escape(reason_text)}</div>"""
                st.markdown(reason_html, unsafe_allow_html=True)
            else:
                st.caption("💡 暂无诊断，可在下方管理版面中添加。")
                
        # Expander for Tags Editing and Delete actions (Extremely clean!)
        with st.expander("🛠️ 管理此题标签与选项", expanded=False):
            analysis = _analysis_from_question(question)
            edit_cols = st.columns([1, 1])
            with edit_cols[0]:
                knowledge_points = _tag_multiselect(
                    "知识点",
                    KNOWLEDGE_POINT_OPTIONS,
                    analysis.knowledge_points,
                    key=f"qb_card_knowledge_{q_id}",
                )
                ability_tags = _tag_multiselect(
                    "数学能力",
                    ABILITY_TAG_OPTIONS,
                    analysis.ability_tags,
                    key=f"qb_card_ability_{q_id}",
                )
                error_points = _tag_multiselect(
                    "易错点",
                    tuple(error_tags),
                    analysis.error_prone_points,
                    key=f"qb_card_errors_{q_id}",
                )
            with edit_cols[1]:
                method_tags = _tag_multiselect(
                    "思想方法",
                    METHOD_TAG_OPTIONS,
                    analysis.method_tags,
                    key=f"qb_card_methods_{q_id}",
                )
                model_tags = _tag_multiselect(
                    "数学模型",
                    MATH_MODEL_OPTIONS,
                    analysis.math_model_tags,
                    key=f"qb_card_models_{q_id}",
                )
                prerequisite_points = _tag_multiselect(
                    "前置知识",
                    KNOWLEDGE_POINT_OPTIONS,
                    analysis.prerequisite_points,
                    key=f"qb_card_prerequisites_{q_id}",
                )
                
            score_cols = st.columns([1, 1, 1])
            with score_cols[0]:
                difficulty_score = st.number_input(
                    "难度",
                    min_value=1,
                    max_value=10,
                    value=analysis.difficulty,
                    step=1,
                    key=f"qb_card_difficulty_{q_id}",
                )
            with score_cols[1]:
                teaching_stage = st.text_input(
                    "教学阶段",
                    value=analysis.teaching_stage,
                    key=f"qb_card_stage_{q_id}",
                )
            with score_cols[2]:
                level_options = _ensure_options(STUDENT_LEVELS, [analysis.suitable_student_level])
                student_level = st.selectbox(
                    "适合层次",
                    options=level_options,
                    index=_option_index(level_options, analysis.suitable_student_level),
                    key=f"qb_card_level_{q_id}",
                )
            chapter_options = _ensure_options(CURRICULUM_CHAPTERS, [analysis.textbook_chapter])
            textbook_chapter = st.selectbox(
                "教材章节",
                options=chapter_options,
                index=_option_index(chapter_options, analysis.textbook_chapter),
                key=f"qb_card_chapter_{q_id}",
            )
            
            reason = st.text_area(
                "教学诊断与提示",
                value=question.get("reason") or "",
                height=80,
                key=f"qb_card_reason_edit_{q_id}",
            )
            
            action_btn_cols = st.columns([1, 1])
            with action_btn_cols[0]:
                if st.button("保存标签与诊断", key=f"qb_card_save_tags_{q_id}", type="primary", use_container_width=True):
                    accepted = TagAnalysis.from_dict(
                        {
                            "knowledge_points": knowledge_points,
                            "method_tags": method_tags,
                            "ability_tags": ability_tags,
                            "math_model_tags": model_tags,
                            "difficulty": difficulty_score,
                            "error_prone_points": error_points,
                            "prerequisite_points": prerequisite_points,
                            "textbook_chapter": textbook_chapter,
                            "teaching_stage": teaching_stage,
                            "suitable_student_level": student_level,
                            "reason": reason,
                        }
                    )
                    saved = service.save_tag_analysis(
                        q_id,
                        accepted,
                        overwrite_manual=True,
                        edited_fields=set(EDITED_TAG_FIELDS).union({"reason"}),
                    )
                    if saved:
                        st.success("修改已成功保存！")
                        st.rerun()
                    else:
                        st.error("保存失败")
            with action_btn_cols[1]:
                if st.button("🗑️ 软删除此题目", key=f"qb_card_delete_{q_id}", type="secondary", use_container_width=True):
                    if service.delete_question(q_id):
                        st.success("该题目已软删除！")
                        st.rerun()
                    else:
                        st.error("删除失败")
def _safe_html_format(value: str) -> str:
    if not value:
        return ""
    
    # 1. 预处理：解出任何已存在的 HTML 实体的多重转义，确保输入源一致
    text = str(value)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped

    # 2. 对全文做基础 HTML 转义以确保安全性
    escaped = html.escape(text)
    
    # 2.5 转换连续空格（2个或以上）为不折叠空格，防止浏览器折叠空白下划线
    escaped = re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), escaped)

    # 3. 动态恢复允许的 HTML 标签，包容大小写及任意属性 (如 colspan 等)
    allowed_tags = ["sub", "sup", "u", "table", "tbody", "tr", "td", "th"]
    for tag in allowed_tags:
        # 正则处理开始标签（兼容属性）
        opening_pattern = re.compile(rf"&lt;({tag})(\s+[^&]*)?&gt;", re.IGNORECASE)
        escaped = opening_pattern.sub(lambda m: f"<{m.group(1)}{html.unescape(m.group(2) or '')}>", escaped)
        
        # 正则处理结束标签
        closing_pattern = re.compile(rf"&lt;/({tag})&gt;", re.IGNORECASE)
        escaped = closing_pattern.sub(rf"</\1>", escaped)
        
    # 4. 单独匹配与规范化换行标签 <br>
    escaped = re.sub(r"&lt;br\s*/?&gt;", "<br>", escaped, flags=re.IGNORECASE)

    # 5. Convert raw newlines to <br> to prevent Streamlit Markdown block splitting
    escaped = escaped.replace("\n", "<br>").replace("\r", "")

    # 6. Clean up inner newlines and inner <br> inside tables to keep table HTML clean
    def _strip_table_br_newlines(match):
        content = match.group(0)
        content = content.replace("\n", "").replace("\r", "")
        content = content.replace("<br>", "").replace("<br/>", "")
        return content
    escaped = re.sub(r"<table\b[^>]*>.*?</table>", _strip_table_br_newlines, escaped, flags=re.DOTALL | re.IGNORECASE)

    return escaped


def _render_rich_text(value: str) -> None:
    text_without_image_markers = IMAGE_MARKER_PATTERN.sub("", str(value or "")).strip()
    if not text_without_image_markers:
        return
    st.markdown(
        f'<div class="qb-rich-text">{_safe_html_format(text_without_image_markers)}</div>',
        unsafe_allow_html=True,
    )


def _render_images(image_paths: list[str], *, image_scale_percent: int | None = None) -> None:
    paths = _dedupe_paths(image_paths)
    valid_paths = [Path(p) for p in paths if Path(p).exists()]
    if not valid_paths:
        return
    if image_scale_percent is None:
        _, image_scale_percent = _preview_display_settings()
        
    if len(valid_paths) == 1:
        width = image_display_width(valid_paths[0], image_count=1, scale_percent=image_scale_percent)
        st.image(str(valid_paths[0]), width=width)
    else:
        num_cols = min(len(valid_paths), 4)
        cols = st.columns(num_cols)
        for idx, path in enumerate(valid_paths):
            with cols[idx % num_cols]:
                width = image_display_width(path, image_count=len(valid_paths), scale_percent=image_scale_percent)
                st.image(str(path), width=width)


def _image_paths_from_text(value: object) -> list[str]:
    return [match.group("path").strip() for match in IMAGE_MARKER_PATTERN.finditer(str(value or ""))]


def _dedupe_paths(paths: list[object]) -> list[str]:
    deduped: list[str] = []
    for path in paths:
        text = _cell_text(path)
        if text and text not in deduped:
            deduped.append(text)
    return deduped


def _render_tag_panel(item: dict[str, Any]) -> None:
    grouped = _group_tags(item.get("tags", []))
    if grouped:
        chip_html = []
        for tag_type, label in TAG_GROUP_LABELS.items():
            values = grouped.get(tag_type) or []
            if not values:
                continue
            chips = "".join(f'<span class="qb-tag-chip">{html.escape(value)}</span>' for value in values)
            chip_html.append(
                f'<div class="qb-tag-row"><span class="qb-tag-label">{html.escape(label)}</span>{chips}</div>'
            )
        st.markdown('<div class="qb-tag-panel">' + "".join(chip_html) + "</div>", unsafe_allow_html=True)
    else:
        st.caption("暂未标注标签。")

    with st.expander("编辑标签", expanded=False):
        analysis = _analysis_from_question(item)
        edit_cols = st.columns([1, 1])
        with edit_cols[0]:
            knowledge_points = _tag_multiselect(
                "知识点",
                KNOWLEDGE_POINT_OPTIONS,
                analysis.knowledge_points,
                key=f"qb_card_knowledge_{item['id']}",
            )
            ability_tags = _tag_multiselect(
                "数学能力",
                ABILITY_TAG_OPTIONS,
                analysis.ability_tags,
                key=f"qb_card_ability_{item['id']}",
            )
            error_points = _tag_multiselect(
                "易错点",
                tuple(grouped.get("error_type", [])),
                analysis.error_prone_points,
                key=f"qb_card_errors_{item['id']}",
            )
        with edit_cols[1]:
            method_tags = _tag_multiselect(
                "思想方法",
                METHOD_TAG_OPTIONS,
                analysis.method_tags,
                key=f"qb_card_methods_{item['id']}",
            )
            model_tags = _tag_multiselect(
                "数学模型",
                MATH_MODEL_OPTIONS,
                analysis.math_model_tags,
                key=f"qb_card_models_{item['id']}",
            )
            prerequisite_points = _tag_multiselect(
                "前置知识",
                KNOWLEDGE_POINT_OPTIONS,
                analysis.prerequisite_points,
                key=f"qb_card_prerequisites_{item['id']}",
            )

        score_cols = st.columns([1, 1, 1])
        with score_cols[0]:
            difficulty_score = st.number_input(
                "难度",
                min_value=1,
                max_value=10,
                value=analysis.difficulty,
                step=1,
                key=f"qb_card_difficulty_{item['id']}",
            )
        with score_cols[1]:
            teaching_stage = st.text_input(
                "教学阶段",
                value=analysis.teaching_stage,
                key=f"qb_card_stage_{item['id']}",
            )
        with score_cols[2]:
            level_options = _ensure_options(STUDENT_LEVELS, [analysis.suitable_student_level])
            student_level = st.selectbox(
                "适合层次",
                options=level_options,
                index=_option_index(level_options, analysis.suitable_student_level),
                key=f"qb_card_level_{item['id']}",
            )
        chapter_options = _ensure_options(CURRICULUM_CHAPTERS, [analysis.textbook_chapter])
        textbook_chapter = st.selectbox(
            "教材章节",
            options=chapter_options,
            index=_option_index(chapter_options, analysis.textbook_chapter),
            key=f"qb_card_chapter_{item['id']}",
        )
        if st.button("保存标签修改", key=f"qb_card_save_tags_{item['id']}", type="primary"):
            accepted = TagAnalysis.from_dict(
                {
                    "knowledge_points": knowledge_points,
                    "method_tags": method_tags,
                    "ability_tags": ability_tags,
                    "math_model_tags": model_tags,
                    "difficulty": difficulty_score,
                    "error_prone_points": error_points,
                    "prerequisite_points": prerequisite_points,
                    "textbook_chapter": textbook_chapter,
                    "teaching_stage": teaching_stage,
                    "suitable_student_level": student_level,
                    "reason": analysis.reason,
                }
            )
            service = QuestionService(question_bank_db_path())
            saved = service.save_tag_analysis(
                int(item["id"]),
                accepted,
                overwrite_manual=True,
                edited_fields=set(EDITED_TAG_FIELDS),
            )
            if saved:
                st.success("标签修改已保存。")
                st.rerun()
            else:
                st.error("题目不存在或已删除，未保存。")
        
        st.write("")
        if st.button("🗑️ 软删除此题目", key=f"qb_card_delete_{item['id']}", type="secondary", use_container_width=True):
            service = QuestionService(question_bank_db_path())
            if service.delete_question(int(item["id"])):
                st.success("该题目已软删除！")
                st.rerun()
            else:
                st.error("删除失败")


def _group_tags(tags: list[dict[str, Any]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for tag in tags or []:
        tag_type = _cell_text(tag.get("tag_type"))
        tag_value = _cell_text(tag.get("tag_value"))
        if not tag_type or not tag_value:
            continue
        grouped.setdefault(tag_type, [])
        if tag_value not in grouped[tag_type]:
            grouped[tag_type].append(tag_value)
    return grouped


def _analysis_from_question(item: dict[str, Any]) -> TagAnalysis:
    grouped = _group_tags(item.get("tags", []))
    return TagAnalysis.from_dict(
        {
            "knowledge_points": grouped.get("knowledge_point", []),
            "method_tags": grouped.get("method", []),
            "ability_tags": grouped.get("ability", []),
            "math_model_tags": grouped.get("model", []),
            "difficulty": item.get("difficulty") or 1,
            "error_prone_points": grouped.get("error_type", []),
            "prerequisite_points": grouped.get("prerequisite", []),
            "textbook_chapter": _first_tag(grouped, "exam_scope"),
            "teaching_stage": _first_tag(grouped, "teaching_stage"),
            "suitable_student_level": _first_tag(grouped, "student_level"),
            "reason": item.get("reason") or "",
        }
    )


def _first_tag(grouped: dict[str, list[str]], tag_type: str) -> str:
    values = grouped.get(tag_type) or []
    return values[0] if values else ""


def _corpus_overview(service: QuestionService | None) -> dict[str, Any]:
    if service is None:
        return {}
    try:
        questions = service.query_questions()
    except Exception:  # noqa: BLE001
        LOGGER.exception("Failed to build question bank corpus overview")
        return {}
    tag_counts: dict[str, int] = {}
    for question in questions:
        for tag in question.get("tags", []):
            if tag.get("tag_type") not in {"knowledge_point", "method", "model"}:
                continue
            value = _cell_text(tag.get("tag_value"))
            if value:
                tag_counts[value] = tag_counts.get(value, 0) + 1
    top_tags = sorted(tag_counts.items(), key=lambda item: item[1], reverse=True)[:20]
    return {
        "total_questions": len(questions),
        "top_repeated_tags": [{"tag": tag, "count": count} for tag, count in top_tags],
    }


def _tag_status(item: dict[str, Any]) -> str:
    tags = item.get("tags", [])
    if not tags:
        return "未标注"
    ai_count = sum(1 for tag in tags if tag.get("source") == "ai")
    manual_count = sum(1 for tag in tags if tag.get("source") == "manual")
    return f"AI {ai_count} / 人工 {manual_count}"


def _tag_multiselect(label: str, base_options: tuple[str, ...], selected: list[str], *, key: str) -> list[str]:
    options = _ensure_options(base_options, selected)
    return [
        item.strip()
        for item in st.multiselect(
            label,
            options=options,
            default=[item for item in selected if item],
            key=key,
            accept_new_options=True,
        )
        if str(item).strip()
    ]


def _ensure_options(base_options: tuple[str, ...], selected: list[str]) -> list[str]:
    options: list[str] = []
    for item in [*base_options, *selected]:
        text = _cell_text(item)
        if text and text not in options:
            options.append(text)
    return options or [""]


def _option_index(options: list[str], selected: str) -> int:
    value = _cell_text(selected)
    return options.index(value) if value in options else 0


def _split_tag_text(value: str) -> list[str]:
    return [item.strip() for item in value.replace("，", ",").split(",") if item.strip()]


def _short_text(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _cell_text(value: object) -> str:
    return str(value or "").strip()


st.set_page_config(page_title="题库管理", layout="wide")
st.markdown(
    """
    <style>
    .qb-rich-text {
        white-space: pre-wrap;
        line-height: 1.62;
        font-size: 0.96rem;
        color: #0f172a;
        margin: 0.25rem 0 0.6rem 0;
    }
    
    /* Premium Paper Style */
    .qb-paper-card {
        background-color: #ffffff;
        border: 1px solid #e2e8f0;
        border-radius: 8px;
        padding: 18px 22px;
        margin-bottom: 14px;
        box-shadow: 0 4px 15px rgba(15, 23, 42, 0.05);
        font-family: 'Times New Roman', SimSun, serif;
    }
    
    .qb-paper-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        border-bottom: 1px dashed #cbd5e1;
        padding-bottom: 8px;
        margin-bottom: 10px;
        font-size: 0.86rem;
        color: #64748b;
    }
    
    .qb-paper-title-tag {
        font-weight: bold;
        font-size: 0.98rem;
        color: #1e293b;
    }
    
    /* Badge styling */
    .qb-badge {
        display: inline-flex;
        align-items: center;
        border-radius: 9999px;
        padding: 2px 10px;
        font-size: 0.75rem;
        font-weight: 600;
        line-height: 1.2;
        margin-right: 6px;
    }
    
    .qb-badge-easy {
        background-color: #f0fdf4;
        color: #166534;
        border: 1px solid #bbf7d0;
    }
    
    .qb-badge-medium {
        background-color: #fef8e6;
        color: #854d0e;
        border: 1px solid #fef08a;
    }
    
    .qb-badge-hard {
        background-color: #fef2f2;
        color: #991b1b;
        border: 1px solid #fecaca;
    }
    
    .qb-badge-blue {
        background-color: #eff6ff;
        color: #1d4ed8;
        border: 1px solid #bfdbfe;
    }
    
    .qb-badge-green {
        background-color: #f0fdf4;
        color: #15803d;
        border: 1px solid #bbf7d0;
    }
    
    .qb-badge-orange {
        background-color: #fff7ed;
        color: #c2410c;
        border: 1px solid #ffedd5;
    }
    
    .qb-badge-purple {
        background-color: #faf5ff;
        color: #6d28d9;
        border: 1px solid #e9d5ff;
    }
    
    .qb-badge-magenta {
        background-color: #fdf2f8;
        color: #be185d;
        border: 1px solid #fbcfe8;
    }
    
    .qb-badge-gray {
        background-color: #f8fafc;
        color: #475569;
        border: 1px solid #e2e8f0;
    }
    
    .qb-badge-red {
        background-color: #fef2f2;
        color: #dc2626;
        border: 1px solid #fecaca;
    }
    
    /* Teacher Mode Box */
    .qb-teacher-box {
        background-color: #f8fafc;
        border-left: 4px solid #3b82f6;
        padding: 10px 14px;
        margin-top: 10px;
        border-radius: 0 6px 6px 0;
        font-size: 0.88rem;
    }
    
    .qb-teacher-title {
        font-weight: bold;
        color: #1e3a8a;
        margin-bottom: 6px;
        display: flex;
        align-items: center;
        gap: 6px;
    }
    .qb-rich-text sub {
        font-size: 70%;
        vertical-align: sub;
        line-height: 0;
    }
    .qb-rich-text sup {
        font-size: 70%;
        vertical-align: super;
        line-height: 0;
    }
    .qb-rich-text u {
        text-decoration: underline;
        text-underline-offset: 3px;
        text-decoration-thickness: 1.5px;
    }
    .qb-rich-text table {
        border-collapse: collapse;
        width: auto;
        max-width: 100%;
        margin: 0.35rem 0 0.65rem;
        table-layout: auto;
    }
    .qb-rich-text td,
    .qb-rich-text th {
        border: 1px solid #d8dee9;
        padding: 0.32rem 0.45rem;
        vertical-align: top;
        word-break: break-word;
    }
    .qb-rich-text tr:nth-child(even) {
        background: #f8fafc;
    }
    .qb-tag-panel {
        display: flex;
        flex-direction: column;
        gap: 0.35rem;
        margin: 0.75rem 0;
    }
    .qb-tag-row {
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 0.35rem;
    }
    .qb-tag-label {
        min-width: 4.75rem;
        color: #697182;
        font-size: 0.9rem;
        font-weight: 600;
    }
    .qb-tag-chip {
        display: inline-flex;
        align-items: center;
        border: 1px solid #d9dee8;
        border-radius: 999px;
        padding: 0.12rem 0.55rem;
        background: #f6f8fb;
        color: #303642;
        font-size: 0.86rem;
        line-height: 1.55;
    }
    .qb-fixed-basket-shell {
        position: fixed;
        right: 1.35rem;
        top: 50%;
        transform: translateY(-50%);
        z-index: 999999;
        font-family: "Microsoft YaHei", sans-serif;
    }
    .qb-fixed-basket-details {
        position: relative;
    }
    .qb-fixed-basket-button {
        width: 3.5rem;
        height: 3.5rem;
        border-radius: 999px;
        background: #ffffff;
        border: 1px solid rgba(49, 51, 63, 0.18);
        box-shadow: 0 8px 24px rgba(20, 25, 40, 0.18);
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        list-style: none;
        position: relative;
    }
    .qb-fixed-basket-button::-webkit-details-marker {
        display: none;
    }
    .qb-fixed-basket-icon {
        font-size: 1.45rem;
    }
    .qb-fixed-basket-badge {
        position: absolute;
        right: -0.15rem;
        top: -0.2rem;
        min-width: 1.35rem;
        height: 1.35rem;
        padding: 0 0.28rem;
        border-radius: 999px;
        background: #ef4444;
        color: #fff;
        font-size: 0.78rem;
        line-height: 1.35rem;
        text-align: center;
        font-weight: 700;
    }
    .qb-fixed-basket-panel {
        position: absolute;
        right: 4.2rem;
        top: 50%;
        transform: translateY(-50%);
        width: min(22rem, calc(100vw - 6rem));
        max-height: 68vh;
        overflow: hidden;
        border: 1px solid #d9dee8;
        border-radius: 0.75rem;
        background: #ffffff;
        box-shadow: 0 16px 40px rgba(20, 25, 40, 0.2);
        padding: 0.8rem;
    }
    .qb-fixed-basket-title {
        font-size: 1rem;
        font-weight: 700;
        margin-bottom: 0.55rem;
        color: #242936;
    }
    .qb-fixed-basket-list {
        max-height: 48vh;
        overflow-y: auto;
        padding-right: 0.25rem;
    }
    .qb-fixed-basket-item {
        border-bottom: 1px solid #edf0f5;
        padding: 0.55rem 0;
    }
    .qb-fixed-basket-source {
        color: #252b37;
        font-weight: 650;
        font-size: 0.9rem;
    }
    .qb-fixed-basket-text,
    .qb-fixed-basket-empty {
        color: #667085;
        font-size: 0.82rem;
        line-height: 1.45;
        margin: 0.25rem 0;
    }
    .qb-fixed-basket-remove {
        color: #ef4444;
        font-size: 0.82rem;
        text-decoration: none;
    }
    .qb-fixed-basket-clear {
        display: block;
        margin-top: 0.65rem;
        border-radius: 0.45rem;
        border: 1px solid #fecaca;
        color: #dc2626 !important;
        text-align: center;
        padding: 0.48rem 0.7rem;
        font-weight: 650;
        text-decoration: none;
        background: #fff7f7;
    }
    .qb-fixed-basket-go {
        display: block;
        margin-top: 0.7rem;
        border-radius: 0.45rem;
        background: #1f6feb;
        color: #fff !important;
        text-align: center;
        padding: 0.55rem 0.7rem;
        font-weight: 650;
        text-decoration: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("本地真题题库管理")

service = QuestionService(question_bank_db_path())
service.initialize_database()

# Run automated question type backfilling silently on page load
try:
    backfilled_cnt = service.backfill_question_types()
    if backfilled_cnt > 0:
        st.toast(f"🏷️ 题库类型自动升级：已成功识别并升级了 {backfilled_cnt} 道题目的细分题型！", icon="🏷️")
except Exception as e:
    LOGGER.warning("Auto-backfilling question types failed: %s", e)

_sync_basket_query_params()
st.caption(f"题库数据库：{service.db_path}")

raw_papers_dir = project_data_root() / "question_bank" / "raw_papers"
raw_papers_dir.mkdir(parents=True, exist_ok=True)

_render_import_area(service, raw_papers_dir)
_render_import_result(service)
_render_rich_content_tools(service)
st.divider()
_render_question_bank_overview(service)
st.divider()
_render_paper_list(service)
st.divider()
_render_questions_v2(service)
