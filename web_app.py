from __future__ import annotations

import json
import html
import inspect
import os
import queue
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont
from objective_admission_wizard_ui import render_objective_admission_wizard_tab
import session_manager as _session_manager

from analytics import AnalyticsService
from answer_region_geometry import answer_regions_with_template_source_sizes, scaled_region_bbox
from answer_region_focus_page import render_answer_region_focus_page
from answer_region_session_lock import get_answer_region_session_lock
from api_profiles import load_api_profiles, normalize_question_allowlist, save_api_profiles
from data_transfer_service import (
    EXPORT_SIZE_WARNING_MB,
    build_export_manifest,
    create_export_zip_bytes,
    default_export_sources,
    total_size_mb,
)
from db_manager import DBManager
from export_names import safe_filename_fragment
from grading_service import GradingService
from llm_client import LLMClient, LLMSettings, normalize_openai_base_url
from manual_review_service import ManualReviewService
from original_paper_exporter import OriginalPaperExporter
from question_bank.database.paths import project_data_root, question_bank_db_path
from question_bank.services.ai_tagging_service import AITaggingService
from question_bank.services.grading_paper_intake_service import copy_and_intake_uploaded_grading_paper
from report import ReportGenerator
from score_policy import enforce_integer_scores_by_type, MAX_QUESTION_SCORE
from scanner import STUDENT_NAME_REGION_ID, ScanAnalysis, Scanner, refine_scan_analysis_matches, render_pdf_to_standard_pages, student_name_region_from_regions
from session_config_state import (
    clear_pending_config_for_new_session,
    remember_saved_config_for_new_session,
    restore_persistent_config_state,
)
from session_cleanup import hard_delete_session_from_recycle_bin
from session_manager import (
    extract_docx_text,
    force_payload_total_score,
    failed_grading_config_question_ids,
    generate_grading_config_from_confirmed_blocks,
    generate_grading_config_from_docx_text,
    generate_grading_config_from_docx_text_legacy,
    generate_grading_config_from_images,
    preview_question_blocks_from_docx_bytes,
    preview_question_blocks_from_docx_text,
    refine_grading_config_from_manual_structure,
    retry_failed_grading_config_questions,
    retry_grading_config_score_allocation,
    save_generated_config,
)
from student_manager import StudentManager
from template_analyzer import (
    create_template_mapping_package,
)
from update_tools.backup_core import _safe_restore_destination


BASE_DIR = Path(__file__).resolve().parent

# --- Unified path management (data separated from code) ---
from path_manager import get_path_manager as _get_pm, resolve_stored_file_path
_pm = _get_pm()
APP_DATA_DIR = _pm.data_root
DB_PATH = _pm.db_path
UPLOAD_CONFIG_DIR = _pm.upload_config_dir
DEFAULT_EXAMS_DIR = _pm.exams_dir
TEMPLATE_DIR = _pm.templates_dir
ANNOTATED_DIR = _pm.annotated_dir
API_PROFILES_PATH = _pm.api_profiles_path



DEFAULT_MODELS = {
    "grading_model": "gpt-4o",
    "config_model": "gpt-4o",
    "ocr_model": "gpt-4o",
}

DEFAULT_GRADING_RPM = 1000
DEFAULT_FULL_PAPER_WORKERS = 200
DEFAULT_HYBRID_INFLIGHT_WORKERS = 200
DEFAULT_PRECHECK_WORKERS = 16


def _sidebar_int_setting(
    saved_profile: dict[str, Any],
    profile_key: str,
    env_key: str,
    default: int,
) -> int:
    raw = saved_profile.get(profile_key)
    if raw in (None, ""):
        raw = os.getenv(env_key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def ensure_env_ready() -> DBManager:
    APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _pm.reports_dir.mkdir(parents=True, exist_ok=True)
    UPLOAD_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_EXAMS_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    ANNOTATED_DIR.mkdir(parents=True, exist_ok=True)

    db = DBManager(DB_PATH)
    if DB_PATH.exists():
        db.create_backup("startup", once_per_day=True)
    db.initialize()
    return db


def build_llm_settings_from_sidebar() -> LLMSettings | None:
    profiles = load_api_profiles(API_PROFILES_PATH)
    saved_profile = profiles[-1] if profiles else {}

    if "api_provider_input" not in st.session_state:
        st.session_state.api_provider_input = str(
            saved_profile.get("provider") or os.getenv("LLM_PROVIDER", "custom-openai-compatible")
        )
    if "api_key_input" not in st.session_state:
        st.session_state.api_key_input = str(saved_profile.get("api_key") or os.getenv("LLM_API_KEY", ""))
    if "api_base_url_input" not in st.session_state:
        st.session_state.api_base_url_input = str(
            saved_profile.get("base_url") or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        )
    if "config_api_provider_input" not in st.session_state:
        st.session_state.config_api_provider_input = str(
            saved_profile.get("config_provider")
            or saved_profile.get("provider")
            or os.getenv("LLM_CONFIG_PROVIDER", os.getenv("LLM_PROVIDER", "custom-openai-compatible"))
        )
    if "config_api_key_input" not in st.session_state:
        st.session_state.config_api_key_input = str(
            saved_profile.get("config_api_key") or saved_profile.get("api_key") or os.getenv("LLM_CONFIG_API_KEY", os.getenv("LLM_API_KEY", ""))
        )
    if "config_base_url_input" not in st.session_state:
        st.session_state.config_base_url_input = str(
            saved_profile.get("config_base_url")
            or saved_profile.get("base_url")
            or os.getenv("LLM_CONFIG_BASE_URL", os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
        )
    if "api_grading_model_input" not in st.session_state:
        st.session_state.api_grading_model_input = str(
            saved_profile.get("grading_model") or os.getenv("LLM_GRADING_MODEL", DEFAULT_MODELS["grading_model"])
        )
    if "api_config_model_input" not in st.session_state:
        st.session_state.api_config_model_input = str(
            saved_profile.get("config_model") or os.getenv("LLM_CONFIG_MODEL", DEFAULT_MODELS["config_model"])
        )
    if "objective_api_key_input" not in st.session_state:
        st.session_state.objective_api_key_input = str(saved_profile.get("objective_api_key") or os.getenv("LLM_OBJECTIVE_API_KEY", ""))
    if "objective_base_url_input" not in st.session_state:
        st.session_state.objective_base_url_input = str(saved_profile.get("objective_base_url") or os.getenv("LLM_OBJECTIVE_BASE_URL", ""))
    if "objective_model_input" not in st.session_state:
        st.session_state.objective_model_input = str(saved_profile.get("objective_model") or os.getenv("LLM_OBJECTIVE_MODEL", ""))
    if "objective_temperature_input" not in st.session_state:
        st.session_state.objective_temperature_input = float(saved_profile.get("objective_temperature", 0.0))
    if "objective_max_tokens_input" not in st.session_state:
        st.session_state.objective_max_tokens_input = int(saved_profile.get("objective_max_tokens", 100))
    if "objective_thinking_type_input" not in st.session_state:
        st.session_state.objective_thinking_type_input = str(saved_profile.get("objective_thinking_type", "disabled"))
    if "objective_timeout_input" not in st.session_state:
        st.session_state.objective_timeout_input = int(saved_profile.get("objective_timeout", 60))
    if "objective_enabled_input" not in st.session_state:
        st.session_state.objective_enabled_input = bool(saved_profile.get("objective_enabled", False))
    if "objective_batch_size_input" not in st.session_state:
        st.session_state.objective_batch_size_input = int(saved_profile.get("objective_batch_size", 15))
    if "hybrid_major_batch_size_input" not in st.session_state:
        st.session_state.hybrid_major_batch_size_input = int(saved_profile.get("hybrid_major_batch_size", 4))
    if "grading_requests_per_minute_input" not in st.session_state:
        st.session_state.grading_requests_per_minute_input = _sidebar_int_setting(
            saved_profile,
            "grading_requests_per_minute",
            "AI_GRADING_REQUESTS_PER_MINUTE",
            DEFAULT_GRADING_RPM,
        )
    if "grading_max_workers_input" not in st.session_state:
        st.session_state.grading_max_workers_input = _sidebar_int_setting(
            saved_profile,
            "grading_max_workers",
            "AI_GRADING_MAX_WORKERS",
            DEFAULT_FULL_PAPER_WORKERS,
        )
    if "hybrid_inflight_workers_input" not in st.session_state:
        st.session_state.hybrid_inflight_workers_input = _sidebar_int_setting(
            saved_profile,
            "hybrid_inflight_workers",
            "AI_HYBRID_INFLIGHT_WORKERS",
            DEFAULT_HYBRID_INFLIGHT_WORKERS,
        )
    if "precheck_max_workers_input" not in st.session_state:
        st.session_state.precheck_max_workers_input = _sidebar_int_setting(
            saved_profile,
            "precheck_max_workers",
            "AI_GRADING_PRECHECK_WORKERS",
            DEFAULT_PRECHECK_WORKERS,
        )

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
    if "tagging_enabled_input" not in st.session_state:
        st.session_state.tagging_enabled_input = bool(saved_profile.get("tagging_enabled", True))
    if "tagging_thinking_input" not in st.session_state:
        st.session_state.tagging_thinking_input = bool(saved_profile.get("tagging_thinking", False))
    if "tagging_review_enabled_input" not in st.session_state:
        st.session_state.tagging_review_enabled_input = bool(saved_profile.get("tagging_review_enabled", False))
    if "tagging_review_api_key_input" not in st.session_state:
        st.session_state.tagging_review_api_key_input = str(saved_profile.get("tagging_review_api_key") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_API_KEY", ""))
    if "tagging_review_base_url_input" not in st.session_state:
        st.session_state.tagging_review_base_url_input = str(saved_profile.get("tagging_review_base_url") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_BASE_URL", ""))
    if "tagging_review_model_input" not in st.session_state:
        st.session_state.tagging_review_model_input = str(saved_profile.get("tagging_review_model") or os.getenv("QUESTION_BANK_TAGGING_REVIEW_MODEL", ""))

    api_key_value = str(st.session_state.get("api_key_input", "")).strip()
    config_api_key_value = str(st.session_state.get("config_api_key_input", "")).strip()
    grading_model_value = str(st.session_state.get("api_grading_model_input", "")).strip() or DEFAULT_MODELS["grading_model"]
    config_model_value = str(st.session_state.get("api_config_model_input", "")).strip() or DEFAULT_MODELS["config_model"]
    api_ready = bool(api_key_value and config_api_key_value)
    badge = _status_badge("已配置", "green") if api_ready else _status_badge("未配置", "yellow")
    model_label = html.escape(f"批改 {grading_model_value} / 评分 {config_model_value}")

    st.sidebar.markdown("### AI 配置")
    st.sidebar.markdown(
        f"""
        <div class="gm-sidebar-summary">
          <div class="gm-sidebar-summary-title">调用模型</div>
          <div class="gm-sidebar-summary-name">{model_label}</div>
          <div class="gm-sidebar-summary-meta">批改 API + 评分标准 API · {badge}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    with st.sidebar.expander(f"API 信息 · 批改 {grading_model_value} · 评分 {config_model_value}", expanded=not api_ready):
        st.markdown("**批改/视觉 API**")
        api_key = st.text_input("批改 API Key", type="password", key="api_key_input")
        base_url = st.text_input("批改 Base URL", key="api_base_url_input")
        grading_model = st.text_input("批改/视觉模型", key="api_grading_model_input")
        provider_name = st.text_input("批改提供方标识", key="api_provider_input")
        st.caption("学生姓名识别复用“批改/视觉模型”，不再单独配置 OCR 模型。")

        st.markdown("**评分标准生成 API**")
        config_api_key = st.text_input("评分标准 API Key", type="password", key="config_api_key_input")
        config_base_url = st.text_input("评分标准 Base URL", key="config_base_url_input")
        config_model = st.text_input("评分标准生成模型", key="api_config_model_input")
        config_provider_name = st.text_input("评分标准提供方标识", key="config_api_provider_input")

        st.markdown("**选填题识别批改 API 参数**")
        st.caption("用于选择题、填空题等客观题的低成本识别模型。建议使用 Lite / Mini 类模型。该配置不会影响主观题批改模型。")
        objective_api_key = st.text_input("API Key", type="password", key="objective_api_key_input")
        objective_base_url = st.text_input("API Base URL", key="objective_base_url_input")
        objective_model = st.text_input("模型名称", key="objective_model_input")
        st.caption("例如 doubaoseed2.0 lite。具体模型名以你的 ohmygpt 后台支持的名称为准。")
        objective_temperature = float(st.session_state.get("objective_temperature_input", 0.0))
        objective_max_tokens = int(st.session_state.get("objective_max_tokens_input", 4096))
        
        # fix selectbox issue by deriving index from state
        _think_options = ["disabled", "enabled"]
        _think_idx = _think_options.index(st.session_state.objective_thinking_type_input) if st.session_state.objective_thinking_type_input in _think_options else 0
        objective_thinking_type = st.selectbox("Thinking 模式", options=_think_options, index=_think_idx, key="objective_thinking_type_input")
        objective_timeout = int(st.session_state.get("objective_timeout_input", 60))
        objective_batch_size = st.number_input("客观题并发合并数量", min_value=1, max_value=50, step=1, key="objective_batch_size_input", help="单次请求中合并的客观题试卷切片数，默认 15")
        major_batch_size = st.number_input("主观题并发合并数量", min_value=1, max_value=20, step=1, key="hybrid_major_batch_size_input", help="单次请求中合并的主观题试卷切片数，默认 4")
        objective_enabled = st.checkbox("启用选填题专用模型", key="objective_enabled_input")

        if st.button("保存 API 配置", use_container_width=True, key="save_single_api_settings", type="primary"):
            # Load existing profiles first to avoid overwriting unrelated configurations
            profiles = load_api_profiles(API_PROFILES_PATH)
            current_profile = profiles[-1] if profiles else {"name": "default"}

            current_profile["provider"] = str(provider_name).strip() or "custom-openai-compatible"
            current_profile["api_key"] = str(api_key).strip()
            current_profile["base_url"] = normalize_openai_base_url(str(base_url).strip() or "https://api.openai.com/v1")
            current_profile["grading_model"] = str(grading_model).strip() or DEFAULT_MODELS["grading_model"]
            current_profile["config_provider"] = str(config_provider_name).strip() or str(provider_name).strip() or "custom-openai-compatible"
            current_profile["config_api_key"] = str(config_api_key).strip()
            current_profile["config_base_url"] = normalize_openai_base_url(str(config_base_url).strip() or "https://api.openai.com/v1")
            current_profile["config_model"] = str(config_model).strip() or DEFAULT_MODELS["config_model"]
            current_profile["ocr_model"] = str(grading_model).strip() or DEFAULT_MODELS["grading_model"]
            current_profile["objective_api_key"] = str(objective_api_key).strip()
            current_profile["objective_base_url"] = normalize_openai_base_url(str(objective_base_url).strip() or "https://api.openai.com/v1")
            current_profile["objective_model"] = str(objective_model).strip()
            current_profile["objective_temperature"] = float(objective_temperature)
            current_profile["objective_max_tokens"] = int(objective_max_tokens)
            current_profile["objective_thinking_type"] = str(objective_thinking_type).strip()
            current_profile["objective_timeout"] = int(objective_timeout)
            current_profile["objective_enabled"] = bool(objective_enabled)
            current_profile["objective_batch_size"] = int(objective_batch_size)
            current_profile["hybrid_major_batch_size"] = int(major_batch_size)
            current_profile["grading_requests_per_minute"] = int(st.session_state.get("grading_requests_per_minute_input", DEFAULT_GRADING_RPM))
            current_profile["grading_max_workers"] = int(st.session_state.get("grading_max_workers_input", DEFAULT_FULL_PAPER_WORKERS))
            current_profile["hybrid_inflight_workers"] = int(st.session_state.get("hybrid_inflight_workers_input", DEFAULT_HYBRID_INFLIGHT_WORKERS))
            current_profile["precheck_max_workers"] = int(st.session_state.get("precheck_max_workers_input", DEFAULT_PRECHECK_WORKERS))

            # NOTE: 打标签 (tagging_*) 配置由「题库管理」页面独立管理并保存。
            # 此处刻意不写入，避免主页保存时用 session_state 中陈旧/默认值覆盖用户
            # 在题库管理页保存的打标签配置（历史 bug：并发数/RPM/API Key 被改回默认）。

            if not current_profile["api_key"]:
                st.error("请先填写批改 API Key")
            elif not current_profile["config_api_key"]:
                st.error("请先填写评分标准 API Key")
            else:
                if not profiles:
                    profiles = [current_profile]
                else:
                    profiles[-1] = current_profile
                save_api_profiles(API_PROFILES_PATH, profiles)
                st.session_state["_saved_api_settings_notice"] = True
                st.success("API 配置已保存")
                st.rerun()

    st.sidebar.markdown("### 全局并发设置")
    with st.sidebar.expander("批改并发 · 1000 RPM 推荐", expanded=False):
        st.caption("1000 RPM 约等于每秒 16.7 个请求；混合批改在途请求数默认 200，用来避免第一批长响应堵住后续题目。")
        st.number_input(
            "请求速率上限（RPM）",
            min_value=1,
            max_value=10000,
            step=50,
            key="grading_requests_per_minute_input",
            help="限制 AI 请求启动速度。1000 RPM 是当前推荐值；若要每秒 20 个请求，需要约 1200 RPM。",
        )
        st.number_input(
            "整卷批改并发数",
            min_value=1,
            max_value=200,
            step=1,
            key="grading_max_workers_input",
            help="整卷批改同时在跑的试卷数。1000 RPM 下默认 200，与混合批改在途请求数保持一致。",
        )
        st.number_input(
            "混合批改在途请求数",
            min_value=1,
            max_value=1000,
            step=10,
            key="hybrid_inflight_workers_input",
            help="混合批改可同时等待返回的请求数。1000 RPM 下默认 200，适合模型响应较慢但请求额度较高的情况。",
        )
        st.number_input(
            "预检姓名识别并发数",
            min_value=1,
            max_value=64,
            step=1,
            key="precheck_max_workers_input",
            help="PDF/图片预检阶段用于并发识别学生姓名。1000 RPM 下建议 16。",
        )

    if st.session_state.pop("_saved_api_settings_notice", False):
        st.sidebar.success("API 配置已保存")

    data = {
        "provider": str(st.session_state.get("api_provider_input", "")).strip() or "custom-openai-compatible",
        "api_key": str(st.session_state.get("api_key_input", "")).strip(),
        "base_url": normalize_openai_base_url(
            str(st.session_state.get("api_base_url_input", "")).strip() or "https://api.openai.com/v1"
        ),
        "grading_model": str(st.session_state.get("api_grading_model_input", "")).strip() or DEFAULT_MODELS["grading_model"],
        "config_provider": str(st.session_state.get("config_api_provider_input", "")).strip() or "custom-openai-compatible",
        "config_api_key": str(st.session_state.get("config_api_key_input", "")).strip(),
        "config_base_url": normalize_openai_base_url(
            str(st.session_state.get("config_base_url_input", "")).strip() or "https://api.openai.com/v1"
        ),
        "config_model": str(st.session_state.get("api_config_model_input", "")).strip() or DEFAULT_MODELS["config_model"],
    }
    data["ocr_model"] = data["grading_model"]

    st.session_state.api_profile_name = "default"
    st.session_state.api_provider = data["provider"]
    st.session_state.api_key_value = data["api_key"]
    st.session_state.api_base_url = data["base_url"]
    st.session_state.config_api_provider = data["config_provider"]
    st.session_state.config_api_key_value = data["config_api_key"]
    st.session_state.config_api_base_url = data["config_base_url"]
    st.session_state.api_ocr_model = data["ocr_model"]
    st.session_state.api_grading_model = data["grading_model"]
    st.session_state.api_config_model = data["config_model"]

    os.environ["LLM_PROVIDER"] = data["provider"]
    os.environ["LLM_API_KEY"] = data["api_key"]
    os.environ["LLM_BASE_URL"] = data["base_url"]
    os.environ["LLM_GRADING_MODEL"] = data["grading_model"]
    os.environ["LLM_CONFIG_PROVIDER"] = data["config_provider"]
    os.environ["LLM_CONFIG_API_KEY"] = data["config_api_key"]
    os.environ["LLM_CONFIG_BASE_URL"] = data["config_base_url"]
    os.environ["LLM_CONFIG_MODEL"] = data["config_model"]
    os.environ["LLM_OCR_MODEL"] = data["grading_model"]
    os.environ["AI_GRADING_REQUESTS_PER_MINUTE"] = str(st.session_state.get("grading_requests_per_minute_input", DEFAULT_GRADING_RPM))
    os.environ["AI_GRADING_MAX_WORKERS"] = str(st.session_state.get("grading_max_workers_input", DEFAULT_FULL_PAPER_WORKERS))
    os.environ["AI_GRADING_CONFIG_WORKERS"] = str(st.session_state.get("grading_max_workers_input", DEFAULT_FULL_PAPER_WORKERS))
    os.environ["AI_HYBRID_INFLIGHT_WORKERS"] = str(st.session_state.get("hybrid_inflight_workers_input", DEFAULT_HYBRID_INFLIGHT_WORKERS))
    os.environ["AI_GRADING_PRECHECK_WORKERS"] = str(st.session_state.get("precheck_max_workers_input", DEFAULT_PRECHECK_WORKERS))

    st.session_state.objective_enabled = st.session_state.get("objective_enabled_input", False)
    os.environ["LLM_OBJECTIVE_API_KEY"] = str(st.session_state.get("objective_api_key_input", ""))
    os.environ["LLM_OBJECTIVE_BASE_URL"] = str(st.session_state.get("objective_base_url_input", ""))
    os.environ["LLM_OBJECTIVE_MODEL"] = str(st.session_state.get("objective_model_input", ""))
    os.environ["LLM_OBJECTIVE_BATCH_SIZE"] = str(st.session_state.get("objective_batch_size_input", 15))
    os.environ["LLM_HYBRID_MAJOR_BATCH_SIZE"] = str(st.session_state.get("hybrid_major_batch_size_input", 4))

    st.session_state.tagging_enabled = True
    os.environ["QUESTION_BANK_TAGGING_API_KEY"] = str(st.session_state.get("tagging_api_key_input", ""))
    os.environ["QUESTION_BANK_TAGGING_BASE_URL"] = str(st.session_state.get("tagging_base_url_input", ""))
    os.environ["QUESTION_BANK_TAGGING_MODEL"] = str(st.session_state.get("tagging_model_input", ""))
    os.environ["QUESTION_BANK_TAGGING_MAX_WORKERS"] = str(st.session_state.get("tagging_max_workers_input", 8))
    os.environ["QUESTION_BANK_TAGGING_REQUESTS_PER_MINUTE"] = str(st.session_state.get("tagging_requests_per_minute_input", 1000))
    os.environ["QUESTION_BANK_TAGGING_THINKING"] = "1" if st.session_state.get("tagging_thinking_input", False) else "0"
    review_enabled_now = bool(st.session_state.get("tagging_review_enabled_input", False) and str(st.session_state.get("tagging_review_model_input", "")).strip())
    os.environ["QUESTION_BANK_TAGGING_REVIEW_MODEL"] = str(st.session_state.get("tagging_review_model_input", "")).strip() if review_enabled_now else ""
    os.environ["QUESTION_BANK_TAGGING_REVIEW_API_KEY"] = str(st.session_state.get("tagging_review_api_key_input", "")).strip() if review_enabled_now else ""
    os.environ["QUESTION_BANK_TAGGING_REVIEW_BASE_URL"] = str(st.session_state.get("tagging_review_base_url_input", "")).strip() if review_enabled_now else ""

    if not data["api_key"]:
        st.sidebar.warning("请先在 API 配置中填写并保存批改 API Key")
        return None
    if not data["config_api_key"]:
        st.sidebar.warning("请先在 API 配置中填写并保存评分标准 API Key")
        return None

    return LLMSettings(
        api_key=data["api_key"],
        base_url=data["base_url"],
        ocr_model=data["ocr_model"],
        grading_model=data["grading_model"],
        config_model=data["config_model"],
        config_api_key=data["config_api_key"],
        config_base_url=data["config_base_url"],
    )


def build_stage_progress_log_body(
    logs: list[str],
    current_stage: str,
    current_detail: str | None,
    elapsed: float,
    current_progress: float,
) -> str:
    visible_logs = [str(item) for item in logs[-200:]]
    if not visible_logs:
        visible_logs = ["等待底层任务日志..."]
    progress_percent = int(max(0.0, min(0.99, float(current_progress))) * 100)
    detail_text = f" - {current_detail}" if current_detail else ""
    visible_logs.append(
        f"[等待中] {current_stage}{detail_text}，已等待 {int(elapsed)} 秒，进度约 {progress_percent}%"
    )
    return "\n".join(visible_logs)


def _qb_safe_html_format(value: str) -> str:
    """复用题库的安全 HTML 渲染：保留 <sub>/<sup>/<u>/<table> 等公式标签，其余转义。"""
    import re as _re
    if not value:
        return ""
    text = str(value)
    for _ in range(3):
        unescaped = html.unescape(text)
        if unescaped == text:
            break
        text = unescaped
    escaped = html.escape(text)
    escaped = _re.sub(r" {2,}", lambda m: "&nbsp;" * len(m.group(0)), escaped)
    allowed_tags = ["sub", "sup", "u", "table", "tbody", "tr", "td", "th"]
    for tag in allowed_tags:
        opening = _re.compile(rf"&lt;({tag})(\s+[^&]*)?&gt;", _re.IGNORECASE)
        escaped = opening.sub(lambda m: f"<{m.group(1)}{html.unescape(m.group(2) or '')}>", escaped)
        closing = _re.compile(rf"&lt;/({tag})&gt;", _re.IGNORECASE)
        escaped = closing.sub(rf"</\1>", escaped)
    escaped = _re.sub(r"&lt;br\s*/?&gt;", "<br>", escaped, flags=_re.IGNORECASE)
    escaped = escaped.replace("\n", "<br>").replace("\r", "")

    def _strip_table_br(match):
        c = match.group(0)
        return c.replace("\n", "").replace("\r", "").replace("<br>", "").replace("<br/>", "")

    escaped = _re.sub(r"<table\b[^>]*>.*?</table>", _strip_table_br, escaped, flags=_re.DOTALL | _re.IGNORECASE)
    return escaped


def _qb_image_paths_from_text(value: object) -> list[str]:
    import re as _re
    pat = _re.compile(r"\[\[IMAGE:(?P<path>.+?)\]\]")
    seen: list[str] = []
    for m in pat.finditer(str(value or "")):
        p = m.group("path").strip()
        if p and p not in seen:
            seen.append(p)
    return seen


def _qb_strip_image_markers(value: str) -> str:
    import re as _re
    return _re.sub(r"\[\[IMAGE:.+?\]\]", "", str(value or "")).strip()


def _render_split_images(image_paths: list[str]) -> None:
    """渲染题目图片：单图自适应，多图按列网格。复用题库展示思路，固定宽度避免外部依赖。"""
    seen: list[str] = []
    for p in image_paths or []:
        if p and p not in seen and Path(p).exists():
            seen.append(p)
    if not seen:
        return
    if len(seen) == 1:
        st.image(seen[0], width=360)
    else:
        num_cols = min(len(seen), 4)
        cols = st.columns(num_cols)
        for idx, path in enumerate(seen):
            with cols[idx % num_cols]:
                st.image(path, width=200)


def _run_with_stage_progress(label: str, work, *, done_text: str = "完成") -> Any:
    default_stages = [
        "准备本地文件",
        "整理提示词与上下文",
        "等待 AI 返回并解析 JSON",
        "校验结构化结果",
        "写入本地缓存",
    ]
    stage_queue: queue.Queue[tuple[float | None, str, str | None]] = queue.Queue()

    def report(progress: float | None, stage: str, detail: str | None = None) -> None:
        stage_queue.put((progress, stage, detail))

    def accepts_report_callback() -> bool:
        try:
            return len(inspect.signature(work).parameters) >= 1
        except (TypeError, ValueError):
            return False

    progress_bar = st.progress(0)
    status_box = st.empty()
    log_box = st.empty()
    started = time.monotonic()
    current_progress = 0.02
    current_stage = default_stages[0]
    current_detail: str | None = None
    use_report_callback = accepts_report_callback()
    logs: list[str] = []
    
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(work, report) if use_report_callback else executor.submit(work)
        while not future.done():
            while True:
                try:
                    progress, stage, detail = stage_queue.get_nowait()
                except queue.Empty:
                    break
                current_stage = stage
                current_detail = detail
                if progress is not None:
                    current_progress = max(0.0, min(0.98, float(progress)))
                if detail:
                    from datetime import datetime
                    ts = datetime.now().strftime("%H:%M:%S")
                    logs.append(f"[{ts}] {stage} - {detail}")

            elapsed = time.monotonic() - started
            if not use_report_callback:
                ratio = min(0.92, 0.08 + elapsed / 120.0)
                current_stage = default_stages[min(int(ratio * len(default_stages)), len(default_stages) - 1)]
                current_progress = ratio
            message = f"{label}：{current_stage}，已等待 {int(elapsed)} 秒"
            progress_bar.progress(current_progress)
            status_box.info(message)
            
            log_body = build_stage_progress_log_body(
                logs,
                current_stage,
                current_detail,
                elapsed,
                current_progress,
            )
            log_box.markdown(
                f'<div style="height:250px;overflow-y:auto;background:#1e1e1e;color:#e0e0e0;padding:10px;border-radius:5px;font-family:monospace;white-space:pre-wrap;font-size:0.85rem;">{html.escape(log_body)}</div>',
                unsafe_allow_html=True
            )
            
            time.sleep(0.35)
        result = future.result()
    progress_bar.progress(1.0)
    status_box.success(f"{label}：{done_text}")
    return result


_DESIGN_CSS = """
<style>
/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   TYPEFACE IMPORT
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,300;0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700;1,9..40,400&family=DM+Mono:wght@400;500&display=swap');

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   DESIGN TOKENS
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
:root {
  --bg:          #F8FAFC;
  --surface:     #FFFFFF;
  --border:      #E2E8F0;
  --border-subtle: #F1F5F9;
  --text-primary:   #0F172A;
  --text-secondary: #475569;
  --text-muted:     #94A3B8;
  --brand:       #2563EB;
  --brand-dark:  #1D4ED8;
  --brand-light: #DBEAFE;
  --brand-glow:  rgba(37,99,235,0.12);
  --success:     #059669;
  --success-bg:  #ECFDF5;
  --warning:     #D97706;
  --warning-bg:  #FFFBEB;
  --danger:      #DC2626;
  --danger-bg:   #FEF2F2;
  --radius-sm:   8px;
  --radius:      14px;
  --radius-lg:   20px;
  --shadow-sm:   0 1px 3px rgba(15,23,42,0.06), 0 1px 2px rgba(15,23,42,0.04);
  --shadow:      0 4px 16px rgba(15,23,42,0.07), 0 1px 4px rgba(15,23,42,0.04);
  --shadow-lg:   0 12px 40px rgba(15,23,42,0.10), 0 2px 8px rgba(15,23,42,0.06);
  --transition:  all 0.18s cubic-bezier(0.4,0,0.2,1);
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   GLOBAL RESET & BASE
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
html, body, .stApp {
  font-family: 'DM Sans', 'Noto Sans SC', system-ui, sans-serif !important;
}

/* Keep Streamlit/Material icon ligatures as icons, not visible text such as "upload". */
.material-icons,
.material-icons-outlined,
.material-icons-round,
.material-icons-sharp,
.material-symbols-outlined,
.material-symbols-rounded,
.material-symbols-sharp,
span[class*="material-icons"],
span[class*="material-symbols"] {
  font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons" !important;
  font-weight: normal !important;
  font-style: normal !important;
  line-height: 1 !important;
  letter-spacing: normal !important;
  text-transform: none !important;
  white-space: nowrap !important;
  word-wrap: normal !important;
  direction: ltr !important;
  -webkit-font-feature-settings: "liga" !important;
  -webkit-font-smoothing: antialiased !important;
  font-feature-settings: "liga" !important;
}

.stApp {
  background: var(--bg) !important;
}

.grading-log-panel {
  max-height: 320px;
  overflow-y: auto;
  white-space: pre-wrap;
  font-family: "DM Mono", ui-monospace, SFMono-Regular, Consolas, monospace;
  font-size: 0.82rem;
  line-height: 1.55;
  padding: 0.85rem 1rem;
  border: 1px solid #E2E8F0;
  border-radius: 16px;
  background: #0F172A;
  color: #CBD5E1;
  box-shadow: 0 10px 30px rgba(15, 23, 42, 0.12);
}

/* Remove default streamlit top padding */
.block-container {
  padding-top: 1.5rem !important;
  padding-left: 2.5rem !important;
  padding-right: 2.5rem !important;
  max-width: 1440px !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   SIDEBAR REDESIGN 鈥?deep slate nav panel
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
section[data-testid="stSidebar"] {
  background: #0F172A !important;
  border-right: 1px solid #1E293B !important;
  padding-top: 0 !important;
}

section[data-testid="stSidebar"] > div {
  padding-top: 0 !important;
}

/* Sidebar brand header */
section[data-testid="stSidebar"]::before {
  content: "GradeMind";
  display: block;
  padding: 1.5rem 1.25rem 1rem;
  font-size: 1.0rem;
  font-weight: 700;
  color: #F8FAFC;
  letter-spacing: -0.01em;
  border-bottom: 1px solid #1E293B;
}

/* Sidebar typography */
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] span,
section[data-testid="stSidebar"] div {
  color: #CBD5E1 !important;
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  color: #F1F5F9 !important;
  font-size: 0.7rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.08em !important;
  text-transform: uppercase !important;
  margin-top: 1.5rem !important;
  margin-bottom: 0.5rem !important;
  padding-bottom: 0.4rem !important;
  border-bottom: 1px solid #1E293B !important;
}

/* Sidebar inputs */
section[data-testid="stSidebar"] .stTextInput input,
section[data-testid="stSidebar"] .stSelectbox select,
section[data-testid="stSidebar"] .stSelectbox > div > div {
  background: #1E293B !important;
  border: 1px solid #334155 !important;
  color: #E2E8F0 !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.85rem !important;
}

section[data-testid="stSidebar"] .stTextInput input:focus {
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 3px var(--brand-glow) !important;
}

/* Sidebar info box */
section[data-testid="stSidebar"] .stAlert {
  background: #1E293B !important;
  border: 1px solid #334155 !important;
  border-radius: var(--radius-sm) !important;
  color: #94A3B8 !important;
}

/* Sidebar buttons */
section[data-testid="stSidebar"] .stButton > button {
  background: #1E293B !important;
  border: 1px solid #334155 !important;
  color: #F8FAFC !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.82rem !important;
  font-weight: 500 !important;
  transition: var(--transition) !important;
  padding: 0.4rem 0.75rem !important;
}

section[data-testid="stSidebar"] .stButton > button:hover {
  background: #2563EB !important;
  border-color: #2563EB !important;
  color: #FFFFFF !important;
  transform: none !important;
}

/* Sidebar expander */
section[data-testid="stSidebar"] .streamlit-expanderHeader {
  background: #1E293B !important;
  border: 1px solid #334155 !important;
  border-radius: var(--radius-sm) !important;
  color: #94A3B8 !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   PAGE HEADER
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 1.25rem 0 1rem;
  margin-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

.app-header-title {
  font-size: 1.35rem;
  font-weight: 700;
  color: var(--text-primary);
  letter-spacing: -0.03em;
  margin: 0;
}

.app-header-sub {
  font-size: 0.82rem;
  color: var(--text-muted);
  margin-top: 0.15rem;
}

.header-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  padding: 0.3rem 0.75rem;
  background: var(--brand-light);
  color: var(--brand);
  border-radius: 999px;
  font-size: 0.75rem;
  font-weight: 600;
  letter-spacing: 0.01em;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   CARD SYSTEM
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 1.5rem;
  box-shadow: var(--shadow-sm);
  transition: var(--transition);
  margin-bottom: 1rem;
}

.card:hover {
  box-shadow: var(--shadow);
  border-color: #CBD5E1;
}

.card-title {
  font-size: 0.78rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.07em;
  margin-bottom: 0.85rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.card-title::before {
  content: '';
  display: inline-block;
  width: 3px;
  height: 12px;
  background: var(--brand);
  border-radius: 2px;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   MAIN CONTENT TYPOGRAPHY
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
h1 { font-size: 1.6rem !important; font-weight: 700 !important; color: var(--text-primary) !important; letter-spacing: -0.03em !important; }
h2 { font-size: 1.15rem !important; font-weight: 650 !important; color: var(--text-primary) !important; letter-spacing: -0.02em !important; }
h3 { font-size: 0.95rem !important; font-weight: 600 !important; color: var(--text-secondary) !important; }

/* Section subheader pills */
.stApp .stMarkdown h2 {
  background: var(--surface) !important;
  padding: 0.5rem 0 !important;
  border-bottom: 2px solid var(--border-subtle) !important;
  margin-bottom: 1rem !important;
}

/* Caption text */
.stApp .stCaption, .stApp p.caption {
  color: var(--text-muted) !important;
  font-size: 0.8rem !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   BUTTON SYSTEM
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
/* Primary brand button */
.stButton > button[kind="primary"],
.stButton > button[data-testid*="primary"],
.stFormSubmitButton > button {
  background: var(--brand) !important;
  color: #FFFFFF !important;
  border: none !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.875rem !important;
  font-weight: 600 !important;
  padding: 0.55rem 1.25rem !important;
  box-shadow: 0 1px 3px rgba(37,99,235,0.3), 0 0 0 0 var(--brand-glow) !important;
  transition: var(--transition) !important;
  letter-spacing: -0.01em !important;
}

.stButton > button[kind="primary"]:hover,
.stFormSubmitButton > button:hover {
  background: var(--brand-dark) !important;
  box-shadow: 0 4px 14px rgba(37,99,235,0.35) !important;
  transform: translateY(-1px) !important;
}

/* Secondary button */
.stButton > button:not([kind="primary"]) {
  background: var(--surface) !important;
  color: var(--text-secondary) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.875rem !important;
  font-weight: 500 !important;
  padding: 0.5rem 1rem !important;
  transition: var(--transition) !important;
  box-shadow: var(--shadow-sm) !important;
}

.stButton > button:not([kind="primary"]):hover {
  background: var(--bg) !important;
  border-color: #CBD5E1 !important;
  color: var(--text-primary) !important;
  box-shadow: var(--shadow) !important;
}

section[data-testid="stSidebar"] .stButton > button * {
  color: inherit !important;
}

.stButton > button *,
.stDownloadButton > button *,
.stFormSubmitButton > button * {
  color: inherit !important;
}

.stDownloadButton > button {
  background: #0F172A !important;
  color: #FFFFFF !important;
  border: 1px solid #0F172A !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.875rem !important;
  font-weight: 650 !important;
  padding: 0.55rem 1.25rem !important;
  box-shadow: var(--shadow-sm) !important;
}

.stDownloadButton > button:hover {
  background: #1E293B !important;
  border-color: #1E293B !important;
  color: #FFFFFF !important;
}

.stButton > button:disabled,
.stDownloadButton > button:disabled {
  color: #94A3B8 !important;
  background: #F1F5F9 !important;
  border-color: #E2E8F0 !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   FORM INPUTS
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stTextInput input,
.stTextArea textarea,
.stSelectbox > div > div {
  border: 1.5px solid var(--border) !important;
  border-radius: var(--radius-sm) !important;
  font-size: 0.88rem !important;
  color: var(--text-primary) !important;
  background: var(--surface) !important;
  transition: var(--transition) !important;
  box-shadow: var(--shadow-sm) !important;
}

.stTextInput input:focus,
.stTextArea textarea:focus {
  border-color: var(--brand) !important;
  box-shadow: 0 0 0 3px var(--brand-glow), var(--shadow-sm) !important;
  outline: none !important;
}

.stTextInput label,
.stTextArea label,
.stSelectbox label,
.stFileUploader label {
  font-size: 0.8rem !important;
  font-weight: 600 !important;
  color: var(--text-secondary) !important;
  letter-spacing: 0.01em !important;
}

.gm-flow {
  display: grid;
  gap: .42rem;
  margin-top: .65rem;
}
.gm-flow-step {
  display: grid;
  grid-template-columns: 1.35rem 1fr;
  gap: .55rem;
  align-items: start;
  padding: .48rem .55rem;
  border-radius: 10px;
  background: rgba(15, 23, 42, .28);
  border: 1px solid rgba(148, 163, 184, .18);
}
.gm-flow-dot {
  width: 1.1rem;
  height: 1.1rem;
  border-radius: 999px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: .62rem;
  font-weight: 800;
  margin-top: .1rem;
}
.gm-flow-done .gm-flow-dot { background: #10B981; color: #FFFFFF; }
.gm-flow-current .gm-flow-dot { background: #2563EB; color: #FFFFFF; box-shadow: 0 0 0 4px rgba(37,99,235,.14); }
.gm-flow-pending .gm-flow-dot { background: #334155; color: #94A3B8; }
.gm-flow-title { color: #F8FAFC; font-size: .76rem; font-weight: 700; line-height: 1.2; }
.gm-flow-desc { color: #94A3B8; font-size: .69rem; line-height: 1.35; margin-top: .1rem; }

.kg-map {
  margin-top: .9rem;
  padding: 1.1rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background:
    radial-gradient(circle at 22% 15%, rgba(37,99,235,.10), transparent 28%),
    radial-gradient(circle at 82% 30%, rgba(239,68,68,.10), transparent 24%),
    #FFFFFF;
  box-shadow: var(--shadow-sm);
}
.kg-center {
  display: inline-flex;
  padding: .48rem .78rem;
  border-radius: 999px;
  background: #0F172A;
  color: #FFFFFF;
  font-weight: 750;
  font-size: .85rem;
  margin-bottom: .85rem;
}
.kg-nodes {
  display: flex;
  flex-wrap: wrap;
  gap: .7rem;
}
.kg-node {
  min-width: 132px;
  padding: .72rem .82rem;
  border-radius: 16px;
  border: 1px solid var(--border);
  background: #F8FAFC;
}
.kg-node-id { font-weight: 800; letter-spacing: .02em; color: #0F172A; }
.kg-node-meta { margin-top: .18rem; font-size: .72rem; color: #64748B; }
.kg-weak {
  border-color: #FCA5A5;
  background: #FEF2F2;
}
.kg-weak .kg-node-id { color: #DC2626; }
.kg-critical {
  border-color: #EF4444;
  background: linear-gradient(135deg, #FEF2F2, #FFF7ED);
  box-shadow: 0 10px 26px rgba(220,38,38,.12);
}
.kg-critical .kg-node-id { color: #B91C1C; }
.kg-ok {
  border-color: #BBF7D0;
  background: #F0FDF4;
}
.kg-ok .kg-node-id { color: #047857; }

.kg-tree {
  margin-top: .9rem;
  padding: 1.2rem;
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(135deg, rgba(248,250,252,.98), rgba(255,255,255,.94)),
    radial-gradient(circle at 18% 12%, rgba(37,99,235,.10), transparent 30%);
  box-shadow: var(--shadow-sm);
  overflow-x: auto;
}
.kg-tree-root {
  display: inline-flex;
  align-items: center;
  padding: .62rem 1rem;
  border-radius: 999px;
  background: #0F172A;
  color: #FFFFFF;
  font-weight: 850;
  letter-spacing: -.02em;
}
.kg-tree-body {
  display: grid;
  gap: .8rem;
  margin-top: 1rem;
}
.kg-student-branch {
  display: grid;
  grid-template-columns: minmax(150px, 220px) 1fr;
  gap: .9rem;
  align-items: start;
  padding-left: .7rem;
  border-left: 2px solid #CBD5E1;
}
.kg-student {
  position: relative;
  padding: .62rem .78rem;
  border-radius: 16px;
  background: #EFF6FF;
  border: 1px solid #BFDBFE;
  color: #1D4ED8;
  font-weight: 800;
}
.kg-student::before {
  content: "";
  position: absolute;
  left: -.72rem;
  top: 1.05rem;
  width: .72rem;
  border-top: 2px solid #CBD5E1;
}
.kg-knowledge-list {
  display: flex;
  flex-wrap: wrap;
  gap: .58rem;
  padding-top: .1rem;
}
.kg-leaf {
  min-width: 172px;
  max-width: 260px;
  padding: .6rem .72rem;
  border-radius: 15px;
  border: 1px solid #E2E8F0;
  box-shadow: 0 1px 2px rgba(15,23,42,.04);
  display: block;
  text-decoration: none !important;
  transition: transform .16s ease, box-shadow .16s ease;
}
.kg-leaf:hover {
  transform: translateY(-2px);
  box-shadow: 0 10px 24px rgba(15,23,42,.10);
}
.kg-leaf-title {
  font-size: .88rem;
  line-height: 1.35;
  font-weight: 850;
  color: #0F172A;
}
.kg-leaf-meta { margin-top: .12rem; font-size: .7rem; color: #64748B; }
.kg-level-green { background: #ECFDF5; border-color: #86EFAC; }
.kg-level-green .kg-leaf-title { color: #047857; }
.kg-level-blue { background: #EFF6FF; border-color: #93C5FD; }
.kg-level-blue .kg-leaf-title { color: #1D4ED8; }
.kg-level-yellow { background: #FFFBEB; border-color: #FCD34D; }
.kg-level-yellow .kg-leaf-title { color: #B45309; }
.kg-level-red { background: #FEF2F2; border-color: #FCA5A5; }
.kg-level-red .kg-leaf-title { color: #DC2626; }
.kg-detail-hero {
  margin: .8rem 0 1rem;
  padding: 1.15rem 1.25rem;
  border-radius: 24px;
  border: 1px solid #DBEAFE;
  background:
    radial-gradient(circle at 8% 10%, rgba(37,99,235,.12), transparent 28%),
    linear-gradient(135deg, #FFFFFF, #F8FAFC);
  box-shadow: var(--shadow-sm);
}
.kg-detail-eyebrow {
  font-size: .72rem;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: #2563EB;
  font-weight: 850;
}
.kg-detail-title {
  margin-top: .25rem;
  font-size: 1.25rem;
  font-weight: 900;
  color: #0F172A;
}
.kg-detail-subtitle {
  margin-top: .18rem;
  color: #475569;
  font-weight: 650;
}
.kg-detail-metrics {
  display: flex;
  flex-wrap: wrap;
  gap: .45rem;
  margin-top: .75rem;
}
.kg-detail-metrics span {
  padding: .36rem .62rem;
  border-radius: 999px;
  background: #EFF6FF;
  color: #1D4ED8;
  font-size: .78rem;
  font-weight: 750;
}

.global-back-top {
  display: inline-flex;
  margin-top: 1.25rem;
  padding: .62rem .9rem;
  border-radius: 999px;
  background: #0F172A;
  color: #FFFFFF !important;
  text-decoration: none !important;
  font-weight: 800;
  box-shadow: var(--shadow-sm);
}
.global-back-top:hover {
  background: #1E293B;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   FILE UPLOADER
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stFileUploader > div {
  border: 2px dashed var(--border) !important;
  border-radius: var(--radius) !important;
  background: var(--bg) !important;
  padding: 1.25rem !important;
  transition: var(--transition) !important;
}

.stFileUploader > div:hover {
  border-color: var(--brand) !important;
  background: var(--brand-light) !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   TABS 鈥?clean underline style
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stTabs [data-baseweb="tab-list"] {
  background: transparent !important;
  border-bottom: 2px solid var(--border) !important;
  gap: 0 !important;
  padding: 0 !important;
}

.stTabs [data-baseweb="tab"] {
  background: transparent !important;
  border: none !important;
  border-bottom: 2px solid transparent !important;
  border-radius: 0 !important;
  color: var(--text-muted) !important;
  font-size: 0.875rem !important;
  font-weight: 500 !important;
  padding: 0.6rem 1.25rem !important;
  margin-bottom: -2px !important;
  transition: var(--transition) !important;
}

.stTabs [data-baseweb="tab"]:hover {
  color: var(--text-primary) !important;
  background: var(--bg) !important;
}

.stTabs [aria-selected="true"][data-baseweb="tab"] {
  color: var(--brand) !important;
  border-bottom-color: var(--brand) !important;
  font-weight: 600 !important;
}

.stTabs [data-baseweb="tab-panel"] {
  padding-top: 1.5rem !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   DATAFRAME / TABLE
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stDataFrame, .stDataEditor {
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  overflow: hidden !important;
  box-shadow: var(--shadow-sm) !important;
}

/* Table header */
[data-testid="stDataFrame"] th,
[data-testid="stDataEditor"] th {
  background: #F8FAFC !important;
  color: var(--text-secondary) !important;
  font-size: 0.75rem !important;
  font-weight: 600 !important;
  letter-spacing: 0.04em !important;
  text-transform: uppercase !important;
  border-bottom: 1px solid var(--border) !important;
  padding: 0.6rem 0.85rem !important;
}

/* Table cells */
[data-testid="stDataFrame"] td,
[data-testid="stDataEditor"] td {
  font-size: 0.855rem !important;
  color: var(--text-primary) !important;
  padding: 0.6rem 0.85rem !important;
  border-bottom: 1px solid var(--border-subtle) !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   METRIC CARDS
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
[data-testid="stMetric"] {
  background: var(--surface) !important;
  border: 1px solid var(--border) !important;
  border-radius: var(--radius) !important;
  padding: 1rem 1.25rem !important;
  box-shadow: var(--shadow-sm) !important;
  transition: var(--transition) !important;
}

[data-testid="stMetric"]:hover {
  box-shadow: var(--shadow) !important;
  border-color: var(--brand) !important;
}

[data-testid="stMetricLabel"] {
  font-size: 0.72rem !important;
  font-weight: 600 !important;
  color: var(--text-muted) !important;
  text-transform: uppercase !important;
  letter-spacing: 0.06em !important;
}

[data-testid="stMetricValue"] {
  font-size: 1.75rem !important;
  font-weight: 700 !important;
  color: var(--text-primary) !important;
  letter-spacing: -0.03em !important;
  line-height: 1.1 !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   ALERT / NOTIFICATION BANNERS 鈫?elegant badges
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stAlert[data-baseweb="notification"] {
  border-radius: var(--radius-sm) !important;
  border: 1px solid var(--border) !important;
  box-shadow: none !important;
  font-size: 0.85rem !important;
  padding: 0.7rem 1rem !important;
}

/* Success */
div[data-testid="stAlert"][kind="success"],
.stSuccess {
  background: var(--success-bg) !important;
  border-left: 3px solid var(--success) !important;
  color: #065F46 !important;
}

/* Warning */
div[data-testid="stAlert"][kind="warning"],
.stWarning {
  background: var(--warning-bg) !important;
  border-left: 3px solid var(--warning) !important;
  color: #92400E !important;
}

/* Error */
div[data-testid="stAlert"][kind="error"],
.stError {
  background: var(--danger-bg) !important;
  border-left: 3px solid var(--danger) !important;
  color: #991B1B !important;
}

/* Info */
div[data-testid="stAlert"][kind="info"],
.stInfo {
  background: var(--brand-light) !important;
  border-left: 3px solid var(--brand) !important;
  color: #1E40AF !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   PROGRESS BAR
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stProgress > div > div > div > div {
  background: linear-gradient(90deg, var(--brand), #60A5FA) !important;
  border-radius: 999px !important;
}

.stProgress > div > div {
  background: var(--border-subtle) !important;
  border-radius: 999px !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   DIVIDER
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
hr {
  border: none !important;
  border-top: 1px solid var(--border-subtle) !important;
  margin: 1.25rem 0 !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   SPINNER
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stSpinner > div {
  border-top-color: var(--brand) !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   CODE BLOCKS (log output)
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stCode, code, pre {
  font-family: 'DM Mono', 'SF Mono', monospace !important;
  font-size: 0.8rem !important;
  background: #0F172A !important;
  color: #94A3B8 !important;
  border-radius: var(--radius-sm) !important;
  border: 1px solid #1E293B !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   CHECKBOX
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stCheckbox > label {
  font-size: 0.875rem !important;
  color: var(--text-secondary) !important;
  font-weight: 500 !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   CUSTOM STATUS BADGE UTILITY
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.badge {
  display: inline-flex;
  align-items: center;
  padding: 0.2rem 0.6rem;
  border-radius: 999px;
  font-size: 0.7rem;
  font-weight: 600;
  letter-spacing: 0.02em;
}
.badge-blue   { background: var(--brand-light);  color: var(--brand); }
.badge-green  { background: var(--success-bg);   color: var(--success); }
.badge-yellow { background: var(--warning-bg);   color: var(--warning); }
.badge-red    { background: var(--danger-bg);    color: var(--danger); }
.badge-gray   { background: #F1F5F9;             color: #64748B; }

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   PAGE SECTION HEADING STYLE
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.section-heading {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  font-size: 0.78rem;
  font-weight: 700;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 1rem;
  padding-bottom: 0.6rem;
  border-bottom: 1px solid var(--border);
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   CANVAS CONTAINER
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.canvas-wrapper {
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   SCROLLBAR
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #CBD5E1; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #94A3B8; }

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   RESPONSIVE COLUMN GAPS
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
[data-testid="stHorizontalBlock"] {
  gap: 1.25rem !important;
  align-items: flex-start !important;
}

/* 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
   SUBHEADER OVERRIDES
鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?*/
.stApp [data-testid="stMarkdownContainer"] h2 {
  font-size: 1.1rem !important;
  font-weight: 650 !important;
  letter-spacing: -0.02em !important;
  color: var(--text-primary) !important;
  padding: 0 !important;
  border: none !important;
}

/* Fix subheader element */
.stSubheader, .stApp div[data-testid="stSubheader"] p {
  font-size: 1.05rem !important;
  font-weight: 700 !important;
  letter-spacing: -0.02em !important;
  color: var(--text-primary) !important;
}

</style>
"""


def _inject_css() -> None:
    st.markdown(_DESIGN_CSS, unsafe_allow_html=True)


def _card_start(title: str, icon: str = "") -> None:
    prefix = f"{icon} " if icon else ""
    st.markdown(
        f'<div class="card"><div class="card-title">{prefix}{title}</div>',
        unsafe_allow_html=True,
    )


def _card_end() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def _status_badge(text: str, kind: str = "gray") -> str:
    return f'<span class="badge badge-{kind}">{text}</span>'


def _section_heading(title: str, icon: str = "") -> None:
    st.markdown(
        f'<div class="section-heading"><span>{icon}</span>{title}</div>',
        unsafe_allow_html=True,
    )


def render_sidebar_session_selector(db: DBManager) -> int | None:
    st.sidebar.header("考试批改")
    st.sidebar.caption(f"本地数据目录：{APP_DATA_DIR}")
    st.sidebar.caption(f"主库文件：{DB_PATH}")

    active_sessions = db.list_grading_sessions(include_deleted=False)
    options: list[int | None] = [None] + [int(s["id"]) for s in active_sessions]

    default_value = st.session_state.get("selected_session_id")
    if default_value not in options:
        default_value = None

    selector_col, delete_col = st.sidebar.columns([5, 1.2])
    with selector_col:
        selected = st.selectbox(
            "当前考试批改",
            options=options,
            index=options.index(default_value) if default_value in options else 0,
            format_func=lambda sid: "未选择" if sid is None else _session_label(int(sid), active_sessions),
        )
    with delete_col:
        st.caption(" ")
        delete_clicked = st.button("删除", key="delete_selected_session_btn", use_container_width=True, disabled=selected is None)

    if delete_clicked and selected is not None:
        db.soft_delete_grading_session(int(selected))
        st.session_state["selected_session_id"] = None
        db.set_app_setting("last_selected_session_id", "")
        st.sidebar.success("考试批改已删除")
        st.rerun()

    st.session_state["selected_session_id"] = selected
    db.set_app_setting("last_selected_session_id", "" if selected is None else str(selected))

    if selected is not None:
        session = db.get_grading_session(int(selected))
        if session:
            stage = _session_stage_summary(db, int(selected))
            st.sidebar.markdown(
                f"""
                <div class="gm-sidebar-summary">
                  <div class="gm-sidebar-summary-title">褰撳墠杩涘害</div>
                  <div class="gm-sidebar-summary-name">{html.escape(str(session['session_name']))}</div>
                  <div class="gm-sidebar-summary-meta">{_status_badge(stage['label'], stage['kind'])}</div>
                  <div style="margin-top:.5rem;color:#CBD5E1;font-size:.76rem;line-height:1.55;">
                    {html.escape(stage['detail'])}
                  </div>
                  {_session_flow_html(db, int(selected))}
                </div>
                """,
                unsafe_allow_html=True,
            )

            st.sidebar.markdown("### 考试批改管理")
            rename_key = f"rename_input_{selected}"
            if rename_key not in st.session_state:
                st.session_state[rename_key] = str(session["session_name"])
            rename_value = st.sidebar.text_input("重命名当前考试", key=rename_key)
            if st.sidebar.button("保存新名称", use_container_width=True, key=f"save_rename_{selected}", type="primary"):
                new_name = str(rename_value or "").strip()
                if not new_name:
                    st.sidebar.error("考试名称不能为空")
                else:
                    db.rename_grading_session(int(selected), new_name)
                    st.sidebar.success("考试批改已重命名")
                    st.rerun()
    deleted_sessions = [s for s in db.list_grading_sessions(include_deleted=True) if int(s.get("is_deleted", 0)) == 1]
    with st.sidebar.expander("回收站", expanded=False):
        if not deleted_sessions:
            st.caption("回收站为空")
        for s in deleted_sessions:
            sid = int(s["id"])
            st.write(f"#{sid} {s['session_name']}")
            confirm_hard_delete = st.checkbox(
                "确认彻底删除此批改及全部数据",
                key=f"hard_delete_confirm_{sid}",
            )
            if st.button(
                "彻底删除",
                key=f"hard_delete_{sid}",
                disabled=not confirm_hard_delete,
                use_container_width=True,
            ):
                try:
                    stats = hard_delete_session_from_recycle_bin(db, sid, data_root=APP_DATA_DIR)
                    st.success(
                        f"已彻底删除 #{sid}，清理 {stats['deleted_files']} 个文件、"
                        f"{stats['deleted_dirs']} 个目录"
                    )
                    if stats.get("failed_paths"):
                        st.warning("部分文件清理失败，请稍后重试或手动检查。")
                except Exception as exc:
                    st.error(f"彻底删除失败: {exc}")
                st.rerun()
            if st.button("恢复", key=f"restore_{sid}"):
                db.restore_grading_session(sid)
                st.success(f"已恢复 #{sid}")
                st.rerun()

    return int(selected) if selected is not None else None


def render_sidebar_grading_mode(selected_session_id: int | None) -> str:
    st.sidebar.markdown("---")
    st.sidebar.markdown("### 批改模式")
    enabled = st.sidebar.checkbox(
        "混合批改（试用）",
        value=False,
        key=f"hybrid_batch_mode_{selected_session_id or 'none'}",
        disabled=selected_session_id is None,
        help="选择填空优先走本地客观题识别；低置信和大题按题号批量发送给 AI；失败项进入人工复核。",
    )
    if enabled:
        st.sidebar.caption("试用模式：客观题本地优先，大题按题号横批；低置信或失败进入人工复核。")
        return "hybrid_batch"
    st.sidebar.caption("默认模式：主批改继续发送整张试卷正反面。")
    return "full_paper"

def _session_stage_summary(db: DBManager, session_id: int) -> dict[str, str]:
    template = db.get_session_template(session_id)
    progress = db.get_session_progress(session_id)
    total_papers = int(progress.get("total_papers", 0))
    graded_papers = int(progress.get("graded_papers", 0))
    failed_papers = int(progress.get("failed_papers", 0))
    active_papers = int(progress.get("grading_papers", 0))

    if total_papers > 0 and graded_papers + failed_papers >= total_papers:
        return {
            "label": "批改完成",
            "kind": "green",
            "detail": f"已处理 {graded_papers}/{total_papers} 份，失败 {failed_papers} 份。",
        }
    if total_papers > 0 and graded_papers == 0 and failed_papers == 0 and active_papers == 0:
        return {
            "label": "试卷已读取",
            "kind": "blue",
            "detail": f"已读取 {total_papers} 份试卷，等待正式批改。",
        }
    if total_papers > 0 or active_papers > 0:
        return {
            "label": "批改中",
            "kind": "blue",
            "detail": f"已完成 {graded_papers}/{max(total_papers, 1)} 份，待复核 {progress.get('needs_human_review', 0)} 份。",
        }
    if template and db.is_template_ready(session_id):
        return {
            "label": "样卷已标定",
            "kind": "green",
            "detail": "Word 评分标准与样卷题框映射已确认，可以开始正式批改。",
        }
    if template:
        return {
            "label": "样卷待确认",
            "kind": "yellow",
            "detail": "已上传样卷，请继续确认题框映射。评分标准来自 Word，不在样卷阶段修改。",
        }
    return {
        "label": "待上传样卷",
        "kind": "gray",
        "detail": "已创建考试批改，请继续上传正反面样卷。",
    }


def _session_flow_html(db: DBManager, session_id: int) -> str:
    template = db.get_session_template(session_id)
    ready = db.is_template_ready(session_id)
    progress = db.get_session_progress(session_id)
    total_papers = int(progress.get("total_papers", 0))
    graded_papers = int(progress.get("graded_papers", 0))
    failed_papers = int(progress.get("failed_papers", 0))

    steps = [
        ("创建考试", True, "评分依据与考试批改已建立"),
        ("上传样卷", bool(template), "上传正反面样卷，建立版面映射区"),
        ("标定题框", ready, "人工确认每题答题区域和题号映射"),
        ("批改试卷", total_papers > 0 and graded_papers + failed_papers >= total_papers, f"已完成 {graded_papers}/{max(total_papers, 1)}"),
        ("审阅导出", total_papers > 0 and graded_papers + failed_papers >= total_papers, "复核结果、导出报表与原卷"),
    ]
    first_pending = next((idx for idx, (_, done, _) in enumerate(steps) if not done), len(steps) - 1)

    items: list[str] = []
    for idx, (title, done, desc) in enumerate(steps):
        state = "done" if done else ("current" if idx == first_pending else "pending")
        dot = "✓" if done else str(idx + 1)
        items.append(
            f"""
            <div class="gm-flow-step gm-flow-{state}">
              <div class="gm-flow-dot">{dot}</div>
              <div>
                <div class="gm-flow-title">{html.escape(title)}</div>
                <div class="gm-flow-desc">{html.escape(desc)}</div>
              </div>
            </div>
            """
        )
    return f'<div class="gm-flow">{"".join(items)}</div>'


def _session_label(session_id: int, sessions: list[dict[str, Any]]) -> str:
    for row in sessions:
        if int(row["id"]) == session_id:
            return f"#{row['id']} {row['session_name']} ({row['status']})"
    return str(session_id)


def render_student_import_section(db: DBManager) -> None:
    st.subheader("学生数据（全局）")
    upload = st.file_uploader("上传学生名单（Excel / CSV）", type=["csv", "xlsx", "xls"], key="student_list_uploader")

    if upload is not None:
        try:
            records = StudentManager.load_students(upload.name, upload.getvalue())
            preview_df = pd.DataFrame(
                [{"student_code": r.student_code, "name": r.name, "class_name": r.class_name} for r in records]
            )
            st.dataframe(preview_df, use_container_width=True, hide_index=True)

            if st.button("写入学生库", key="import_students_btn", type="primary"):
                result = db.upsert_students(records)
                st.success(f"导入完成：新增 {result['inserted']}，更新 {result['updated']}，合计变更 {result['total']}")
        except Exception as exc:  # noqa: BLE001
            st.error(f"学生名单解析失败：{exc}")

    students = db.list_students()
    st.markdown("**当前学生库**")
    if not students:
        st.info("当前学生库为空。导入名单后，可在这里直接修改或删除学生。")
        return

    editor_rows = [
        {
            "id": int(student["id"]),
            "student_code": str(student.get("student_code") or ""),
            "name": str(student.get("name") or ""),
            "class_name": str(student.get("class_name") or ""),
            "删除": False,
        }
        for student in students
    ]
    original_rows = {row["id"]: row for row in editor_rows}
    edited_students = st.data_editor(
        pd.DataFrame(editor_rows),
        use_container_width=True,
        hide_index=True,
        num_rows="fixed",
        disabled=["id"],
        key="global_student_editor",
        column_config={
            "id": st.column_config.NumberColumn("ID", width="small"),
            "student_code": st.column_config.TextColumn("学号", required=True),
            "name": st.column_config.TextColumn("姓名", required=True),
            "class_name": st.column_config.TextColumn("班级"),
            "删除": st.column_config.CheckboxColumn("删除", help="勾选后点击下方删除按钮。删除会清除该学生历史批改结果。"),
        },
    )

    save_col, delete_col = st.columns([1, 1])
    with save_col:
        if st.button("保存学生修改", key="save_global_student_edits", type="primary", use_container_width=True):
            try:
                updated = 0
                for row in edited_students.to_dict("records"):
                    student_id = int(row["id"])
                    original = original_rows[student_id]
                    next_values = {
                        "student_code": _student_cell_text(row.get("student_code")),
                        "name": _student_cell_text(row.get("name")),
                        "class_name": _student_cell_text(row.get("class_name")),
                    }
                    current_values = {
                        "student_code": _student_cell_text(original.get("student_code")),
                        "name": _student_cell_text(original.get("name")),
                        "class_name": _student_cell_text(original.get("class_name")),
                    }
                    if next_values == current_values:
                        continue
                    db.update_student(
                        student_id,
                        next_values["student_code"],
                        next_values["name"],
                        next_values["class_name"],
                    )
                    updated += 1
                if updated:
                    st.success(f"已保存 {updated} 名学生的修改。")
                    st.rerun()
                else:
                    st.info("没有检测到需要保存的学生修改。")
            except Exception as exc:  # noqa: BLE001
                st.error(f"保存学生修改失败：{exc}")

    delete_ids = [
        int(row["id"])
        for row in edited_students.to_dict("records")
        if bool(row.get("删除"))
    ]
    with delete_col:
        confirm_delete = st.checkbox(
            "确认硬删除勾选学生",
            key="confirm_global_student_delete",
            help="会删除这些学生的历史成绩、题目明细、批注和出勤记录，且不可恢复。",
        )
        if st.button(
            f"删除勾选学生（{len(delete_ids)}）",
            key="delete_global_students",
            use_container_width=True,
            disabled=not delete_ids,
        ):
            if not confirm_delete:
                st.warning("请先勾选“确认硬删除勾选学生”。")
            else:
                try:
                    deleted_students = 0
                    deleted_results = 0
                    for student_id in delete_ids:
                        result = db.delete_student_hard(student_id)
                        deleted_students += int(result.get("deleted_students", 0))
                        deleted_results += int(result.get("deleted_results", 0))
                    st.success(f"已硬删除 {deleted_students} 名学生，并清除 {deleted_results} 条批改结果。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"删除学生失败：{exc}")


def _student_cell_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _render_upload_paths_guide(is_pdf: bool | None, use_text_only: bool) -> None:
    active_idx = -1
    if is_pdf is not None:
        if not use_text_only:
            active_idx = 0 if is_pdf else 1
        else:
            active_idx = 2 if is_pdf else 3

    status_html = ""
    if is_pdf is None:
        status_html = '<div class="status-prompt">💡 <b>使用提示</b>：请在上方上传试卷（.docx 或 .pdf）并选择模式，系统将自动激活匹配的解析路径。</div>'
    else:
        file_type = "PDF" if is_pdf else "Word"
        mode_type = "整卷单次请求" if use_text_only else "单题并发 (推荐)"
        status_html = f'<div class="status-prompt" style="background: #ECFDF5; color: #065F46; border: 1px solid #A7F3D0; margin-bottom: 14px;">✅ <b>当前激活路径</b>：您上传了 <b>{file_type}</b> 文件，选择了 <b>{mode_type}</b> 模式，已激活下方高亮路径。</div>'

    def get_class(idx: int) -> str:
        if active_idx == -1:
            return "path-card"
        return "path-card active" if active_idx == idx else "path-card inactive"

    if not use_text_only:
        cards_html = f"""
        <div class="path-card {get_class(0)}">
            { '<span class="active-badge">当前生效</span>' if active_idx == 0 else '' }
            <div class="path-card-title">📝🖼️ 单题并发 + PDF 格式</div>
            <div class="path-card-mode-badge">多模态视觉裁图模式</div>
            <div class="flow-steps">
                <span class="step-node">PDF文件</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">本地拆题</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">物理截图</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">AI 视觉分析</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">结构合并</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">统一赋分</span>
            </div>
            <div class="path-card-desc">
                <b>适用场景</b>：试卷包含几何图形、函数图表、较多插图，或扫描版试卷。<br>
                <b>核心机制</b>：AI 仅接收裁切出的题目/答案高清图和少量规则；PDF 抽取文字仅用于本地拆题定位，不发送给 AI。
            </div>
        </div>
        <div class="path-card {get_class(1)}">
            { '<span class="active-badge">当前生效</span>' if active_idx == 1 else '' }
            <div class="path-card-title">📝✍️ 单题并发 + Word 格式</div>
            <div class="path-card-mode-badge">文本与公式混合模式</div>
            <div class="flow-steps">
                <span class="step-node">Word文件</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">富文本拆题</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">提取HTML公式</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">AI 文本分析</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">结构合并</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">统一赋分</span>
            </div>
            <div class="path-card-desc">
                <b>适用场景</b>：试卷由排版规范的 DOCX 文档生成，且包含不少大题步骤。<br>
                <b>核心机制</b>：AI 接收每道题的富文本、公式 HTML 和可用的内嵌图片；失败题支持自动与手动重试。
            </div>
        </div>
        """
    else:
        cards_html = f"""
        <div class="path-card {get_class(2)}">
            { '<span class="active-badge">当前生效</span>' if active_idx == 2 else '' }
            <div class="path-card-title">📝🖼️ PDF 整卷视觉单次请求</div>
            <div class="path-card-mode-badge">真正单次视觉请求</div>
            <div class="flow-steps">
                <span class="step-node">PDF文件</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">逐页渲染图片</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">AI 一次性解析与赋分</span>
            </div>
            <div class="path-card-desc">
                <b>适用场景</b>：本地无法可靠拆题，或需要让 AI 从整卷上下文判断题目与答案对应关系。<br>
                <b>核心机制</b>：一次性发送全部 PDF 整页图片，不发送 PDF 抽取文字；失败后仅手动重试。
            </div>
        </div>
        <div class="path-card {get_class(3)}">
            { '<span class="active-badge">当前生效</span>' if active_idx == 3 else '' }
            <div class="path-card-title">📝📄 Word 整卷文本单次请求</div>
            <div class="path-card-mode-badge">真正单次文本请求</div>
            <div class="flow-steps">
                <span class="step-node">Word文件</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">提取整卷文本</span>
                <span class="flow-arrow">➔</span>
                <span class="step-node">AI 一次性解析与赋分</span>
            </div>
            <div class="path-card-desc">
                <b>适用场景</b>：排版极为简单、无图无公式的纯客观题试卷。<br>
                <b>核心机制</b>：一次性将整卷文本发给 AI，不自动修复或补请求；失败后仅手动重试。
            </div>
        </div>
        """

    html_content = f"""
    <style>
    .paths-guide-container {{
        background: #F8FAFC;
        border: 1px solid #E2E8F0;
        border-radius: 12px;
        padding: 16px;
        margin: 16px 0;
    }}
    .paths-guide-header {{
        font-size: 0.95rem;
        font-weight: 600;
        color: #1E293B;
        margin-bottom: 12px;
        display: flex;
        align-items: center;
        gap: 8px;
    }}
    .status-prompt {{
        padding: 10px 14px;
        border-radius: 8px;
        font-size: 0.85rem;
        background: #F1F5F9;
        color: #475569;
        margin-bottom: 12px;
        border: 1px solid #E2E8F0;
    }}
    .paths-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
        gap: 12px;
    }}
    .path-card {{
        border: 1px solid #E2E8F0;
        border-radius: 10px;
        padding: 14px;
        background: #FFFFFF;
        transition: all 0.3s ease;
        position: relative;
    }}
    .path-card.inactive {{
        opacity: 0.5;
    }}
    .path-card.active {{
        border-color: #3B82F6;
        box-shadow: 0 4px 12px rgba(59, 130, 246, 0.1);
        background: #F0F7FF;
    }}
    .active-badge {{
        position: absolute;
        top: 10px;
        right: 10px;
        background: #3B82F6;
        color: #FFFFFF;
        font-size: 0.75rem;
        padding: 2px 6px;
        border-radius: 4px;
        font-weight: 500;
    }}
    .path-card-title {{
        font-size: 0.9rem;
        font-weight: 600;
        color: #0F172A;
        margin-bottom: 4px;
    }}
    .path-card-mode-badge {{
        display: inline-block;
        font-size: 0.75rem;
        color: #475569;
        background: #E2E8F0;
        padding: 2px 6px;
        border-radius: 4px;
        margin-bottom: 10px;
    }}
    .path-card.active .path-card-mode-badge {{
        background: #DBEAFE;
        color: #1E40AF;
    }}
    .flow-steps {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 4px;
        font-size: 0.75rem;
        margin-bottom: 10px;
        background: #F8FAFC;
        padding: 6px;
        border-radius: 6px;
    }}
    .step-node {{
        background: #FFFFFF;
        border: 1px solid #E2E8F0;
        padding: 2px 6px;
        border-radius: 4px;
        color: #334155;
    }}
    .path-card.active .step-node {{
        border-color: #BFDBFE;
    }}
    .flow-arrow {{
        color: #94A3B8;
    }}
    .path-card-desc {{
        font-size: 0.75rem;
        color: #64748B;
        line-height: 1.4;
    }}
    </style>
    <div class="paths-guide-container">
        <div class="paths-guide-header">
            🗺️ 试卷解析与评分细则生成路径指南
        </div>
        {status_html}
        <div class="paths-grid">
            {cards_html}
        </div>
    </div>
    """
    cleaned_content = "".join([line.strip() for line in html_content.splitlines() if line.strip()])
    st.markdown(cleaned_content, unsafe_allow_html=True)



def _persistent_rubric_source(
    db: DBManager,
    selected_session_id: int | None,
) -> tuple[Path, Path, str, int | None] | None:
    if selected_session_id is not None:
        session = db.get_grading_session(int(selected_session_id))
        if session and int(session.get("is_deleted") or 0) == 0:
            return (
                Path(str(session.get("rubric_path") or "")),
                Path(str(session.get("answer_key_path") or "")),
                f"当前考试：{session.get('session_name') or selected_session_id}",
                int(selected_session_id),
            )

    rubric_path = str(st.session_state.get("latest_rubric_path") or "").strip()
    answer_path = str(st.session_state.get("latest_answer_path") or "").strip()
    if rubric_path and answer_path:
        return Path(rubric_path), Path(answer_path), "最近确认保存的评分标准", None
    return None


def _load_rubric_payload(rubric_path: Path, answer_key_path: Path) -> dict[str, Any]:
    rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
    answer_key = json.loads(answer_key_path.read_text(encoding="utf-8"))
    payload = {"rubric": rubric, "answer_key": answer_key}
    _session_manager.normalize_generated_config_schema(payload)
    return payload


def _render_persistent_rubric_overview(db: DBManager, selected_session_id: int | None) -> None:
    st.markdown("### 当前评分标准总览")
    source = _persistent_rubric_source(db, selected_session_id)
    if source is None:
        st.info("尚未确认保存评分标准。生成并确认后，总览会常驻在这里。")
        return

    rubric_path, answer_path, source_label, source_session_id = source
    if not rubric_path.exists() or not answer_path.exists():
        st.warning(f"{source_label}的评分标准文件缺失，请重新确认保存。")
        return

    try:
        payload = _load_rubric_payload(rubric_path, answer_path)
        rows = build_unified_rubric_rows(payload)
    except Exception as exc:  # noqa: BLE001
        st.error(f"加载常驻评分标准失败：{exc}")
        return

    rubric = payload.get("rubric") if isinstance(payload, dict) else {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    total_score = _to_float(rubric.get("total_score"), 0.0) if isinstance(rubric, dict) else 0.0
    st.caption(
        f"{source_label} · {len(questions) if isinstance(questions, list) else 0} 道题 · "
        f"总分 {total_score:g} · 已持久保存，重新打开首页仍会显示"
    )
    visible_columns = [
        "题号",
        "评分单元",
        "评分点",
        "题型",
        "分值",
        "标准答案",
        "作答匹配规则",
        "知识点",
    ]
    overview_df = pd.DataFrame(rows)
    if overview_df.empty:
        st.info("当前评分标准中暂无可展示的评分行。")
        return
    st.dataframe(
        overview_df[[column for column in visible_columns if column in overview_df.columns]],
        use_container_width=True,
        hide_index=True,
        height=min(620, max(220, 38 * (len(overview_df) + 1))),
    )

    edit_key = f"persistent_rubric_edit_{source_session_id if source_session_id is not None else rubric_path.name}"
    if st.toggle("编辑当前评分标准", value=False, key=edit_key):
        _render_active_session_rubric_editor(
            db,
            source_session_id,
            rubric_path,
            answer_path,
            payload,
        )
    st.divider()


def render_config_and_session_tab(
    db: DBManager,
    llm_settings: LLMSettings | None,
    selected_session_id: int | None,
) -> int | None:
    st.subheader("评分依据生成与考试批改创建")
    st.caption("上传 .docx 生成 rubric + answer_key，确认保存后再创建考试批改")

    if "latest_rubric_path" not in st.session_state:
        st.session_state.latest_rubric_path = ""
    if "latest_answer_path" not in st.session_state:
        st.session_state.latest_answer_path = ""
    if "generated_config_payload" not in st.session_state:
        st.session_state.generated_config_payload = None
    if "generated_doc_name" not in st.session_state:
        st.session_state.generated_doc_name = ""
    if "generated_doc_bytes" not in st.session_state:
        st.session_state.generated_doc_bytes = b""
    if "whole_config_generation_failed" not in st.session_state:
        st.session_state.whole_config_generation_failed = False
    _render_persistent_rubric_overview(db, selected_session_id)
    intake_notice = st.session_state.pop("_question_bank_intake_notice", None)
    if isinstance(intake_notice, dict):
        st.success(
            "题库同步完成："
            f"导入 {intake_notice.get('question_count', 0)} 题，"
            f"AI 标注 {intake_notice.get('tagged_questions', 0)} 题，"
            f"文件 {intake_notice.get('saved_file', '')}"
        )

    cfg_col, session_col = st.columns([1.25, 0.9])
    with cfg_col:
        with st.container(border=True):
            st.markdown("### 评分依据生成")
            st.caption("上传 Word 或 PDF 试卷，AI 会抽取题目、分值、答案与步骤给分规则。")
            word_upload = st.file_uploader("上传试卷 (支持 .docx 或 .pdf)", type=["docx", "pdf"], key="word_exam_uploader")

            def run_word_config_generation(use_text_only: bool = False) -> None:
                try:
                    st.session_state.generated_config_payload = None
                    st.session_state.generated_doc_name = ""
                    if llm_settings is None:
                        raise ValueError("请先在左侧配置 API Key/Base URL")
                    if word_upload is None:
                        raise ValueError("请先上传 .docx 或 .pdf 文件")
                    word_bytes = word_upload.getvalue()
                    is_pdf = word_upload.name.lower().endswith(".pdf")

                    def generate_work(report) -> tuple[dict[str, Any], Path | None, str]:
                        llm_client = LLMClient(llm_settings)
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        if is_pdf:
                            from rubric_auto_cropper import extract_pdf_images

                            report(0.04, "渲染 PDF 整页图片")
                            page_images = extract_pdf_images(word_bytes)
                            report(0.14, "PDF 整卷视觉单次请求", f"已准备 {len(page_images)} 张整页图片；不会发送 PDF 抽取文字。")
                            payload = generate_grading_config_from_images(
                                page_images,
                                "",
                                llm_client=llm_client,
                                model_name=llm_settings.config_model,
                                report=report,
                            )
                            extracted_path = None
                        else:
                            report(0.04, "读取 Word 文件")
                            doc_text = extract_docx_text(word_bytes)
                            report(0.10, "写入 Word 文本缓存", f"已提取约 {len(doc_text):,} 个字符")
                            extracted_path = UPLOAD_CONFIG_DIR / f"extracted_word_text_{ts}.txt"
                            extracted_path.write_text(doc_text, encoding="utf-8")
                            report(0.16, "Word 整卷文本单次请求", "整份 Word 文本仅发送一次；失败后由用户手动重试。")
                            payload = generate_grading_config_from_docx_text(
                                doc_text,
                                llm_client=llm_client,
                                model_name=llm_settings.config_model,
                                report=report,
                            )
                        report(0.98, "校验结构完成")
                        return payload, extracted_path, ts

                    payload, extracted_path, generated_ts = _run_with_stage_progress(
                        "AI 试卷解析",
                        generate_work,
                        done_text="预览准备就绪",
                    )
                    st.session_state.generated_config_payload = payload
                    st.session_state.generated_doc_name = word_upload.name
                    st.session_state.generated_doc_bytes = word_bytes
                    st.session_state.whole_config_generation_failed = False
                    generated_raw_path = UPLOAD_CONFIG_DIR / f"generated_config_preview_{generated_ts}.json"
                    _write_compact_json_file(generated_raw_path, payload)
                    if extracted_path is not None:
                        st.success(f"AI 已生成评分标准，请先预览再确认保存。Word 提取文本已保存：{extracted_path.name}")
                    else:
                        st.success("AI 已通过 PDF 整卷视觉单次请求生成评分标准，请先预览再确认保存。")
                except Exception as exc:  # noqa: BLE001
                    st.session_state.whole_config_generation_failed = True
                    st.error(f"生成失败：{exc}")

            use_text_only = st.checkbox(
                "整卷单次请求模式（不走本地拆题；Word 发送整卷文本，PDF 发送全部整页图片）",
                value=False,
                key="config_generation_use_text_only",
                help="适合本地拆题不可靠时使用。整卷模式严格只请求一次，不自动重试；失败后可手动重试。",
            )

            is_pdf = word_upload.name.lower().endswith(".pdf") if word_upload is not None else None
            _render_upload_paths_guide(is_pdf, use_text_only)

            if use_text_only:
                whole_button_label = (
                    "重试整卷生成"
                    if st.session_state.get("whole_config_generation_failed")
                    else "AI 生成评分标准（整卷单次请求）"
                )
                if st.button(
                    whole_button_label,
                    key="generate_from_word_btn",
                    type="primary",
                ):
                    run_word_config_generation(use_text_only=True)
            else:
                _QTYPE_LABELS = {
                    "choice": "选择",
                    "fill_blank": "填空",
                    "calculation": "计算",
                    "proof": "证明",
                    "comprehensive": "综合",
                }
                _QTYPE_LABEL_OPTIONS = ["选择", "填空", "计算", "证明", "综合"]
                _QTYPE_FROM_LABEL = {v: k for k, v in _QTYPE_LABELS.items()}

                def run_split_preview() -> None:
                    try:
                        if word_upload is None:
                            raise ValueError("请先上传 .docx 或 .pdf 文件")
                        word_bytes = word_upload.getvalue()
                        is_pdf = word_upload.name.lower().endswith(".pdf")
                        if is_pdf:
                            import rubric_auto_cropper
                            import importlib
                            importlib.reload(rubric_auto_cropper)
                            from rubric_auto_cropper import extract_pdf_text, extract_pdf_question_images
                            import base64
                            doc_text = extract_pdf_text(word_bytes)
                            blocks = preview_question_blocks_from_docx_text(doc_text)
                            # Plan B: crop per-question images from the PDF
                            if blocks:
                                try:
                                    raw_crops = extract_pdf_question_images(word_bytes, blocks)
                                    q_images = {}
                                    for qid, crop_dict in raw_crops.items():
                                        q_images[qid] = {
                                            "question": base64.b64encode(crop_dict["question"]).decode(),
                                            "answer": base64.b64encode(crop_dict["answer"]).decode() if crop_dict.get("answer") else None
                                        }
                                    st.session_state.pending_q_images = q_images
                                    st.session_state.pending_is_pdf = True
                                except Exception as crop_exc:  # noqa: BLE001
                                    st.error(f"PDF 题目裁图失败，已停止图片准备，不会退回纯文本模式：{crop_exc}")
                                    st.session_state.pending_q_images = {}
                                    st.session_state.pending_is_pdf = True
                            else:
                                st.session_state.pending_q_images = {}
                                st.session_state.pending_is_pdf = True
                        else:
                            doc_text = extract_docx_text(word_bytes)
                            # 富文本拆题：保留公式 HTML 与图片，答案按卷末/内联正确配对；失败回退纯文本
                            blocks = preview_question_blocks_from_docx_bytes(
                                word_bytes, fallback_doc_text=doc_text
                            )
                            st.session_state.pending_q_images = {}
                            st.session_state.pending_is_pdf = False
                        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                        st.session_state.pending_question_blocks = blocks
                        st.session_state.pending_doc_text = doc_text
                        st.session_state.pending_doc_ts = ts
                        st.session_state.generated_config_payload = None
                        st.session_state.generated_doc_name = word_upload.name
                        st.session_state.generated_doc_bytes = word_bytes
                        if not blocks:
                            st.warning("本地未能拆分出任何题目，请检查文档格式或改用旧版整卷模式。")
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"拆题失败：{exc}")

                if st.button("① 拆分试卷（本地预览，不调用 AI）", key="split_preview_btn"):
                    run_split_preview()

                pending_blocks = st.session_state.get("pending_question_blocks")
                if pending_blocks:
                    total_n = len(pending_blocks)
                    review_n = sum(
                        1 for b in pending_blocks
                        if b.get("needs_review") or not b.get("local_answer_trusted")
                    )
                    st.caption(
                        f"共拆出 {total_n} 题"
                        + (f"，其中 {review_n} 题答案待人工确认（⚠️）。请核对题型与答案，删除拆错/多余的题后再生成。"
                           if review_n else "，答案均已提取。请核对题型与答案后再生成。")
                    )

                    def _render_block_card(i: int, b: dict) -> None:
                        qid = str(b.get("question_id") or f"Q{i + 1}")
                        cur_type = str(b.get("question_type") or "comprehensive")
                        cur_label = _QTYPE_LABELS.get(cur_type, "综合")
                        flagged = bool(b.get("needs_review") or not b.get("local_answer_trusted"))
                        with st.container(border=True):
                            head = st.columns([0.18, 0.34, 0.48])
                            with head[0]:
                                st.markdown(f"**{qid.replace('Q', '第')}题**" + ("　⚠️" if flagged else ""))
                            with head[1]:
                                st.selectbox(
                                    "题型",
                                    options=_QTYPE_LABEL_OPTIONS,
                                    index=_QTYPE_LABEL_OPTIONS.index(cur_label)
                                    if cur_label in _QTYPE_LABEL_OPTIONS else 4,
                                    key=f"qtype_{i}",
                                    label_visibility="collapsed",
                                )
                            with head[2]:
                                st.checkbox(
                                    "删除此题（不进入 AI 生成）",
                                    value=False,
                                    key=f"del_{i}",
                                )
                            is_pdf = bool(st.session_state.get("pending_is_pdf"))
                            if is_pdf:
                                # PDF 模式下，直接展示自动裁切的题干截图，不显示可能带有OCR噪音的解析文本
                                q_images = st.session_state.get("pending_q_images", {})
                                if qid in q_images and isinstance(q_images[qid], dict) and q_images[qid].get("question"):
                                    st.image(f"data:image/jpeg;base64,{q_images[qid]['question']}", use_container_width=True)
                                else:
                                    st.markdown("_（PDF 题干截图为空）_")
                            else:
                                # Word 模式下，渲染富文本题干及文档内嵌图
                                stem_html = str(b.get("question_html") or b.get("question_text") or b.get("text") or "")
                                stem_render = _qb_safe_html_format(_qb_strip_image_markers(stem_html))
                                if stem_render:
                                    st.markdown(
                                        f'<div style="font-size:0.96rem;line-height:1.7">{stem_render}</div>',
                                        unsafe_allow_html=True,
                                    )
                                else:
                                    st.markdown("_（题干为空）_")
                                stem_imgs = list(b.get("image_paths") or []) + _qb_image_paths_from_text(stem_html)
                                _render_split_images(stem_imgs)

                            ans_html = str(b.get("answer_html") or b.get("canonical_answer") or b.get("answer_text") or "")
                            ana_html = str(b.get("analysis_html") or b.get("analysis") or "")
                            exp_label = "查看答案与解析/证明过程" + ("（⚠️ 答案缺失，请核对）" if flagged else "")
                            with st.expander(exp_label, expanded=False):
                                if is_pdf:
                                    # PDF 模式下，将答案部分的裁切截图放入答案 expander 内
                                    q_images = st.session_state.get("pending_q_images", {})
                                    has_ans_crop = False
                                    if qid in q_images and isinstance(q_images[qid], dict) and q_images[qid].get("answer"):
                                        st.image(f"data:image/jpeg;base64,{q_images[qid]['answer']}", use_container_width=True)
                                        has_ans_crop = True
                                    
                                    if not has_ans_crop:
                                        ans_render = _qb_safe_html_format(_qb_strip_image_markers(ans_html))
                                        if ans_render:
                                            st.markdown(f"**答案**：{ans_render}", unsafe_allow_html=True)
                                        else:
                                            st.markdown("_未提取到答案，建议人工核对原卷_")
                                else:
                                    # Word 模式
                                    ans_render = _qb_safe_html_format(_qb_strip_image_markers(ans_html))
                                    st.markdown(
                                        "**答案**：" + (ans_render if ans_render else "_未提取到，建议人工核对原卷_"),
                                        unsafe_allow_html=True,
                                    )
                                    _render_split_images(_qb_image_paths_from_text(ans_html))
                                    if ana_html.strip():
                                        ana_render = _qb_safe_html_format(_qb_strip_image_markers(ana_html))
                                        st.markdown("**解析 / 证明过程**：", unsafe_allow_html=True)
                                        st.markdown(
                                            f'<div style="font-size:0.92rem;line-height:1.7">{ana_render}</div>',
                                            unsafe_allow_html=True,
                                        )
                                        _render_split_images(_qb_image_paths_from_text(ana_html))

                    for _i, _b in enumerate(pending_blocks):
                        _render_block_card(_i, _b)


                    def run_confirmed_generation() -> None:
                        try:
                            if llm_settings is None:
                                raise ValueError("请先在左侧配置 API Key/Base URL")
                            confirmed_blocks: list[dict[str, Any]] = []
                            for i, block in enumerate(pending_blocks):
                                if st.session_state.get(f"del_{i}"):
                                    continue
                                new_block = dict(block)
                                chosen_label = str(st.session_state.get(f"qtype_{i}") or "")
                                new_block["question_type"] = _QTYPE_FROM_LABEL.get(
                                    chosen_label,
                                    new_block.get("question_type") or "comprehensive",
                                )
                                if bool(st.session_state.get("pending_is_pdf")):
                                    new_block["semantic_source"] = "images"
                                confirmed_blocks.append(new_block)
                            if not confirmed_blocks:
                                raise ValueError("没有可生成的题目（是否全部勾选了删除？）")
                            st.session_state.pending_confirmed_blocks = confirmed_blocks
                            doc_text = str(st.session_state.get("pending_doc_text") or "")
                            ts = str(
                                st.session_state.get("pending_doc_ts")
                                or datetime.now().strftime("%Y%m%d_%H%M%S")
                            )
                            q_images: dict[str, Any] | None = (
                                st.session_state.get("pending_q_images") or None
                            )
                            is_pdf = bool(st.session_state.get("pending_is_pdf"))
                            if is_pdf:
                                _session_manager._validate_image_semantic_inputs(confirmed_blocks, q_images)

                            def generate_work(report) -> dict[str, Any]:
                                llm_client = LLMClient(llm_settings)
                                img_count = len(q_images) if q_images else 0
                                report(
                                    0.10,
                                    "单题并发生成",
                                    f"已确认 {len(confirmed_blocks)} 题，开始单题并发生成评分标准结构"
                                    + (f"（PDF 模式，携带 {img_count} 张题目裁图）。" if is_pdf else "。"),
                                )
                                payload = generate_grading_config_from_confirmed_blocks(
                                    confirmed_blocks,
                                    doc_text,
                                    llm_client=llm_client,
                                    model_name=llm_settings.config_model,
                                    report=report,
                                    q_images=q_images,
                                )
                                report(0.98, "校验结构完成")
                                return payload

                            payload = _run_with_stage_progress(
                                "AI 评分标准生成（单题并发）",
                                generate_work,
                                done_text="预览准备就绪",
                            )
                            st.session_state.generated_config_payload = payload
                            generated_raw_path = UPLOAD_CONFIG_DIR / f"generated_config_preview_{ts}.json"
                            _write_compact_json_file(generated_raw_path, payload)
                            st.success(
                                f"AI 已生成评分标准（已确认 {len(confirmed_blocks)} 题），请先预览再确认保存。"
                            )
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"生成失败：{exc}")

                    if st.button("② 确认无误，生成评分标准", key="generate_from_blocks_btn", type="primary"):
                        run_confirmed_generation()

                    def run_failed_question_retry() -> None:
                        try:
                            if llm_settings is None:
                                raise ValueError("请先在左侧配置 API Key/Base URL")
                            current_payload = st.session_state.get("generated_config_payload")
                            confirmed_blocks = st.session_state.get("pending_confirmed_blocks") or []
                            if not isinstance(current_payload, dict):
                                raise ValueError("当前没有可恢复的评分标准")
                            if not confirmed_blocks:
                                raise ValueError("已确认的题目数据已丢失，请重新拆分试卷")
                            doc_text = str(st.session_state.get("pending_doc_text") or "")
                            q_images = st.session_state.get("pending_q_images") or None

                            def retry_work(report) -> dict[str, Any]:
                                report(0.08, "重试失败题目", "仅重新发送失败题目，已成功题目保持不变。")
                                return retry_failed_grading_config_questions(
                                    current_payload,
                                    confirmed_blocks,
                                    doc_text,
                                    llm_client=LLMClient(llm_settings),
                                    model_name=llm_settings.config_model,
                                    report=report,
                                    q_images=q_images,
                                )

                            payload = _run_with_stage_progress(
                                "AI 评分标准恢复",
                                retry_work,
                                done_text="失败题目重试完成",
                            )
                            st.session_state.generated_config_payload = payload
                            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                            _write_compact_json_file(
                                UPLOAD_CONFIG_DIR / f"generated_config_retry_{ts}.json",
                                payload,
                            )
                            remaining = failed_grading_config_question_ids(payload)
                            if remaining:
                                st.warning(f"仍有 {len(remaining)} 道题生成失败：{', '.join(remaining)}")
                            else:
                                st.success("失败题目已全部补齐，并已重新完成 AI 整体赋分。")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"失败题目重试失败：{exc}")

                    def run_score_allocation_retry() -> None:
                        try:
                            if llm_settings is None:
                                raise ValueError("请先在左侧配置 API Key/Base URL")
                            current_payload = st.session_state.get("generated_config_payload")
                            confirmed_blocks = st.session_state.get("pending_confirmed_blocks") or []
                            if not isinstance(current_payload, dict):
                                raise ValueError("当前没有可重新赋分的评分标准")
                            if not confirmed_blocks:
                                raise ValueError("已确认的题目数据已丢失，请重新拆分试卷")
                            doc_text = str(st.session_state.get("pending_doc_text") or "")
                            q_images = st.session_state.get("pending_q_images") or None

                            def score_work(report) -> dict[str, Any]:
                                report(0.10, "重新整体赋分", "保持题目与评分点不变，仅重新分配整卷分值。")
                                return retry_grading_config_score_allocation(
                                    current_payload,
                                    confirmed_blocks,
                                    doc_text,
                                    llm_client=LLMClient(llm_settings),
                                    model_name=llm_settings.config_model,
                                    report=report,
                                    q_images=q_images,
                                )

                            payload = _run_with_stage_progress(
                                "AI 重新整体赋分",
                                score_work,
                                done_text="整体赋分完成",
                            )
                            st.session_state.generated_config_payload = payload
                            if payload.get("meta", {}).get("score_allocation_ai_success"):
                                st.success("AI 整体赋分已重新完成。")
                            else:
                                st.warning("AI 整体赋分仍失败，当前保留本地兜底分值，可再次点击重试。")
                        except Exception as exc:  # noqa: BLE001
                            st.error(f"重新整体赋分失败：{exc}")

                    current_split_payload = st.session_state.get("generated_config_payload")
                    current_confirmed_blocks = st.session_state.get("pending_confirmed_blocks") or []
                    if isinstance(current_split_payload, dict) and current_confirmed_blocks:
                        failed_qids = failed_grading_config_question_ids(current_split_payload)
                        if failed_qids:
                            st.error(f"{len(failed_qids)} 道题生成失败：{', '.join(failed_qids)}。整体赋分已暂停。")
                            failed_details = current_split_payload.get("meta", {}).get("failed_questions", [])
                            if isinstance(failed_details, list) and failed_details:
                                with st.expander("查看失败原因", expanded=False):
                                    for failure in failed_details:
                                        if not isinstance(failure, dict):
                                            continue
                                        st.write(
                                            f"{failure.get('question_id', '')}："
                                            f"已尝试 {failure.get('attempts', 1)} 次；"
                                            f"{failure.get('error', '未知错误')}"
                                        )
                            if st.button("仅重试失败题目", key="retry_failed_config_questions_btn", type="primary"):
                                run_failed_question_retry()
                        else:
                            score_success = bool(
                                current_split_payload.get("meta", {}).get("score_allocation_ai_success")
                            )
                            if not score_success:
                                st.warning("题目均已生成，但 AI 整体赋分未成功，当前使用本地兜底分值。")
                            if st.button("重新整体赋分", key="retry_config_score_allocation_btn"):
                                run_score_allocation_retry()


    with session_col:
        with st.container(border=True):
            created_session_id: int | None = None
            if selected_session_id is not None:
                current = db.get_grading_session(selected_session_id)
                stage = _session_stage_summary(db, selected_session_id)
                st.markdown("### 当前考试批改")
                if current:
                    st.markdown(f"**{current['session_name']}**")
                    st.markdown(_status_badge(stage["label"], stage["kind"]), unsafe_allow_html=True)
                    st.caption(stage["detail"])
                    st.code(
                        f"rubric: {Path(str(current['rubric_path'])).name}\n"
                        f"answer_key: {Path(str(current['answer_key_path'])).name}"
                    )
                st.caption("如需新建另一场考试，请先在左侧选择“未选择”。")
            else:
                st.markdown("### 创建考试批改")
                st.caption("确认评分依据后，创建一个可批改、可追踪、可回收的考试批改任务。")
                st.markdown("**当前待创建考试批改配置**")
                st.code(
                    f"rubric: {st.session_state.latest_rubric_path or '未设置'}\n"
                    f"answer_key: {st.session_state.latest_answer_path or '未设置'}"
                )

                if "new_session_name_input" not in st.session_state:
                    st.session_state.new_session_name_input = f"考试批改_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                session_name = st.text_input("考试批改名称", key="new_session_name_input")
                if st.button("创建考试批改", type="primary", key="create_session_button"):
                    try:
                        rubric_path = st.session_state.latest_rubric_path
                        answer_path = st.session_state.latest_answer_path
                        if not rubric_path or not answer_path:
                            raise ValueError("请先完成 Word 生成并点击“确认保存评分依据”。")
                        clean_session_name = str(session_name or "").strip()
                        if not clean_session_name:
                            raise ValueError("考试批改名称不能为空")

                        created_session_id = db.create_grading_session(
                            session_name=clean_session_name,
                            rubric_path=rubric_path,
                            answer_key_path=answer_path,
                        )
                        _write_session_workflow_state(
                            db,
                            created_session_id,
                            "session_created",
                            {"rubric_path": rubric_path, "answer_key_path": answer_path},
                        )
                        clear_pending_config_for_new_session(st.session_state)
                        st.session_state["selected_session_id"] = created_session_id
                        st.success(f"考试批改创建成功：{created_session_id}")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"创建考试批改失败：{exc}")

    payload = st.session_state.generated_config_payload
    if payload is not None:
        preview_ready = _render_generated_config_preview(payload, st.session_state.generated_doc_name)
        _render_manual_scoring_unit_tools(payload, llm_settings)

        if not preview_ready:
            st.caption("当前评分依据存在漏题或结构风险，请重新生成或先修正文档提示后再保存。")

        sync_to_question_bank = st.checkbox(
            "同时把这份带答案 Word 导入题库并自动打标签",
            value=False,
            key="sync_generated_word_to_question_bank",
            help="只调用题库导入和题库 AI 打标签流程；失败不会影响评分依据保存或后续阅卷。",
        )

        if st.button("确认保存评分依据", key="confirm_save_generated_config", disabled=not preview_ready):
            try:
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                rubric_path, answer_path = save_generated_config(UPLOAD_CONFIG_DIR, payload, ts)
                remember_saved_config_for_new_session(
                    st.session_state,
                    selected_session_id=selected_session_id,
                    rubric_path=str(rubric_path),
                    answer_key_path=str(answer_path),
                    settings_store=db,
                )
                if sync_to_question_bank:
                    try:
                        tagging_workers = int(st.session_state.get("qb_tagging_workers", 8) or 8)
                        tagging_rpm = int(st.session_state.get("qb_tagging_rpm", 1000) or 1000)

                        def intake_work(report):
                            report(
                                0.08,
                                "复制并解析 Word",
                                f"随后按题库打标签设置执行：并发 {tagging_workers}，RPM {tagging_rpm}。",
                            )

                            def on_tagging_progress(done, total, question_id, result) -> None:
                                ratio = done / max(total, 1)
                                status = "已保存标签" if getattr(result, "ok", False) else "标签失败"
                                report(
                                    0.35 + ratio * 0.6,
                                    "AI 打标签",
                                    f"{done}/{total}，题目 {question_id}：{status}",
                                )

                            intake_result = copy_and_intake_uploaded_grading_paper(
                                filename=str(st.session_state.generated_doc_name or f"grading_paper_{ts}.docx"),
                                content=bytes(st.session_state.get("generated_doc_bytes") or b""),
                                raw_papers_dir=project_data_root() / "question_bank" / "raw_papers",
                                db_path=question_bank_db_path(),
                                run_ai_tagging=True,
                                ai_service=AITaggingService(),
                                tagging_max_workers=tagging_workers,
                                tagging_requests_per_minute=tagging_rpm,
                                tagging_progress_callback=on_tagging_progress,
                                grading_session_id=selected_session_id,
                                grading_source_questions=payload.get("questions", []),
                            )
                            report(0.98, "写入题库同步结果")
                            return intake_result

                        intake_result = _run_with_stage_progress(
                            "同步导入题库并 AI 打标签",
                            intake_work,
                            done_text="题库同步完成",
                        )
                        st.session_state["_question_bank_intake_notice"] = {
                            "saved_file": intake_result.saved_file.name,
                            "question_count": intake_result.import_result.question_count,
                            "tagged_questions": intake_result.tagged_questions,
                            "confirmed_links": intake_result.confirmed_links,
                            "suggested_links": intake_result.suggested_links,
                        }
                    except Exception as intake_exc:  # noqa: BLE001
                        st.warning(f"评分依据已保存，但同步题库失败：{intake_exc}")
                if selected_session_id is not None:
                    db.update_grading_session_config(
                        selected_session_id,
                        rubric_path=str(rubric_path),
                        answer_key_path=str(answer_path),
                    )
                    refreshed = _refresh_template_mapping_from_session(db, selected_session_id)
                    _write_session_workflow_state(
                        db,
                        selected_session_id,
                        "word_scoring_saved",
                        {
                            "rubric_path": str(rubric_path),
                            "answer_key_path": str(answer_path),
                            "template_mapping_refreshed": refreshed,
                        },
                    )
                    if refreshed:
                        st.success("评分依据已保存，并已同步到当前考试批改；样卷映射表已按新评分标准刷新，请重新确认题框映射。")
                    else:
                        st.success("评分依据已保存，并已同步到当前考试批改。")
                    st.rerun()
                else:
                    st.success("评分依据已保存，可用于创建考试批改。")
            except Exception as exc:  # noqa: BLE001
                st.error(f"保存失败：{exc}")

    st.divider()
    st.subheader("样卷上传与题框映射（批改前必须完成）")

    if selected_session_id is None:
        st.info("请先在左侧选择考试批改，再进行样卷上传与题框映射。")
        return created_session_id

    session = db.get_grading_session(selected_session_id)
    if not session:
        st.error("当前考试批改不存在。")
        return created_session_id

    # ---- Upload template images / PDF ----
    pdf_upload = st.file_uploader(
        "上传整班扫描 PDF（可选，自动取前两页作为样卷）",
        type=["pdf"],
        key=f"template_pdf_upload_{selected_session_id}",
        help="如果你已经有扫描仪生成的整班 PDF，直接上传它；系统只抽取前两页用于样卷标定，不会在这里批改整班。",
    )
    first_page_role = st.radio(
        "PDF 第一页是哪一面？",
        options=["front", "back"],
        format_func=lambda value: "第一页是正面，第二页自动作为反面" if value == "front" else "第一页是反面，第二页自动作为正面",
        horizontal=True,
        index=0 if _session_front_page_parity(selected_session_id) == "odd" else 1,
        key=f"template_pdf_first_page_role_{selected_session_id}",
        disabled=pdf_upload is None,
    )
    pdf_front_page = 1 if first_page_role == "front" else 2
    pdf_back_page = 2 if first_page_role == "front" else 1
    
    rubric_images = st.file_uploader(
        "上传大题标准答案截图（可选，自动提取图内公式图形，支持多选）",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True,
        key=f"rubric_images_upload_{selected_session_id}",
        help="推荐按题号命名（如 Q8.png, 9.jpg）。批改时AI将把此图作为该题的最高评分准则！",
    )
    
    st.caption("样卷上传只用于建立版面和题框映射；评分标准、答案和分值继续读取上方 Word 生成结果。")
    if st.button("上传样卷与答案，进入题框标定", key=f"detect_regions_btn_{selected_session_id}", type="primary"):
        try:
            if pdf_upload is None:
                raise ValueError("请上传整班扫描 PDF，系统会自动取前两页作为样卷。")

            session_dir = TEMPLATE_DIR / f"session_{selected_session_id}"
            session_dir.mkdir(parents=True, exist_ok=True)
            
            if rubric_images:
                rubric_img_dir = session_dir / "rubric_images"
                rubric_img_dir.mkdir(parents=True, exist_ok=True)
                for img in rubric_images:
                    img_name = img.name
                    if img_name and img_name[0].isdigit():
                        img_name = f"Q{img_name}"
                    elif img_name and img_name.lower().startswith("q"):
                        img_name = f"Q{img_name[1:]}"
                    img_path = rubric_img_dir / img_name
                    with open(img_path, "wb") as f:
                        f.write(img.getvalue())

            front_path, back_path = _save_template_pages_from_pdf_upload(
                pdf_upload,
                session_dir,
                front_page_number=int(pdf_front_page),
                back_page_number=int(pdf_back_page),
            )

            template_id = db.upsert_session_template(selected_session_id, str(front_path), str(back_path))

            def mapping_work(report) -> dict[str, Any]:
                report(0.08, "读取已保存评分标准")
                rubric_context = _read_json_safely(_resolve_session_file_path(session["rubric_path"]))
                answer_context = _read_json_safely(_resolve_session_file_path(session["answer_key_path"]))
                report(0.22, "建立题号候选与映射区")
                return create_template_mapping_package(
                    front_path,
                    back_path,
                    rubric=rubric_context,
                    answer_key=answer_context,
                    output_dir=session_dir,
                )

            package = _run_with_stage_progress(
                "建立样卷映射",
                mapping_work,
                done_text="已保存样卷映射包",
            )
            all_regions: list[dict[str, Any]] = []
            st.info("样卷已保存。题目、答案、分值与等价答案预案继续使用 Word 评分标准；请在下方手动新增作答区并绑定题号/小问。")

            db.save_answer_regions(selected_session_id, template_id, all_regions)
            db.update_session_template_analysis(
                selected_session_id,
                ai_analysis_path=package["paths"]["raw_path"],
                template_config_path=package["paths"]["config_path"],
                regions_path=package["paths"]["regions_path"],
            )
            db.mark_template_confirmed(selected_session_id, confirmed=False)
            _write_regions_snapshot(db, selected_session_id)
            _write_session_workflow_state(
                db,
                selected_session_id,
                "template_uploaded",
                {
                    "package_paths": package["paths"],
                    "template_first_page_role": first_page_role,
                    "front_page_parity": _front_page_parity_from_first_page_role(first_page_role),
                },
            )
            st.success(
                f"样卷映射包已建立：读到 {len(package['config'].get('questions', []))} 道题号候选。"
                f"作答区域将由人工标定；本地调试文件已保存到 {session_dir}"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"样卷上传失败：{exc}")

    # ---- Interactive region editor ----
    template = db.get_session_template(selected_session_id)
    if template:
        with st.expander("样卷映射预览（默认折叠）", expanded=False):
            _render_template_config_editor(db, selected_session_id, template)
        if st.button(
            "进入专注题框标定",
            key=f"enter_region_focus_{selected_session_id}",
            type="primary",
            use_container_width=True,
        ):
            st.session_state["region_focus_session_id"] = selected_session_id
            st.rerun()
        if os.getenv("AI_REGION_EDITOR_LEGACY", "").strip() == "1":
            with st.expander("旧版题框编辑器（紧急回退）", expanded=False):
                _render_region_editor_v3(db, selected_session_id, session, template, llm_settings)
        _render_workflow_state_card(db, selected_session_id)

    ready = db.is_template_ready(selected_session_id)
    st.info("模板状态：已确认，可批改" if ready else "模板状态：未确认，批改前需完成题框映射")

    return created_session_id


def _resolve_grading_run_request(
    *,
    run_full: bool,
    run_hybrid: bool,
    retry_full: bool,
    retry_hybrid: bool,
    retry_incomplete_hybrid: bool = False,
) -> tuple[str, bool] | None:
    if run_full:
        return "full_paper", False
    if run_hybrid:
        return "hybrid_batch", False
    if retry_full:
        return "full_paper", True
    if retry_hybrid or retry_incomplete_hybrid:
        return "hybrid_batch", True
    return None


def _incomplete_status_label(status: object) -> str:
    return {
        "complete": "完整",
        "incomplete": "不完整",
        "invalid": "无效",
    }.get(str(status or "").strip(), "无效")


def _sanitize_incomplete_result_error(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"data:image/\S+", "[图片数据已省略]", text, flags=re.IGNORECASE)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]+\b", "[已隐藏密钥]", text)
    text = re.sub(r"\bBearer\s+[A-Za-z0-9._-]+\b", "Bearer [已隐藏密钥]", text, flags=re.IGNORECASE)
    return text


def _build_incomplete_result_display_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    display_rows: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("student_name") or row.get("ocr_name") or "未知学生").strip()
        code = str(row.get("student_code") or "").strip()
        student_label = f"{name}（{code}）" if code else name
        affected = "、".join(str(item).strip() for item in row.get("affected_major_question_ids", []) if str(item).strip())
        missing = "、".join(str(item).strip() for item in row.get("missing_question_ids", []) if str(item).strip())
        display_rows.append(
            {
                "学生": student_label,
                "状态": _incomplete_status_label(row.get("status")),
                "受影响大题": affected,
                "缺失小题": missing,
                "最近失败原因": _sanitize_incomplete_result_error(row.get("last_failure_reason")),
                "失败重试次数": int(row.get("retry_attempt_count") or 0),
            }
        )
    return display_rows


def _render_incomplete_results_panel(db: DBManager, session_id: int) -> bool:
    incomplete_rows = db.list_incomplete_results(session_id)
    if not incomplete_rows:
        return False
    st.markdown("---")
    st.markdown(f"##### 不完整批改结果（{len(incomplete_rows)} 份）")
    st.info("这些试卷虽然已经生成成绩，但仍有缺题或异常。点击下方“一键补跑不完整大题”后，系统会只用混合批改重跑受影响的大题，其他已成功分数会继续保留。")
    st.dataframe(
        pd.DataFrame(_build_incomplete_result_display_rows(incomplete_rows)),
        use_container_width=True,
        hide_index=True,
    )
    return st.button(
        "一键补跑不完整大题",
        type="secondary",
        key=f"retry_incomplete_hybrid_btn_{session_id}",
    )


def render_grading_tab(
    db: DBManager,
    selected_session_id: int | None,
    llm_settings: LLMSettings | None,
) -> None:
    st.subheader("批改进度（当前考试批改）")

    if selected_session_id is None:
        st.warning("请先在左侧选择一个考试批改")
        return
    if llm_settings is None:
        st.warning("请先在左侧配置 API Key/Base URL")
        return

    if not db.is_template_ready(selected_session_id):
        st.warning("当前考试批改尚未完成模板映射确认，请先到“评分依据与考试批改”页完成模板步骤。")
        return

    session = db.get_grading_session(selected_session_id)
    if not session:
        st.error("考试批改不存在。")
        return

    upload_dir = _session_exam_upload_dir(selected_session_id)
    uploaded_exam_files = st.file_uploader(
        "选择扫描试卷文件（可多选）",
        type=["pdf", "jpg", "jpeg", "png"],
        accept_multiple_files=True,
        key=f"scan_exam_files_{selected_session_id}",
        help="可一次选择整班扫描 PDF，或选择多张 JPG/PNG 图片。文件会复制到当前考试批改的本地目录后再预检和批改。",
    )
    enhance_images = st.checkbox(
        "启用扫描增强（推荐）",
        value=True,
        key=f"enhance_scan_images_{selected_session_id}",
        help="PDF 会转成一套标准原卷页；开启后标准页直接保存为增强版，不再额外保留 PDF 源文件和增强副本。",
    )
    st.caption("PDF 会在处理成功后删除源文件；系统只保留一套标准原卷页用于批改、复核和导出。")
    saved_uploads = _save_uploaded_exam_files(uploaded_exam_files, upload_dir, enhance_pdf_pages=enhance_images)
    if saved_uploads:
        st.success(f"已接收 {len(saved_uploads)} 个扫描文件，保存到：{upload_dir}")
    else:
        existing_uploads = _list_scan_input_files(upload_dir)
        if existing_uploads:
            st.info(f"当前考试批改已保存 {len(existing_uploads)} 个扫描文件：{upload_dir}")
    default_scan_dir = upload_dir if _list_scan_input_files(upload_dir) else DEFAULT_EXAMS_DIR
    with st.expander("高级：从已有本地目录读取", expanded=False):
        exams_dir_input = st.text_input("试卷目录", value=str(default_scan_dir), key=f"scan_exam_dir_{selected_session_id}")
    active_exams_dir = upload_dir if _list_scan_input_files(upload_dir) else Path(exams_dir_input)
    grading_max_workers = int(st.session_state.get("grading_max_workers_input", DEFAULT_FULL_PAPER_WORKERS))
    grading_rpm_limit = int(st.session_state.get("grading_requests_per_minute_input", DEFAULT_GRADING_RPM))
    precheck_workers = int(st.session_state.get("precheck_max_workers_input", DEFAULT_PRECHECK_WORKERS))
    st.caption(
        f"当前全局并发：整卷 {grading_max_workers}，预检 {precheck_workers}，"
        f"请求速率 {grading_rpm_limit} RPM。可在左侧 API 配置下方修改。"
    )
    manual_service = ManualReviewService(db, ANNOTATED_DIR)
    grading_log_key = f"grading_run_logs_{selected_session_id}"
    if grading_log_key not in st.session_state:
        st.session_state[grading_log_key] = []
    scan_state_key = f"scan_analysis_{selected_session_id}"
    scan_path = _session_work_dir(selected_session_id) / "scan_analysis_latest.json"
    if scan_state_key not in st.session_state and scan_path.exists():
        try:
            st.session_state[scan_state_key] = _read_json_safely(scan_path)
        except Exception:
            pass

    if st.button("预检本地试卷 PDF / 图片", key=f"precheck_scan_{selected_session_id}", type="secondary"):
        try:
            exams_dir = active_exams_dir
            if not exams_dir.exists():
                raise FileNotFoundError(f"目录不存在：{exams_dir}")
            if not _list_scan_input_files(exams_dir):
                raise FileNotFoundError("未找到可预检的 PDF/JPG/PNG。请先选择扫描文件，或在高级目录中指定包含试卷的文件夹。")
            students_for_scan = db.list_students()
            if not students_for_scan:
                raise ValueError("当前学生库为空。请先在“全局资料”中导入学生名单，再进行 PDF/图片预检。")
            def precheck_work(report) -> Any:
                report(0.05, "读取本地 PDF / 图片")
                scanner = Scanner(
                    exams_dir=exams_dir,
                    llm_client=LLMClient(llm_settings),
                    ocr_model=llm_settings.ocr_model,
                    enhance_images=enhance_images,
                    ocr_workers=precheck_workers,
                    name_region=_student_name_region_for_scan(db, selected_session_id),
                    front_page_parity=_session_front_page_parity(selected_session_id),
                )
                report(0.18, "并发渲染/识别学生姓名", f"姓名识别并发数：{precheck_workers}；将对 OCR 姓名和学生库做相似匹配。")
                return scanner.analyze(students_for_scan)

            analysis = _run_with_stage_progress(
                "试卷预检",
                precheck_work,
                done_text="已完成学生匹配预检",
            )
            payload = analysis.to_dict()
            payload["enhance_images"] = enhance_images
            st.session_state[scan_state_key] = payload
            _write_json_file(scan_path, payload)
            _write_session_workflow_state(
                db,
                selected_session_id,
                "scan_prechecked",
                {
                    "scan_analysis_path": str(scan_path),
                    "summary": _scan_analysis_summary(payload),
                    "front_page_parity": _session_front_page_parity(selected_session_id),
                },
            )
            st.success("试卷预检完成，已保存到本地；请先处理下方异常卷，再开始批改。")
        except Exception as exc:  # noqa: BLE001
            st.error(f"试卷预检失败：{exc}")

    scan_payload = st.session_state.get(scan_state_key)
    manual_decisions: list[dict] = []
    if isinstance(scan_payload, dict):
        manual_decisions = _render_scan_precheck_panel(scan_payload, db.list_students(), selected_session_id)

    c1, c2 = st.columns([1, 2])
    with c1:
        if session.get("status") == "running":
            st.error("检测到当前考试批改状态处于“运行中”。如果之前因崩溃或刷新中断，请强制重置状态后再启动。")
            if st.button("强制重置状态", type="secondary", key="reset_run_status_btn"):
                db.update_session_status(selected_session_id, "failed")
                st.rerun()
        
        log_box = st.empty()
        log_box = st.empty()
        _render_grading_log_panel(log_box, st.session_state.get(grading_log_key, []))
        
        failed_papers = db.list_failed_papers(selected_session_id)
        retry_incomplete_hybrid = _render_incomplete_results_panel(db, selected_session_id)
        
        with st.expander("⚙️ 混合批改参数快速设置", expanded=False):
            d_obj = int(st.session_state.get("objective_batch_size_input", 15))
            d_maj = int(st.session_state.get("hybrid_major_batch_size_input", 4))
            obj_bs = st.number_input("客观题批大小", min_value=1, max_value=50, value=d_obj, step=1, key="run_objective_batch_size")
            maj_bs = st.number_input("主观题横批批大小 (batch_size)", min_value=1, max_value=20, value=d_maj, step=1, key="run_hybrid_major_batch_size")
            st.session_state.objective_batch_size_input = obj_bs
            st.session_state.hybrid_major_batch_size_input = maj_bs
            st.caption("参数快速调整，无需重新在左侧 API 配置中保存。")
            
        col1, col2 = st.columns(2)
        with col1:
            run_full = st.button("开始整卷并发批改", type="primary", key="run_grading_full_btn")
        with col2:
            run_hybrid = st.button("开始混合批改（客观题+大题横批）", type="primary", key="run_grading_hybrid_btn")
            
        retry_full = False
        retry_hybrid = False
        if failed_papers:
            st.markdown("---")
            st.markdown("##### 仅重试批改失败的试卷")
            rcol1, rcol2 = st.columns(2)
            with rcol1:
                retry_full = st.button("仅重试失败：整卷批改", type="secondary", key="retry_grading_full_btn")
            with rcol2:
                retry_hybrid = st.button("仅重试失败：混合批改", type="secondary", key="retry_grading_hybrid_btn")
            
        run_request = _resolve_grading_run_request(
            run_full=run_full,
            run_hybrid=run_hybrid,
            retry_full=retry_full,
            retry_hybrid=retry_hybrid,
            retry_incomplete_hybrid=retry_incomplete_hybrid,
        )
        run_any = run_request is not None
        
        if run_any:
            grading_mode, failed_only = run_request
            try:
                exams_dir = active_exams_dir
                if not exams_dir.exists():
                    raise FileNotFoundError(f"目录不存在：{exams_dir}")
                if not failed_only and not isinstance(scan_payload, dict):
                    raise ValueError("请先点击“预检本地试卷 PDF / 图片”，确认异常卷后再开始批改。")
                
                if not failed_only:
                    decisions_path = _session_work_dir(selected_session_id) / "scan_manual_decisions_latest.json"
                    _write_json_file(decisions_path, manual_decisions)

                llm_client = LLMClient(llm_settings)
                service = GradingService(db, llm_client=llm_client)

                progress_bar = st.progress(0)
                logs: list[str] = list(st.session_state.get(grading_log_key, []))
                logs.append(f"--- 批改运行开始 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---")
                st.session_state[grading_log_key] = logs
                _render_grading_log_panel(log_box, logs)

                for event in service.run_session_grading(
                    session_id=selected_session_id,
                    exams_dir=exams_dir,
                    rubric_path=_resolve_session_file_path(session["rubric_path"]),
                    answer_key_path=_resolve_session_file_path(session["answer_key_path"]),
                    ocr_model=llm_settings.ocr_model,
                    grading_model=llm_settings.grading_model,
                    scan_analysis=scan_payload if not failed_only else None,
                    manual_decisions=manual_decisions if not failed_only else None,
                    enhance_images=enhance_images,
                    max_workers=grading_max_workers,
                    requests_per_minute=grading_rpm_limit,
                    grading_mode=grading_mode,
                    failed_only=failed_only,
                ):
                    et = event["event"]
                    if et == "grading_started":
                        current = event.get("current", 0)
                        total = max(event.get("total", 1), 1)
                        logs.append(f"开始批改 {event['student_name']} ({current}/{total})")
                    elif et == "grading_log":
                        logs.append(f"[{event['student_name']}] {event['message']}")
                    elif et == "batch_grading_config":
                        if event.get("grading_mode") == "hybrid_batch":
                            mode_label = "混合批改（客观题批量识别 + 大题横批）"
                        else:
                            mode_label = "标准整卷批改"
                        logs.append(f"批改模式：{mode_label}")
                        logs.append(
                            f"并发批改启动：{event.get('total', 0)} 份，"
                            f"整卷并发 {event.get('max_workers')}，"
                            f"混合在途 {event.get('hybrid_inflight_workers')}，"
                            f"RPM 上限 {event.get('requests_per_minute')}"
                        )
                    elif et == "graded":
                        current = event.get("current", 0)
                        total = max(event.get("total", 1), 1)
                        progress_bar.progress(min(current / total, 1.0))
                        result_id = event.get("result_id")
                        if result_id:
                            try:
                                manual_service.render_result_annotation(int(result_id))
                            except Exception as rexc:  # noqa: BLE001
                                logs.append(f"标注渲染失败(result_id={result_id}): {rexc}")

                        logs.append(
                            f"完成: {event['student_name']} -> {event['score']}/{event['total_score']}"
                            
                        )
                        if event.get("needs_human_review") and result_id:
                            review_text = _review_reason_for_result(db, int(result_id), session)
                            if review_text:
                                logs.append(f"  复核原因: {review_text}")
                    elif et == "grading_failed":
                        current = event.get("current", 0)
                        total = max(event.get("total", 1), 1)
                        progress_bar.progress(min(current / total, 1.0))
                        logs.append(f"失败: {event['student_name']} -> {event['error']}")
                    elif et == "paper_unmatched":
                        logs.append(f"未匹配名单 OCR={event['ocr_name']} ({event['front_image']}, {event['back_image']})")
                    elif et == "scan_issue":
                        logs.append(f"扫描异常跳过: {event.get('source_label')} - {event.get('message')}")
                    elif et == "session_completed":
                        progress_bar.progress(1.0)
                        logs.append("考试批改完成")
                        _write_session_workflow_state(
                            db,
                            selected_session_id,
                            "grading_completed",
                            {"event": event},
                        )

                    st.session_state[grading_log_key] = logs[-400:]
                    _render_grading_log_panel(log_box, st.session_state[grading_log_key])
                    import time
                    time.sleep(0.01)

                st.success("批改流程执行完成")
            except Exception as exc:  # noqa: BLE001
                st.error(f"批改执行失败：{exc}")

    with c2:
        progress = db.get_session_progress(selected_session_id)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("总试卷", progress["total_papers"])
        m2.metric("已匹配", progress["matched_papers"])
        m3.metric("已完成", progress["graded_papers"])
        m4.metric("失败", progress["failed_papers"])

        n1, n2, n3 = st.columns(3)
        n1.metric("未匹配", progress["unmatched_papers"])
        n3.metric("进度(%)", progress["progress_percent"])
        a1, a2 = st.columns(2)
        a1.metric("缺考/未检测到", progress.get("absent_students", 0))
        a2.metric("扫描异常学生", progress.get("scan_issue_students", 0))

        attendance = db.get_session_attendance(selected_session_id)
        absent_rows = [row for row in attendance if row.get("attendance_status") == "absent"]
        if absent_rows:
            with st.expander("缺考 / 未检测到有效答卷", expanded=False):
                st.dataframe(pd.DataFrame(absent_rows), use_container_width=True, hide_index=True)

        review_rows = _session_review_reason_rows(db, selected_session_id, session)
        if review_rows:
            with st.expander("待复核学生与原因", expanded=True):
                st.dataframe(pd.DataFrame(review_rows), use_container_width=True, hide_index=True)

        failed_papers = db.list_failed_papers(selected_session_id)
        if failed_papers:
            with st.expander(f"⚠️ 批改失败学生（{len(failed_papers)} 人）—— 点击查看原因", expanded=True):
                for fp in failed_papers:
                    name = fp.get("student_name") or fp.get("ocr_name") or "未知学生"
                    code = fp.get("student_code") or ""
                    cls = fp.get("class_name") or ""
                    err = str(fp.get("error_message") or "未记录错误信息")
                    label = f"❌ {name}"
                    if code:
                        label += f"（{code}）"
                    if cls:
                        label += f" · {cls}"
                    err_short = err[:300] + "…" if len(err) > 300 else err
                    st.error(f"{label}\n\n**失败原因**：{err_short}")

    if selected_session_id is not None and session:
        st.write("")
        with st.expander("📑 当前考试评分标准查阅与微调 (已折叠)", expanded=False):
            try:
                r_path = Path(session["rubric_path"])
                a_path = Path(session["answer_key_path"])
                if r_path.exists() and a_path.exists():
                    with open(r_path, "r", encoding="utf-8") as rf:
                        rub_data = json.load(rf)
                    with open(a_path, "r", encoding="utf-8") as af:
                        ans_data = json.load(af)
                    active_payload = {"rubric": rub_data, "answer_key": ans_data}
                    _render_active_session_rubric_editor(db, selected_session_id, r_path, a_path, active_payload)
                else:
                    st.warning("未找到当前考试的评分标准 JSON 文件，无法查阅与微调。")
            except Exception as e:
                st.error(f"加载当前考试评分标准失败：{e}")


def _render_grading_log_panel(container: Any, logs: list[str]) -> None:
    visible_logs = [str(item) for item in logs[-400:]]
    body = "\n".join(visible_logs) if visible_logs else "暂无批改日志。点击“开始批改已确认试卷”后，运行状态会保留在这里。"
    container.markdown(
        f"""
        <div class="grading-log-panel">{html.escape(body)}</div>
        """,
        unsafe_allow_html=True,
    )


def _review_reason_for_result(db: DBManager, result_id: int, session: dict[str, Any] | None) -> str:
    score_map, _ = _load_session_score_type_maps(session)
    details = db.get_result_details(result_id)
    info = _review_summary_from_details(details, score_map, True)
    summary = str(info.get("summary") or "").strip()
    if summary:
        return summary
    return "模型标记需要复核，但未定位到具体题目；请在评分审阅页查看。"


def _session_review_reason_rows(db: DBManager, session_id: int, session: dict[str, Any] | None) -> list[dict[str, Any]]:
    score_map, _ = _load_session_score_type_maps(session)
    rows: list[dict[str, Any]] = []
    for result in db.get_session_results(session_id):
        if not result.get("needs_human_review"):
            continue
        result_id = int(result.get("result_id") or 0)
        if not result_id:
            continue
        details = db.get_result_details(result_id)
        info = _review_summary_from_details(details, score_map, True)
        rows.append(
            {
                "学生": result.get("student_name") or "",
                "学号": result.get("student_code") or "",
                "得分": result.get("student_score"),
                "复核原因": info.get("summary") or "模型标记需要复核，但未定位到具体题目；请在评分审阅页查看。",
            }
        )
    return rows


def _scan_analysis_summary(payload: dict[str, Any]) -> dict[str, int]:
    return {
        "auto_matched": len(payload.get("groups") or []),
        "issues": len(payload.get("issues") or []),
        "absent_candidates": len(payload.get("absent_students") or []),
        "total_pages": int(payload.get("total_pages") or 0),
    }


def _render_scan_precheck_panel(payload: dict[str, Any], students: list[dict[str, Any]], session_id: int) -> list[dict]:
    summary = _scan_analysis_summary(payload)
    st.markdown("### 试卷预检与异常处理")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("自动匹配", summary["auto_matched"])
    m2.metric("异常卷", summary["issues"])
    m3.metric("预计缺考", summary["absent_candidates"])
    m4.metric("页面数", summary["total_pages"])

    if st.button("用剩余名单重试低可信姓名匹配", key=f"retry_reduced_name_match_{session_id}", type="secondary"):
        analysis = ScanAnalysis.from_dict(payload)
        refine_scan_analysis_matches(analysis, students)
        next_payload = analysis.to_dict()
        for key in ("enhance_images",):
            if key in payload:
                next_payload[key] = payload[key]
        st.session_state[f"scan_analysis_{session_id}"] = next_payload
        _write_json_file(_session_work_dir(session_id) / "scan_analysis_latest.json", next_payload)
        st.success("已用排除 100% 匹配学生后的剩余名单重新匹配低可信姓名。")
        st.rerun()

    warnings = payload.get("warnings") or []
    if warnings:
        with st.expander("扫描警告", expanded=False):
            for warning in warnings:
                st.warning(str(warning))

    groups = payload.get("groups") or []
    if groups:
        low_conf_groups = [
            group for group in groups
            if float(group.get("match_score") or 0) < 0.999 or str(group.get("match_method") or "") == "fuzzy"
        ]
        students_sorted = sorted(students, key=lambda s: (str(s.get("student_code") or ""), str(s.get("name") or "")))
        if low_conf_groups:
            st.markdown("#### 需要确认的姓名匹配")
            st.caption("默认只显示识别率不到 100% 或相似匹配的试卷；可在这里直接改成对应学生。")
            student_options = ["keep"] + [f"student:{student['id']}" for student in students_sorted]
            student_labels = {"keep": "保持当前匹配"}
            import pypinyin
            for student in students_sorted:
                name = str(student.get('name') or '').strip()
                if not name:
                    student_labels[f"student:{student['id']}"] = str(student.get('student_code') or '')
                else:
                    py = "".join([p[0][0] for p in pypinyin.pinyin(name, style=pypinyin.Style.FIRST_LETTER)]).lower()
                    student_labels[f"student:{student['id']}"] = f"{name} ({py})" if py else name
            for idx, group in enumerate(low_conf_groups, start=1):
                cols = st.columns([1.2, 1.4, 1.2, 1.6])
                cols[0].markdown(f"**{group.get('source_label') or f'试卷{idx}'}**")
                cols[1].caption(f"OCR：{group.get('detected_name') or '未识别'}")
                cols[2].caption(f"相似度：{float(group.get('match_score') or 0):.0%}")
                current_student_id = group.get("student_id")
                default_option = "keep"
                if current_student_id:
                    candidate = f"student:{current_student_id}"
                    if candidate in student_options:
                        default_option = candidate
                selected_option = cols[3].selectbox(
                    "匹配学生",
                    options=student_options,
                    index=student_options.index(default_option),
                    format_func=lambda option: student_labels.get(option, option),
                    key=f"scan_group_match_{session_id}_{group.get('source_label') or idx}",
                    label_visibility="collapsed",
                )
                if selected_option.startswith("student:"):
                    selected_id = int(selected_option.split(":", 1)[1])
                    selected_student = next((student for student in students_sorted if int(student["id"]) == selected_id), None)
                    if selected_student:
                        group["student_id"] = selected_id
                        group["student_name"] = str(selected_student.get("name") or "")
                        group["match_method"] = "manual"
                        group["match_score"] = 1.0

        exact_groups = [group for group in groups if group not in low_conf_groups]
        with st.expander("100% 识别或已确认的可批改试卷", expanded=False):
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "学生": group.get("student_name"),
                            "识别姓名": group.get("detected_name"),
                            "匹配方式": "相似匹配" if group.get("match_method") == "fuzzy" else "精确匹配",
                            "相似度": f"{float(group.get('match_score') or 0):.0%}",
                            "来源": group.get("source_label"),
                        }
                        for group in groups
                        if group not in low_conf_groups
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )

    issues = payload.get("issues") or []
    if not issues:
        st.success("没有需要人工处理的异常卷，可以直接开始批改。")
        return []

    st.info("下面只展示异常卷。你可以把它匹配到学生，或标记为空白/无效卷；未处理异常卷不会交给 AI 批改。")
    students_sorted = sorted(students, key=lambda s: (str(s.get("student_code") or ""), str(s.get("name") or "")))
    option_labels = {"pending": "暂不处理", "invalid": "无效卷 / 空白卷"}
    import pypinyin
    for student in students_sorted:
        name = str(student.get('name') or '').strip()
        if not name:
            option_labels[f"match:{student['id']}"] = str(student.get('student_code') or '')
        else:
            py = "".join([p[0][0] for p in pypinyin.pinyin(name, style=pypinyin.Style.FIRST_LETTER)]).lower()
            option_labels[f"match:{student['id']}"] = f"{name} ({py})" if py else name

    decisions: list[dict] = []
    for idx, issue in enumerate(issues, start=1):
        issue_id = str(issue.get("issue_id") or f"issue_{idx}")
        with st.expander(f"异常卷 #{idx} - {issue.get('source_label') or issue_id}", expanded=True):
            st.caption(f"{issue.get('message') or ''}；AI识别姓名：{issue.get('detected_name') or '未识别'}")
            suggested_id = issue.get("suggested_student_id")
            suggested_name = issue.get("suggested_student_name")
            suggested_score = issue.get("suggested_match_score")
            if suggested_id and suggested_name:
                st.info(f"系统建议匹配：{suggested_name}（相似度 {float(suggested_score or 0):.0%}）。请确认后再交给 AI 批改。")
            cols = st.columns([1, 1, 1])
            front_path = _resolve_session_file_path(issue.get("front_image"))
            back_path = _resolve_session_file_path(issue.get("back_image")) if issue.get("back_image") else None
            with cols[0]:
                st.markdown("**疑似正面**")
                if front_path.exists():
                    st.image(str(front_path), width="stretch")
                else:
                    st.warning("正面图片不存在")
            with cols[1]:
                st.markdown("**疑似反面**")
                if back_path and back_path.exists():
                    st.image(str(back_path), width="stretch")
                else:
                    st.info("没有可配对反面")
            with cols[2]:
                options = ["pending", "invalid"] + [f"match:{student['id']}" for student in students_sorted]
                default_index = 0
                if suggested_id:
                    suggested_option = f"match:{suggested_id}"
                    if suggested_option in options:
                        default_index = options.index(suggested_option)
                value = st.selectbox(
                    "处理方式",
                    options=options,
                    index=default_index,
                    format_func=lambda option: option_labels.get(option, option),
                    key=f"scan_issue_decision_{session_id}_{issue_id}",
                )
                if value == "invalid":
                    decisions.append({"issue_id": issue_id, "action": "invalid"})
                    st.success("该卷将作为无效卷跳过")
                elif value.startswith("match:"):
                    student_id = int(value.split(":", 1)[1])
                    decisions.append({"issue_id": issue_id, "action": "match", "student_id": student_id})
                    if not back_path:
                        st.warning("该异常卷缺少反面，当前不会进入批改。")
                    else:
                        st.success(f"将匹配给：{option_labels[value]}")
                else:
                    st.warning("暂不处理：不会交给 AI 批改")

    return decisions


def render_review_tab(db: DBManager, analytics: AnalyticsService, selected_session_id: int | None) -> None:
    return _render_question_review_tab(db, analytics, selected_session_id)


def _render_question_review_tab(db: DBManager, analytics: AnalyticsService, selected_session_id: int | None) -> None:
    st.subheader("按题号批量复核")
    if selected_session_id is None:
        st.warning("请先在左侧选择考试批改")
        return

    results = db.get_session_results(selected_session_id)
    if not results:
        st.info("当前考试批改暂无结果")
        return

    session = db.get_grading_session(selected_session_id)
    score_map, _type_map = _load_session_score_type_maps(session)
    knowledge_label_map = _load_session_knowledge_label_map(session)
    review_rows = _build_question_review_rows(db, selected_session_id, results, score_map, knowledge_label_map)
    if not review_rows:
        st.info("当前考试暂无可复核的题目明细。")
        return

    question_ids = sorted({str(row["question_id"]) for row in review_rows}, key=_question_sort_key)
    qid_counts = {qid: sum(1 for row in review_rows if row["question_id"] == qid) for qid in question_ids}
    qid_needs_review_counts = {qid: sum(1 for row in review_rows if row["question_id"] == qid and row.get("needs_review")) for qid in question_ids}
    reviewable_qids = [qid for qid in question_ids if qid_needs_review_counts.get(qid, 0) > 0]
    
    if not reviewable_qids:
        st.info("🎉 所有题目均无需人工复核，批改已完成！")
        return

    selector_key = f"review_question_selector_{selected_session_id}"
    pending_key = f"_review_pending_qid_{selected_session_id}"
    if pending_key in st.session_state:
        st.session_state[selector_key] = st.session_state.pop(pending_key)

    selected_qid = st.selectbox(
        "选择需复核的题目",
        options=reviewable_qids,
        format_func=lambda qid: f"{qid} ({qid_needs_review_counts.get(qid, 0)} 人需复核)",
        key=selector_key,
    )
    
    # 始终只展示需要复核的学生
    selected_rows = [row for row in review_rows if row["question_id"] == selected_qid and bool(row.get("needs_review"))]
    selected_rows.sort(key=lambda row: (str(row.get("student_code") or ""), str(row.get("student_name") or "")))


    if not selected_rows:
        st.info("该题在当前筛选条件下没有待展示学生。取消上方筛选可查看全班该题结果。")
        return

    st.markdown(f"### {selected_qid} - 作答区域")
    
    manual_service = ManualReviewService(db, ANNOTATED_DIR)

    with st.form(key=f"review_form_{selected_session_id}_{selected_qid}"):
        _render_question_review_crop_grid(db, selected_rows, selected_session_id, selected_qid)
        submitted = st.form_submit_button("保存本题复核并自动切换下一题", type="primary")

    if submitted:
        try:
            adjustments_by_result: dict[int, list[dict[str, Any]]] = {}
            for row in selected_rows:
                result_id = int(row["result_id"])
                detail_id = int(row["detail_id"])
                key = f"score_input_{selected_session_id}_{selected_qid}_{result_id}_{detail_id}"
                new_score_str = st.session_state.get(key)
                
                if new_score_str is not None:
                    try:
                        new_score = float(new_score_str)
                    except ValueError:
                        new_score = float(row.get("score_awarded") or 0)
                else:
                    new_score = float(row.get("score_awarded") or 0)

                adjustments_by_result.setdefault(result_id, []).append(
                    {
                        "detail_id": detail_id,
                        "score_awarded": new_score,
                        "deduction_reason": "人工复核已确认",
                        "error_category": "已复核",
                        "error_summary": "manual_review_confirmed",
                    }
                )
            
            updated = 0
            for result_id, adjustments in adjustments_by_result.items():
                applied = manual_service.apply_manual_adjustments(result_id, adjustments, highlight_qids=[selected_qid])
                updated += int(applied.get("updated_details") or 0)
            st.success(f"已更新 {updated} 条 {selected_qid} 评分明细，并重新计算总分。")

            try:
                current_idx = reviewable_qids.index(selected_qid)
                if current_idx + 1 < len(reviewable_qids):
                    st.session_state[pending_key] = reviewable_qids[current_idx + 1]
            except ValueError:
                pass

            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(f"保存失败：{exc}")

    with st.expander("辅助查看整卷标注图", expanded=False):
        chosen = st.selectbox(
            "选择学生整卷标注",
            options=selected_rows,
            format_func=_format_review_student,
            key=f"review_fullpaper_selector_{selected_session_id}_{selected_qid}",
        )
        result_id = int(chosen["result_id"])
        annotated = db.get_annotated_result(result_id)
        if not annotated:
            manual_service.render_result_annotation(result_id, highlight_qids=[selected_qid])
            annotated = db.get_annotated_result(result_id)
        if annotated:
            front_path = _resolve_session_file_path(annotated.get("annotated_front_path"))
            back_path = _resolve_session_file_path(annotated.get("annotated_back_path"))
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**标注正面**")
                if front_path.exists():
                    st.image(str(front_path), use_container_width=True)
            with c2:
                st.markdown("**标注反面**")
                if back_path and back_path.exists():
                    st.image(str(back_path), use_container_width=True)

    return


def _build_question_review_rows(
    db: DBManager,
    session_id: int,
    results: list[dict[str, Any]],
    score_map: dict[str, float],
    knowledge_label_map: dict[str, str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        result_id = int(result.get("result_id") or 0)
        raw_json = result.get("raw_json") if isinstance(result.get("raw_json"), dict) else {}
        details = db.get_result_details(result_id)
        for detail in details:
            qid = str(detail.get("question_id") or "").strip()
            if not qid:
                continue
            metadata = _detail_metadata_for_qid(raw_json, qid)
            candidate_scores = metadata.get("candidate_scores") if isinstance(metadata, dict) else []
            knowledge_id = str(detail.get("knowledge_id") or "")
            rows.append(
                {
                    **result,
                    **detail,
                    "session_id": session_id,
                    "question_id": qid,
                    "max_score": float(score_map.get(qid) or 0),
                    "knowledge_label": _knowledge_display_label(knowledge_id, knowledge_label_map.get(knowledge_id, "")),
                    "metadata": metadata,
                    "candidate_scores": candidate_scores if isinstance(candidate_scores, list) else [],
                    "needs_review": _is_substantive_review_reason(
                        str(detail.get("deduction_reason") or ""),
                        str(detail.get("error_category") or ""),
                        detail.get("confidence_score"),
                    ),
                }
            )
    return rows


def _detail_metadata_for_qid(raw_json: dict[str, Any], qid: str) -> dict[str, Any]:
    metadata_map = raw_json.get("detail_metadata") if isinstance(raw_json, dict) else None
    if not isinstance(metadata_map, dict):
        return {}
    direct = metadata_map.get(qid)
    return direct if isinstance(direct, dict) else {}


def _candidate_scores_text(candidate_scores: Any) -> str:
    if not isinstance(candidate_scores, list) or not candidate_scores:
        return ""
    parts = []
    for item in candidate_scores:
        if not isinstance(item, dict):
            continue
        score = item.get("score")
        confidence = item.get("confidence")
        reason = str(item.get("reason") or "").strip()
        prefix = f"{score}分"
        if confidence is not None:
            prefix += f"/{confidence}"
        parts.append(prefix + (f": {reason}" if reason else ""))
    return "；".join(parts)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except Exception:
        pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _format_review_student(row: dict[str, Any]) -> str:
    return f"[{row.get('student_code')}] {row.get('student_name')} - {row.get('question_id')} - {float(row.get('score_awarded') or 0):g}分"


def _render_question_review_crop_grid(db: DBManager, rows: list[dict[str, Any]], session_id: int, qid: str) -> None:
    for start in range(0, len(rows), 3):
        cols = st.columns(3)
        for col, row in zip(cols, rows[start:start + 3]):
            with col:
                student_name = row.get("student_name")
                current_score = float(row.get('score_awarded') or 0)
                max_score = float(row.get('max_score') or 0)
                result_id = int(row['result_id'])
                detail_id = int(row['detail_id'])
                
                c1, c2, c3 = st.columns([2, 2, 1])
                with c1:
                    st.markdown(f"**{student_name}**")
                with c2:
                    st.text_input(
                        "得分",
                        value=f"{current_score:g}",
                        key=f"score_input_{session_id}_{qid}_{result_id}_{detail_id}",
                        label_visibility="collapsed"
                    )
                with c3:
                    st.markdown(f"/ {max_score:g}")

                reason = str(row.get("deduction_reason") or row.get("error_summary") or "").strip()
                if reason:
                    st.caption(_short_text(reason, 90))
                crop = _build_answer_region_crop_preview(db, row)
                if crop is not None:
                    st.image(crop, use_container_width=True)
                else:
                    st.info("未找到该题框映射，无法裁剪。")


def _load_session_score_type_maps(session: dict | None) -> tuple[dict[str, float], dict[str, str]]:
    if not session:
        return {}, {}
    rubric_path = _resolve_session_file_path(session.get("rubric_path"))
    rubric = _read_json_safely(rubric_path) if rubric_path.exists() else {}
    score_map: dict[str, float] = {}
    type_map: dict[str, str] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return score_map, type_map
    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "").strip()
        qtype = str(question.get("question_type") or "").strip()
        if qid:
            score_map[qid] = _to_float(question.get("max_score"), 0.0)
            type_map[qid] = qtype
        parts = question.get("parts")
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                pid = str(part.get("part_id") or "").strip()
                if pid:
                    score_map[pid] = _to_float(part.get("part_score"), 0.0)
                    type_map[pid] = qtype
    return score_map, type_map


def _load_session_knowledge_label_map(session: dict | None) -> dict[str, str]:
    if not session:
        return {}
    rubric_path = _resolve_session_file_path(session.get("rubric_path"))
    rubric = _read_json_safely(rubric_path) if rubric_path.exists() else {}
    labels: dict[str, str] = {}
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return labels
    for question in questions:
        if not isinstance(question, dict):
            continue
        for point in question.get("knowledge_points", []) or []:
            if not isinstance(point, dict):
                continue
            kid = str(point.get("knowledge_id") or point.get("id") or "").strip()
            if not kid or kid == "UNKNOWN":
                continue
            label = _knowledge_display_label(kid, str(point.get("knowledge_name") or point.get("name") or ""))
            if label and label not in ("未命名知识点", "未知知识点"):
                labels[kid] = label
        kid = str(question.get("knowledge_id") or "").strip()
        if not kid or kid == "UNKNOWN":
            continue
        label = _knowledge_display_label(kid, str(question.get("knowledge_name") or question.get("stem_summary") or ""))
        if label and label not in ("未命名知识点", "未知知识点"):
            labels.setdefault(kid, label)
    return labels


def _review_summary_from_details(details: list[dict[str, Any]], score_map: dict[str, float], model_flag: bool) -> dict[str, Any]:
    issue_items: list[str] = []
    for detail in details:
        qid = str(detail.get("question_id") or "").strip()
        reason = str(detail.get("deduction_reason") or "").strip()
        if not qid or not reason:
            continue
        if not _is_substantive_review_reason(reason, str(detail.get("error_category") or ""), detail.get("confidence_score")):
            continue
        issue_items.append(f"{qid}: {_short_text(reason, 28)}")

    if issue_items:
        return {"needs_review": True, "summary": "；".join(issue_items[:3])}
    if model_flag:
        return {"needs_review": False, "summary": "模型曾标记复核，但未定位到具体题目；若只是姓名差异，可忽略。"}
    return {"needs_review": False, "summary": ""}


def _is_substantive_review_reason(reason: str, cat: str = "", confidence: Any = None) -> bool:
    import pandas as pd
    text = str(reason or "").strip()
    cat_text = "" if pd.isna(cat) or cat is None else str(cat).strip()
    if "已复核" in cat_text or "人工复核" in cat_text:
        return False

    # Align exactly with the AI model's review threshold and explicit markers.
    # Stop using keyword screening like "错", "漏", "少", "多" which falsely flagged all wrong answers.
    review_markers = ["需复核", "踴", "锟借复锟"]
    if any(marker in cat_text for marker in review_markers):
        return True
    if any(marker in text for marker in review_markers):
        return True

    # As a fallback, check the AI model's confidence threshold directly
    if pd.notna(confidence) and confidence is not None:
        try:
            if float(confidence) < 80.0:
                return True
        except ValueError:
            pass

    return False

def _short_text(text: str, max_len: int) -> str:
    compact = " ".join(str(text or "").replace("\n", " ").split())
    return compact[:max_len] + ("..." if len(compact) > max_len else "")


def render_global_weak_points_tab(db: DBManager, analytics: AnalyticsService) -> None:
    st.subheader("跨考试知识图谱（全局）")
    active_sessions = db.list_grading_sessions(include_deleted=False)
    if not active_sessions:
        st.info("暂无未放入回收站的考试批改数据")
        return

    st.caption(f"当前只统计未放入回收站的考试批改，共 {len(active_sessions)} 场。放入回收站的考试不会进入全局知识图谱。")

    selected_session_ids = _render_active_session_multiselect(active_sessions)
    score_rates = db.get_active_student_score_rates()
    filter_cols = st.columns([1, 1, 2])
    with filter_cols[0]:
        min_rate = st.number_input("最低得分率 %", min_value=0.0, max_value=100.0, value=0.0, step=5.0)
    with filter_cols[1]:
        max_rate = st.number_input("最高得分率 %", min_value=0.0, max_value=100.0, value=100.0, step=5.0)
    if min_rate > max_rate:
        min_rate, max_rate = max_rate, min_rate

    eligible_ids = {
        int(row["student_id"])
        for row in score_rates
        if min_rate <= float(row.get("avg_score_rate") or 0) <= max_rate
    }

    students = sorted(
        [s for s in db.list_students() if int(s["id"]) in eligible_ids],
        key=lambda item: (str(item.get("student_code") or ""), str(item.get("name") or "")),
    )
    student_id = _render_student_button_selector(students, score_rates, min_rate, max_rate)
    rows = db.get_active_global_weak_points(student_id=student_id, session_ids=selected_session_ids)
    if student_id is None:
        rows = [row for row in rows if int(row.get("student_id") or 0) in eligible_ids]
    weak_df = analytics._weak_points_dataframe_from_rows(rows)

    if weak_df.empty:
        st.info("暂无可用于生成知识图谱的薄弱点数据")
        return

    _render_knowledge_graph_from_rows(rows, aggregate=student_id is None, session_ids=selected_session_ids)
    with st.expander("查看明细表格", expanded=False):
        st.dataframe(weak_df, use_container_width=True, hide_index=True)

    detail = _read_graph_detail_query()
    if detail and detail.get("view") == "kg_detail" and detail.get("knowledge_id"):
        _render_knowledge_wrong_detail(
            db,
            detail.get("student_id"),
            str(detail.get("knowledge_id")),
            detail.get("session_ids") or None,
        )
    elif detail and detail.get("view") == "error_detail" and detail.get("error_category"):
        _render_error_wrong_detail(
            db,
            str(detail.get("error_category")),
            detail.get("student_id"),
            detail.get("session_ids") or None,
        )


def _render_knowledge_graph(db: DBManager, session_id: int, student_id: int | None = None) -> None:
    rows = db.get_session_weak_points(session_id=session_id, student_id=student_id)
    _render_knowledge_graph_from_rows(rows, aggregate=student_id is None)


def _render_active_session_multiselect(active_sessions: list[dict[str, Any]]) -> list[int]:
    options = [int(item["id"]) for item in active_sessions]
    name_map = {
        int(item["id"]): f"#{item['id']} {item.get('session_name') or item.get('name') or '未命名考试'}"
        for item in active_sessions
    }
    selected = st.multiselect(
        "纳入图谱的考试批改",
        options=options,
        default=options,
        format_func=lambda value: name_map.get(int(value), str(value)),
        help="默认汇总所有未放入回收站的考试；取消勾选后，只统计选中的考试结果。",
        key="global_graph_session_filter",
    )
    return [int(value) for value in selected] or options


def _render_knowledge_graph_from_rows(
    rows: list[dict[str, Any]],
    aggregate: bool = False,
    session_ids: list[int] | None = None,
) -> None:
    if not rows:
        return

    st.markdown("### 学生知识图谱")
    st.caption("颜色表示掌握风险：绿色稳定，蓝色轻微欠缺，黄色需要讲评，红色为重点薄弱。")

    grouped = _aggregate_knowledge_rows(rows) if aggregate else _group_rows_by_student(rows)

    branch_html: list[str] = []
    for student_name, student_rows in list(grouped.items())[:16]:
        leaves: list[str] = []
        sorted_rows = sorted(
            student_rows,
            key=lambda item: (
                _rate_value(item.get("weighted_score_rate"), default=100.0),
                -int(item.get("deduction_count") or 0),
                str(item.get("knowledge_id") or ""),
            ),
        )[:14]
        for row in sorted_rows:
            raw_knowledge_id = str(row.get("knowledge_id") or "UNKNOWN")
            query_knowledge_id = _knowledge_query_id(row) or raw_knowledge_id
            raw_knowledge_label = str(row.get("knowledge_label") or raw_knowledge_id)
            knowledge_title = html.escape(_knowledge_display_label(raw_knowledge_id, raw_knowledge_label))
            student_id = int(row.get("student_id") or 0)
            session_query = _graph_session_query(session_ids)
            if aggregate or student_id <= 0:
                link = f"?view=kg_detail&kg_knowledge_id={quote(query_knowledge_id)}{session_query}"
            else:
                link = f"?view=kg_detail&kg_student_id={student_id}&kg_knowledge_id={quote(query_knowledge_id)}{session_query}"
            deduction_count = int(row.get("deduction_count") or 0)
            item_count = max(1, int(row.get("item_count") or 1))
            score_rate = _rate_value(row.get("weighted_score_rate"), default=100.0)
            reasons = html.escape(str(row.get("sample_reasons") or raw_knowledge_label or "暂无扣分原因"))
            level = _knowledge_mastery_level(score_rate)
            leaves.append(
                f'<a class="kg-leaf kg-level-{level}" href="{link}" title="{reasons}">'
                f'<div class="kg-leaf-title">{knowledge_title}</div>'
                f'<div class="kg-leaf-meta">得分率 {score_rate:.1f}% · 扣分 {deduction_count}/{item_count}</div>'
                '</a>'
            )
        branch_html.append(
            '<div class="kg-student-branch">'
            f'<div class="kg-student">{html.escape(student_name)}</div>'
            f'<div class="kg-knowledge-list">{"".join(leaves)}</div>'
            '</div>'
        )

    graph_html = (
        '<div class="kg-tree">'
        '<div class="kg-tree-root">全局知识图谱</div>'
        f'<div class="kg-tree-body">{"".join(branch_html)}</div>'
        '</div>'
    )
    st.markdown(graph_html, unsafe_allow_html=True)


def _group_rows_by_student(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        student_key = f"{row.get('student_code') or ''} {row.get('student_name') or '全部学生'}".strip()
        grouped.setdefault(student_key, []).append(row)
    return grouped


def _knowledge_query_id(row: dict[str, Any]) -> str:
    values = row.get("knowledge_ids")
    if isinstance(values, list):
        ids = [str(item).strip() for item in values if str(item).strip()]
        if ids:
            return "|".join(ids)
    return str(row.get("knowledge_id") or "").strip()


def _aggregate_knowledge_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        kid = str(row.get("knowledge_id") or "UNKNOWN")
        label = str(row.get("knowledge_label") or kid)
        display_label = _knowledge_display_label(kid, label)
        bucket_key = display_label or kid
        item = buckets.setdefault(
            bucket_key,
            {
                **row,
                "student_id": 0,
                "student_code": "",
                "student_name": "筛选学生合计",
                "knowledge_label": label,
                "knowledge_ids": [],
                "score_sum": 0.0,
                "full_score_sum": 0.0,
                "deduction_count": 0,
                "item_count": 0,
                "exam_count": 0,
                "_exam_ids": set(),
                "_reasons": set(),
            },
        )
        item["score_sum"] += float(row.get("score_sum") or 0)
        item["full_score_sum"] += float(row.get("full_score_sum") or 0)
        item["deduction_count"] += int(row.get("deduction_count") or 0)
        item["item_count"] += int(row.get("item_count") or 0)
        for value in str(_knowledge_query_id(row)).replace("|", ",").split(","):
            value = value.strip()
            if value and value not in item["knowledge_ids"]:
                item["knowledge_ids"].append(value)
        item["_exam_ids"].add(str(row.get("exam_count") or ""))
        reason = str(row.get("sample_reasons") or "").strip()
        if reason:
            item["_reasons"].add(reason)
    result: list[dict[str, Any]] = []
    for item in buckets.values():
        full = float(item.get("full_score_sum") or 0)
        score = float(item.get("score_sum") or 0)
        item["weighted_score_rate"] = round(score / full * 100, 2) if full > 0 else 100.0
        item["exam_count"] = len(item.get("_exam_ids") or [])
        item["sample_reasons"] = "；".join(sorted(item.get("_reasons") or []))
        item.pop("_exam_ids", None)
        item.pop("_reasons", None)
        result.append(item)
    return {"筛选学生合计": result}


def _render_error_graph_from_rows(
    rows: list[dict[str, Any]],
    aggregate: bool = False,
    session_ids: list[int] | None = None,
) -> None:
    if not rows:
        return
    st.markdown("### 错因图谱")
    st.caption("按 AI 推测错因聚合；颜色仍按该类错因涉及题目的加权得分率显示。")
    grouped = _aggregate_error_rows(rows) if aggregate else _group_error_rows_by_student(rows)
    branch_html: list[str] = []
    for student_name, student_rows in list(grouped.items())[:16]:
        leaves: list[str] = []
        sorted_rows = sorted(
            student_rows,
            key=lambda item: (-int(item.get("deduction_count") or 0), _rate_value(item.get("weighted_score_rate"), default=100.0)),
        )[:14]
        for row in sorted_rows:
            raw_category = str(row.get("error_category") or "其他")
            title = html.escape(raw_category)
            student_id = int(row.get("student_id") or 0)
            session_query = _graph_session_query(session_ids)
            if aggregate or student_id <= 0:
                link = f"?view=error_detail&error_category={quote(raw_category)}{session_query}"
            else:
                link = f"?view=error_detail&kg_student_id={student_id}&error_category={quote(raw_category)}{session_query}"
            deduction_count = int(row.get("deduction_count") or 0)
            item_count = max(1, int(row.get("item_count") or 1))
            score_rate = float(row.get("weighted_score_rate") or 0.0)
            reasons = html.escape(str(row.get("sample_reasons") or "暂无错因摘要"))
            level = _knowledge_mastery_level(score_rate)
            leaves.append(
                f'<a class="kg-leaf kg-level-{level}" href="{link}" title="{reasons}">'
                f'<div class="kg-leaf-title">{title}</div>'
                f'<div class="kg-leaf-meta">得分率 {score_rate:.1f}% · 失分 {deduction_count}/{item_count}</div>'
                '</a>'
            )
        branch_html.append(
            '<div class="kg-student-branch">'
            f'<div class="kg-student">{html.escape(student_name)}</div>'
            f'<div class="kg-knowledge-list">{"".join(leaves)}</div>'
            '</div>'
        )
    st.markdown(
        '<div class="kg-tree"><div class="kg-tree-root">错因图谱</div>'
        f'<div class="kg-tree-body">{"".join(branch_html)}</div></div>',
        unsafe_allow_html=True,
    )


def _group_error_rows_by_student(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        student_key = f"{row.get('student_code') or ''} {row.get('student_name') or '全部学生'}".strip()
        grouped.setdefault(student_key, []).append(row)
    return grouped


def _aggregate_error_rows(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        category = str(row.get("error_category") or "其他")
        item = buckets.setdefault(
            category,
            {
                **row,
                "student_id": 0,
                "student_code": "",
                "student_name": "筛选学生合计",
                "score_sum": 0.0,
                "full_score_sum": 0.0,
                "deduction_count": 0,
                "item_count": 0,
                "_reasons": set(),
            },
        )
        item["score_sum"] += float(row.get("score_sum") or 0)
        item["full_score_sum"] += float(row.get("full_score_sum") or 0)
        item["deduction_count"] += int(row.get("deduction_count") or 0)
        item["item_count"] += int(row.get("item_count") or 0)
        reason = str(row.get("sample_reasons") or "").strip()
        if reason:
            item["_reasons"].add(reason)
    result: list[dict[str, Any]] = []
    for item in buckets.values():
        full = float(item.get("full_score_sum") or 0)
        score = float(item.get("score_sum") or 0)
        item["weighted_score_rate"] = round(score / full * 100, 2) if full > 0 else 0.0
        item["sample_reasons"] = "；".join(sorted(item.get("_reasons") or []))
        item.pop("_reasons", None)
        result.append(item)
    return {"筛选学生合计": result}


def _knowledge_display_label(knowledge_id: str, knowledge_label: str) -> str:
    kid = str(knowledge_id or "").strip()
    label = str(knowledge_label or "").strip()
    if kid and label.startswith(kid):
        import re
        label = label[len(kid):].strip()
        label = re.sub(r"^[\s:\-|_·\u00b7]+", "", label).strip()
    # label 含中文：直接使用
    if label and label != kid and any("\u4e00" <= ch <= "\u9fff" for ch in label):
        return label
    # label 不含中文（代码或英文）：尝试从 registry 查找中文名
    effective = kid or label
    if effective and effective != "UNKNOWN":
        try:
            from question_bank.taxonomy.registry import canonicalize_knowledge
            canonical = canonicalize_knowledge(effective)
            if canonical is not None:
                return canonical.canonical_name
        except Exception:
            pass
        # kid 本身是中文（如 knowledge_id 直接存的中文名）
        if any("\u4e00" <= ch <= "\u9fff" for ch in effective):
            return effective
        return effective  # 显示代码本身（如 C2_01）
    return "未知知识点"


def _render_student_button_selector(
    students: list[dict[str, Any]],
    score_rates: list[dict[str, Any]],
    min_rate: float,
    max_rate: float,
) -> int | None:
    rate_map = {int(row["student_id"]): float(row.get("avg_score_rate") or 0.0) for row in score_rates}
    state_key = "kg_selected_student_id"
    selected_id = st.session_state.get(state_key)
    valid_ids = {int(student["id"]) for student in students}
    if selected_id not in valid_ids:
        selected_id = None
        st.session_state[state_key] = None

    with st.expander("按学生筛选", expanded=True):
        st.caption(f"当前显示平均得分率 {min_rate:g}% - {max_rate:g}% 区间内的学生；按钮按学号排序，每行 3 人。")
        all_type = "primary" if selected_id is None else "secondary"
        if st.button("全部学生", key="kg_student_all", type=all_type, use_container_width=True):
            st.session_state[state_key] = None
            st.rerun()

        if not students:
            st.info("当前得分率区间内没有学生。")
            return None

        for row_start in range(0, len(students), 3):
            cols = st.columns(3)
            for col, student in zip(cols, students[row_start:row_start + 3]):
                sid = int(student["id"])
                label = f"{student.get('student_code', '')}｜{student.get('name', '')}｜{rate_map.get(sid, 0):.1f}%"
                with col:
                    if st.button(
                        label,
                        key=f"kg_student_btn_{sid}",
                        type="primary" if sid == selected_id else "secondary",
                        use_container_width=True,
                    ):
                        st.session_state[state_key] = sid
                        st.rerun()
    return int(st.session_state[state_key]) if st.session_state.get(state_key) is not None else None


def _knowledge_mastery_level(score_rate: float) -> str:
    if score_rate >= 90:
        return "green"
    if score_rate >= 75:
        return "blue"
    if score_rate >= 60:
        return "yellow"
    return "red"


def _rate_value(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _graph_session_query(session_ids: list[int] | None) -> str:
    if not session_ids:
        return ""
    value = ",".join(str(int(item)) for item in session_ids)
    return f"&kg_sessions={quote(value)}"


def _read_graph_detail_query() -> dict[str, Any] | None:
    try:
        params = st.query_params
    except Exception:
        return None

    def first_value(name: str) -> str | None:
        value = params.get(name)
        if isinstance(value, list):
            return str(value[0]) if value else None
        return str(value) if value is not None else None

    view = first_value("view")
    if view not in {"kg_detail", "error_detail"}:
        return None

    raw_student = first_value("kg_student_id")
    try:
        student_id = int(raw_student) if raw_student not in {None, ""} else None
    except (TypeError, ValueError):
        student_id = None
    if student_id == 0:
        student_id = None

    raw_sessions = first_value("kg_sessions")
    session_ids: list[int] = []
    if raw_sessions:
        for part in unquote(raw_sessions).split(","):
            try:
                session_ids.append(int(part))
            except ValueError:
                continue

    return {
        "view": view,
        "student_id": student_id,
        "knowledge_id": unquote(first_value("kg_knowledge_id") or "") or None,
        "error_category": unquote(first_value("error_category") or "") or None,
        "session_ids": session_ids,
    }


def _read_knowledge_detail_query() -> tuple[int | None, str | None]:
    try:
        params = st.query_params
        raw_view = params.get("view")
        raw_student = params.get("kg_student_id")
        raw_knowledge = params.get("kg_knowledge_id")
    except Exception:
        return None, None

    if isinstance(raw_student, list):
        raw_student = raw_student[0] if raw_student else None
    if isinstance(raw_knowledge, list):
        raw_knowledge = raw_knowledge[0] if raw_knowledge else None
    if isinstance(raw_view, list):
        raw_view = raw_view[0] if raw_view else None
    if raw_view and str(raw_view) != "kg_detail":
        return None, None
    try:
        student_id = int(raw_student) if raw_student is not None else None
    except (TypeError, ValueError):
        student_id = None
    knowledge_id = unquote(str(raw_knowledge)) if raw_knowledge else None
    return student_id, knowledge_id


def _render_knowledge_wrong_detail(
    db: DBManager,
    student_id: int | None,
    knowledge_id: str,
    session_ids: list[int] | None = None,
) -> None:
    if student_id is None:
        items = db.get_representative_wrong_items_for_knowledge(knowledge_id, session_ids=session_ids, limit=1)
    else:
        items = db.get_active_items_for_knowledge(student_id, knowledge_id)
        if session_ids:
            session_set = {int(item) for item in session_ids}
            items = [item for item in items if int(item.get("session_id") or 0) in session_set]
    st.markdown('<div id="knowledge-detail"></div>', unsafe_allow_html=True)
    st.markdown("### 知识点作答档案")
    if not items:
        st.info("该学生在未放入回收站的考试中暂无该知识点的作答记录。")
        return

    first = items[0]
    avg_rate = sum(float(item.get("score_rate") or 0) for item in items) / max(1, len(items))
    deducted_count = sum(1 for item in items if item.get("is_deducted"))
    detail_knowledge_label = _knowledge_display_label(knowledge_id, str(first.get("knowledge_label") or ""))
    st.markdown(
        f"""
        <div class="kg-detail-hero">
          <div class="kg-detail-eyebrow">Knowledge Trace</div>
          <div class="kg-detail-title">[{html.escape(str(first.get('student_code') or ''))}] {html.escape(str(first.get('student_name') or ''))}</div>
          <div class="kg-detail-subtitle">{html.escape(detail_knowledge_label)}</div>
          <div class="kg-detail-metrics">
            <span>涉及题目 {len(items)} 道</span>
            <span>失分题 {deducted_count} 道</span>
            <span>加权/记录均值 {avg_rate:.1f}%</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("返回全局知识图谱", key="kg_detail_back"):
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()

    for idx, item in enumerate(items, start=1):
        score_rate = float(item.get("score_rate") or 0)
        is_deducted = bool(item.get("is_deducted"))
        title = (
            f"{idx}. {item.get('session_name')} | {item.get('question_id')} | "
            f"{float(item.get('score_awarded') or 0):g}/{float(item.get('max_score') or 0):g} "
            f"({score_rate:.1f}%)"
        )
        with st.expander(title, expanded=is_deducted or score_rate < 100):
            badge = "失分" if is_deducted else "满分"
            st.markdown(_status_badge(badge, "red" if is_deducted else "green"), unsafe_allow_html=True)
            st.caption(f"考试时间：{item.get('graded_at') or '未知'}")
            reason = str(item.get("deduction_reason") or "").strip()
            if is_deducted and reason:
                st.caption(f"扣分理由：{reason}")
            elif not is_deducted:
                st.caption("本题满分，默认折叠；展开后可查看对应原卷区域。")
            highlighted = _build_wrong_question_preview(db, item)
            if highlighted is not None:
                st.image(highlighted, use_container_width=True)
            else:
                st.info("未找到该题对应的题框映射，无法在原卷上高亮。")


def _render_error_wrong_detail(
    db: DBManager,
    error_category: str,
    student_id: int | None = None,
    session_ids: list[int] | None = None,
) -> None:
    items = db.get_representative_wrong_items_for_error(
        error_category,
        student_id=student_id,
        session_ids=session_ids,
        limit=1 if student_id is None else 20,
    )
    st.markdown('<div id="error-detail"></div>', unsafe_allow_html=True)
    st.markdown("### 错因对应错题")
    if not items:
        st.info("没有找到该错因对应的失分题目。")
        return

    first = items[0]
    st.markdown(
        f"""
        <div class="kg-detail-hero">
          <div class="kg-detail-eyebrow">Error Trace</div>
          <div class="kg-detail-title">{html.escape(error_category)}</div>
          <div class="kg-detail-subtitle">代表样本：[{html.escape(str(first.get('student_code') or ''))}] {html.escape(str(first.get('student_name') or ''))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if st.button("返回知识/错因图谱", key="error_detail_back"):
        try:
            st.query_params.clear()
        except Exception:
            pass
        st.rerun()

    for idx, item in enumerate(items, start=1):
        title = (
            f"{idx}. {item.get('session_name')} | {item.get('question_id')} | "
            f"{float(item.get('score_awarded') or 0):g}/{float(item.get('max_score') or 0):g}"
        )
        with st.expander(title, expanded=True):
            reason = str(item.get("error_summary") or item.get("deduction_reason") or "").strip()
            if reason:
                st.caption(f"错因摘要：{reason}")
            highlighted = _build_wrong_question_preview(db, item)
            if highlighted is not None:
                st.image(highlighted, use_container_width=True)
            else:
                st.info("未找到该题对应的题框映射，无法在原卷上高亮。")


def _build_wrong_question_preview(db: DBManager, item: dict[str, Any]) -> Image.Image | None:
    session_id = int(item.get("session_id") or 0)
    qid = str(item.get("question_id") or "")
    regions = answer_regions_with_template_source_sizes(db, session_id, data_root=APP_DATA_DIR)
    matched_regions = [
        region for region in regions
        if str(region.get("mapped_question_id") or region.get("detected_question_id") or "") == qid
    ]
    if not matched_regions:
        return None
    region = matched_regions[0]
    page = str(region.get("page") or "front")
    image_path = _resolve_session_file_path(item.get("front_image") if page == "front" else item.get("back_image"))
    if not image_path.exists():
        return None

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _load_preview_font()
    x, y, right, bottom = scaled_region_bbox(region, image.width, image.height)
    w = right - x
    h = bottom - y
    draw.rectangle([x, y, right, bottom], outline=(220, 38, 38), width=max(5, image.width // 240))
    label = f"{qid} 失分：{float(item.get('max_score') or 0) - float(item.get('score_awarded') or 0):g}"
    bbox = draw.textbbox((0, 0), label, font=font)
    tx = min(max(0, x + w - (bbox[2] - bbox[0]) - 18), max(0, image.width - (bbox[2] - bbox[0]) - 18))
    ty = min(max(0, y + h - (bbox[3] - bbox[1]) - 18), max(0, image.height - (bbox[3] - bbox[1]) - 18))
    pad = 8
    bbox = draw.textbbox((tx, ty), label, font=font)
    draw.rectangle([bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad], fill=(254, 242, 242), outline=(220, 38, 38), width=2)
    draw.text((tx, ty), label, fill=(185, 28, 28), font=font)
    return image


def render_export_tab(selected_session_id: int | None) -> None:
    st.subheader("报表导出与班级题目分析")
    if selected_session_id is None:
        st.warning("请先在左侧选择考试批改")
        return

    _reset_report_score_input_state(selected_session_id)
    flash_key = f"report_score_review_flash_{selected_session_id}"
    flash_message = st.session_state.pop(flash_key, None)
    if flash_message:
        st.success(str(flash_message))

    st.caption("先筛选班级查看各题得分率；点击题目行后，可核查全体学生的作答框并直接调整分数。")
    analysis = _build_session_question_analysis(DBManager(DB_PATH), selected_session_id)
    if not analysis["rows"]:
        st.info("当前考试暂无可分析的题目明细。")
        _render_report_export_controls(selected_session_id)
        return

    class_options = ["全部班级"] + analysis["classes"]
    selected_class = st.selectbox("按班级筛选", class_options, key=f"export_class_filter_{selected_session_id}")
    if selected_class == "全部班级":
        rows = _merge_question_analysis_rows(analysis["rows"])
    else:
        rows = [
            row for row in analysis["rows"]
            if row["班级"] == selected_class
        ]
    if not rows:
        st.info("该班级暂无批改明细。")
        _render_report_export_controls(selected_session_id)
        return

    rows = sorted(rows, key=lambda item: (float(item.get("班级得分率") or 0), _question_sort_key(str(item.get("题号") or ""))))
    matrix_df = pd.DataFrame(rows)
    display_df = matrix_df[["班级", "题号", "满分", "班级得分率", "平均得分", "失分人数", "失分学生与扣分"]].copy()

    st.markdown("### 班级题目得分矩阵")
    st.caption("点击包含失分名单的题目行，下方会索引出这些学生该题作答框。")
    selected_row_idx = _render_selectable_question_matrix(display_df, selected_session_id)
    if selected_row_idx is None:
        selected_row_idx = 0

    selected_row = rows[int(selected_row_idx)]
    selected_qid = str(selected_row["题号"])
    review_rows = _build_report_score_review_rows(
        DBManager(DB_PATH),
        selected_session_id,
        selected_class,
        selected_qid,
    )
    _render_report_score_review_grid(selected_session_id, selected_class, selected_qid, review_rows)
    _render_report_export_controls(selected_session_id)


def _build_report_score_review_rows(
    db: DBManager,
    session_id: int,
    selected_class: str,
    selected_qid: str,
) -> list[dict[str, Any]]:
    session = db.get_grading_session(session_id)
    score_map, _type_map = _load_session_score_type_maps(session)
    selected_max_score = score_map.get(selected_qid)
    selected_parent_id = _question_parent_id(selected_qid)
    rows: list[dict[str, Any]] = []
    for result in db.get_session_results(session_id):
        class_name = str(result.get("class_name") or "未分班")
        if selected_class != "全部班级" and class_name != selected_class:
            continue
        result_id = int(result["result_id"])
        details = db.get_result_details(result_id)
        direct_details: list[dict[str, Any]] = []
        for detail in details:
            raw_qid = str(detail.get("question_id") or "").strip()
            canonical_qid = _canonical_question_id_for_score(raw_qid, score_map)
            if canonical_qid != selected_qid:
                continue
            direct_details.append(detail)
            max_score = score_map.get(raw_qid)
            if max_score is None:
                max_score = score_map.get(canonical_qid)
            rows.append(
                {
                    **result,
                    **detail,
                    "session_id": session_id,
                    "question_id": raw_qid,
                    "canonical_question_id": canonical_qid,
                    "max_score": float(max_score) if max_score is not None else None,
                    "source_question_id": raw_qid,
                    "source_score_awarded": float(detail.get("score_awarded") or 0),
                    "is_inferred_full_score": False,
                }
            )
        if direct_details or selected_parent_id is None or selected_max_score is None:
            continue

        parent_detail = next(
            (
                detail
                for detail in details
                if str(detail.get("question_id") or "").strip() == selected_parent_id
            ),
            None,
        )
        parent_max_score = score_map.get(selected_parent_id)
        if parent_detail is None or parent_max_score is None:
            continue
        parent_score = float(parent_detail.get("score_awarded") or 0)
        if parent_score < float(parent_max_score) - 1e-6:
            continue
        rows.append(
            {
                **result,
                **parent_detail,
                "session_id": session_id,
                "question_id": selected_qid,
                "canonical_question_id": selected_qid,
                "score_awarded": float(selected_max_score),
                "max_score": float(selected_max_score),
                "source_question_id": selected_parent_id,
                "source_score_awarded": parent_score,
                "is_inferred_full_score": True,
            }
        )
    return sorted(
        rows,
        key=lambda item: (
            str(item.get("class_name") or ""),
            str(item.get("student_code") or ""),
            str(item.get("student_name") or ""),
            _question_sort_key(str(item.get("question_id") or "")),
        ),
    )


def _render_report_score_review_grid(
    session_id: int,
    selected_class: str,
    selected_qid: str,
    rows: list[dict[str, Any]],
) -> None:
    st.markdown(f"### {selected_class} · {selected_qid} 全员评分复核")
    if not rows:
        st.info("当前筛选范围没有可调整的评分明细。")
        return

    st.caption("分数调整会保存到批改结果并自动重算学生总分。仅展示题框裁剪内容，不展开完整卷面。")
    deducted_rows = [item for item in rows if not _report_row_is_full_score(item)]
    full_score_rows = [item for item in rows if _report_row_is_full_score(item)]
    form_key = f"report_score_review_form_{session_id}_{selected_class}_{selected_qid}"
    with st.form(key=form_key):
        if deducted_rows:
            _render_report_score_cards(session_id, deducted_rows)
        else:
            st.success("当前题目没有已记录的失分学生。")

        if full_score_rows:
            with st.expander(f"查看满分同学复核（{len(full_score_rows)} 人）", expanded=False):
                _render_report_score_cards(session_id, full_score_rows)

        submitted = st.form_submit_button("保存本题全部评分调整", type="primary", use_container_width=True)

    if not submitted:
        return

    try:
        adjustments: list[dict[str, Any]] = []
        for item in rows:
            current_score = float(item.get("score_awarded") or 0)
            input_key = _report_score_input_key(session_id, item)
            new_score = _optional_float(st.session_state.get(input_key))
            if new_score is None:
                raise ValueError(f"{item.get('student_name')} · {item.get('question_id')} 的得分不是有效数字。")
            if abs(new_score - current_score) > 1e-6:
                adjustments.append(
                    {
                        "detail_id": int(item["detail_id"]),
                        "score_awarded": _report_persisted_score(item, new_score),
                    }
                )

        if not adjustments:
            st.info("没有检测到分数变化。")
            return

        result = ManualReviewService(DBManager(DB_PATH), ANNOTATED_DIR).apply_batch_score_adjustments(
            session_id,
            adjustments,
        )
        _clear_report_export_cache(session_id)
        _mark_report_score_inputs_for_reset(session_id)
        st.session_state[f"report_score_review_flash_{session_id}"] = (
            f"已更新 {result['updated_details']} 项评分，并重算 {result['updated_results']} 名学生总分。"
        )
        st.rerun()
    except Exception as exc:  # noqa: BLE001
        st.error(f"评分调整保存失败：{exc}")


def _render_report_score_cards(session_id: int, rows: list[dict[str, Any]]) -> None:
    for row_start in range(0, len(rows), 3):
        cols = st.columns(3)
        for col, item in zip(cols, rows[row_start:row_start + 3]):
            with col:
                current_score = float(item.get("score_awarded") or 0)
                max_score = item.get("max_score")
                max_score_text = f"{float(max_score):g}" if max_score is not None else "未知"
                st.markdown(
                    f"**[{item.get('student_code')}] {item.get('student_name')}**  \n"
                    f"{item.get('question_id')}"
                )
                score_col, max_col = st.columns([2, 1])
                with score_col:
                    st.text_input(
                        "得分",
                        value=f"{current_score:g}",
                        key=_report_score_input_key(session_id, item),
                        label_visibility="collapsed",
                    )
                with max_col:
                    st.markdown(f"/ {max_score_text}")

                reason = str(item.get("deduction_reason") or "").strip()
                if reason:
                    st.caption(_short_text(reason, 90))
                crop = _build_answer_region_crop_preview(DBManager(DB_PATH), item)
                if crop is not None:
                    st.image(crop, use_container_width=True)
                else:
                    st.info("未找到该题框映射，无法裁剪。")


def _report_row_is_full_score(item: dict[str, Any]) -> bool:
    max_score = item.get("max_score")
    if max_score is None:
        return False
    return float(item.get("score_awarded") or 0) >= float(max_score) - 1e-6


def _report_persisted_score(item: dict[str, Any], displayed_score: float) -> float:
    if not item.get("is_inferred_full_score"):
        return float(displayed_score)
    source_score = float(item.get("source_score_awarded") or 0)
    displayed_current = float(item.get("score_awarded") or 0)
    return source_score - (displayed_current - float(displayed_score))


def _report_score_input_key(session_id: int, item: dict[str, Any]) -> str:
    question_id = str(item.get("question_id") or "").strip()
    return f"report_score_input_{session_id}_{int(item['result_id'])}_{int(item['detail_id'])}_{question_id}"


def _mark_report_score_inputs_for_reset(session_id: int) -> None:
    st.session_state[f"report_score_inputs_reset_{session_id}"] = True


def _reset_report_score_input_state(session_id: int) -> None:
    marker_key = f"report_score_inputs_reset_{session_id}"
    if not st.session_state.pop(marker_key, False):
        return
    prefix = f"report_score_input_{session_id}_"
    for key in list(st.session_state.keys()):
        if str(key).startswith(prefix):
            st.session_state.pop(key, None)


def _report_export_cache_key(session_id: int, export_type: str) -> str:
    return f"report_export_cache_{session_id}_{export_type}"


def _clear_report_export_cache(session_id: int) -> None:
    for export_type in ("excel", "pdf"):
        st.session_state.pop(_report_export_cache_key(session_id, export_type), None)


def _report_score_revision(session_id: int) -> str:
    db = DBManager(DB_PATH)
    rows: list[list[Any]] = []
    for result in db.get_session_results(session_id):
        result_id = int(result["result_id"])
        rows.append(["result", result_id, float(result.get("student_score") or 0)])
        for detail in db.get_result_details(result_id):
            rows.append(
                [
                    "detail",
                    int(detail["detail_id"]),
                    str(detail.get("question_id") or ""),
                    float(detail.get("score_awarded") or 0),
                ]
            )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _cached_report_export_path(session_id: int, export_type: str, score_revision: str) -> Path | None:
    cache_key = _report_export_cache_key(session_id, export_type)
    cached = st.session_state.get(cache_key)
    if not isinstance(cached, dict) or cached.get("revision") != score_revision:
        st.session_state.pop(cache_key, None)
        return None
    path_value = cached.get("path")
    path = Path(str(path_value)) if path_value else None
    if path is None or not path.exists():
        st.session_state.pop(cache_key, None)
        return None
    return path


def _render_report_export_controls(session_id: int) -> None:
    st.divider()
    st.markdown("### 导出最新成绩")
    st.caption("生成文件时会重新读取当前已保存成绩。评分调整后，旧的下载缓存会自动清除。")
    score_revision = _report_score_revision(session_id)
    excel_col, pdf_col = st.columns(2)

    with excel_col:
        if st.button("生成 Excel 成绩报表", type="primary", use_container_width=True, key=f"generate_excel_{session_id}"):
            try:
                output_path = ReportGenerator(db_path=DB_PATH, reports_dir=APP_DATA_DIR / "reports").export_session(session_id)
                st.session_state[_report_export_cache_key(session_id, "excel")] = {
                    "path": str(output_path),
                    "revision": score_revision,
                }
                st.success("已生成最新 Excel 成绩报表。")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Excel 报表生成失败：{exc}")

        excel_path = _cached_report_export_path(session_id, "excel", score_revision)
        if excel_path is not None:
            st.download_button(
                "下载 Excel 成绩报表",
                data=excel_path.read_bytes(),
                file_name=excel_path.name,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
                key=f"download_excel_{session_id}",
            )

    with pdf_col:
        if st.button("生成全体考生批注原卷 PDF", use_container_width=True, key=f"generate_pdf_{session_id}"):
            try:
                pdf_path = OriginalPaperExporter(DBManager(DB_PATH), APP_DATA_DIR / "reports").export_session_originals(session_id)
                st.session_state[_report_export_cache_key(session_id, "pdf")] = {
                    "path": str(pdf_path),
                    "revision": score_revision,
                }
                st.success("已生成最新批注原卷 PDF。")
            except Exception as exc:  # noqa: BLE001
                st.error(f"批注原卷 PDF 生成失败：{exc}")

        pdf_path = _cached_report_export_path(session_id, "pdf", score_revision)
        if pdf_path is not None:
            st.download_button(
                "下载批注原卷 PDF",
                data=pdf_path.read_bytes(),
                file_name=pdf_path.name,
                mime="application/pdf",
                use_container_width=True,
                key=f"download_pdf_{session_id}",
            )


def _render_selectable_question_matrix(display_df: pd.DataFrame, session_id: int) -> int | None:
    table_key = f"question_analysis_matrix_{session_id}"
    column_config = {
        "班级得分率": st.column_config.NumberColumn("班级得分率", format="%.1f%%"),
        "平均得分": st.column_config.NumberColumn("平均得分", format="%.2f"),
        "满分": st.column_config.NumberColumn("满分", format="%g"),
        "失分人数": st.column_config.NumberColumn("失分人数", format="%d"),
    }
    try:
        event = st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            column_config=column_config,
            on_select="rerun",
            selection_mode="single-row",
            key=table_key,
        )
        rows = getattr(event, "selection", {}).get("rows", []) if event is not None else []
        if rows:
            return int(rows[0])
    except TypeError:
        st.dataframe(display_df, use_container_width=True, hide_index=True, column_config=column_config)

    options = list(range(len(display_df)))
    if not options:
        return None
    return int(
        st.selectbox(
            "选择要查看作答框的题目",
            options=options,
            format_func=lambda idx: f"{display_df.iloc[idx]['班级']} · {display_df.iloc[idx]['题号']} · {display_df.iloc[idx]['失分人数']}人失分",
            key=f"question_analysis_select_{session_id}",
        )
    )


def _merge_question_analysis_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        qid = str(row.get("题号") or "").strip()
        if not qid:
            continue
        bucket = merged.setdefault(
            qid,
            {
                "班级": "全部班级",
                "题号": qid,
                "满分": float(row.get("满分") or 0),
                "_score_sum": 0.0,
                "_full_sum": 0.0,
                "_attempt_count": 0,
                "_wrong_items": [],
                "_correct_items": [],
            },
        )
        bucket["满分"] = max(float(bucket.get("满分") or 0), float(row.get("满分") or 0))
        bucket["_score_sum"] += float(row.get("_score_sum") or 0)
        bucket["_full_sum"] += float(row.get("_full_sum") or 0)
        bucket["_attempt_count"] += int(row.get("_attempt_count") or 0)
        bucket["_wrong_items"].extend(row.get("_wrong_items") or [])
        bucket["_correct_items"].extend(row.get("_correct_items") or [])

    merged_rows: list[dict[str, Any]] = []
    for bucket in merged.values():
        wrong_items = sorted(
            bucket["_wrong_items"],
            key=lambda item: (
                str(item.get("class_name") or ""),
                -float(item.get("deduction_amount") or 0),
                str(item.get("student_name") or ""),
            ),
        )
        wrong_text = "、".join(
            f"{item.get('student_name')}(-{float(item.get('deduction_amount') or 0):g})"
            for item in wrong_items
        )
        full_sum = float(bucket.get("_full_sum") or 0)
        score_sum = float(bucket.get("_score_sum") or 0)
        attempt_count = max(1, int(bucket.get("_attempt_count") or 0))
        correct_items = sorted(
            bucket.get("_correct_items", []),
            key=lambda item: (
                str(item.get("class_name") or ""),
                str(item.get("student_name") or ""),
            ),
        )
        merged_rows.append(
            {
                "班级": "全部班级",
                "题号": bucket["题号"],
                "满分": float(bucket["满分"]),
                "班级得分率": round(score_sum / full_sum * 100, 2) if full_sum > 0 else 0.0,
                "平均得分": round(score_sum / attempt_count, 2),
                "失分人数": len(wrong_items),
                "失分学生与扣分": wrong_text or "无",
                "_wrong_items": wrong_items,
                "_correct_items": correct_items,
                "_score_sum": score_sum,
                "_full_sum": full_sum,
                "_attempt_count": attempt_count,
            }
        )
    return sorted(
        merged_rows,
        key=lambda item: (float(item.get("班级得分率") or 0), _question_sort_key(str(item.get("题号") or ""))),
    )


def _build_session_question_analysis(db: DBManager, session_id: int) -> dict[str, Any]:
    session = db.get_grading_session(session_id)
    score_map, _type_map = _load_session_score_type_maps(session)
    results = db.get_session_results(session_id)
    buckets: dict[tuple[str, str], dict[str, Any]] = {}

    for result in results:
        class_name = str(result.get("class_name") or "未分班")
        result_id = int(result.get("result_id") or 0)
        details = db.get_result_details(result_id)
        for detail in _normalize_question_analysis_details(details, score_map):
            qid = str(detail.get("question_id") or "").strip()
            if not qid:
                continue
            max_score = float(score_map.get(qid) or 0)
            awarded = float(detail.get("score_awarded") or 0)
            if max_score <= 0:
                max_score = max(awarded, 0.0)
            elif awarded > max_score:
                awarded = max_score
            key = (class_name, qid)
            bucket = buckets.setdefault(
                key,
                {
                    "班级": class_name,
                    "题号": qid,
                    "满分": max_score,
                    "score_sum": 0.0,
                    "full_sum": 0.0,
                    "attempt_count": 0,
                    "wrong_items": [],
                    "correct_items": [],
                },
            )
            bucket["score_sum"] += awarded
            bucket["full_sum"] += max_score
            bucket["attempt_count"] += 1
            bucket["满分"] = max(bucket["满分"], max_score)
            deduction = max_score - awarded
            reason = str(detail.get("deduction_reason") or "").strip()
            if deduction > 0.01:
                bucket["wrong_items"].append(
                    {
                        **result,
                        **detail,
                        "session_id": session_id,
                        "max_score": max_score,
                        "deduction_amount": round(deduction, 2),
                        "deduction_reason": reason,
                    }
                )
            else:
                bucket["correct_items"].append(
                    {
                        **result,
                        **detail,
                        "session_id": session_id,
                        "max_score": max_score,
                    }
                )

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        full_sum = float(bucket.get("full_sum") or 0)
        score_sum = float(bucket.get("score_sum") or 0)
        wrong_items = sorted(
            bucket["wrong_items"],
            key=lambda item: (-float(item.get("deduction_amount") or 0), str(item.get("student_name") or "")),
        )
        wrong_text = "；".join(
            f"{item.get('student_name')}(-{float(item.get('deduction_amount') or 0):g})"
            for item in wrong_items
        )
        correct_items = sorted(
            bucket.get("correct_items", []),
            key=lambda item: str(item.get("student_name") or "")
        )
        rows.append(
            {
                "班级": bucket["班级"],
                "题号": bucket["题号"],
                "满分": float(bucket["满分"]),
                "班级得分率": round(score_sum / full_sum * 100, 2) if full_sum > 0 else 0.0,
                "平均得分": round(score_sum / max(1, int(bucket.get("attempt_count") or 0)), 2),
                "失分人数": len(wrong_items),
                "失分学生与扣分": wrong_text or "无",
                "_wrong_items": wrong_items,
                "_correct_items": correct_items,
                "_score_sum": score_sum,
                "_full_sum": full_sum,
                "_attempt_count": int(bucket.get("attempt_count") or 0),
            }
        )
    rows.sort(key=lambda item: (str(item["班级"]), _question_sort_key(str(item["题号"]))))
    classes = sorted({str(row["班级"]) for row in rows})
    return {"rows": rows, "classes": classes}


def _normalize_question_analysis_details(
    details: list[dict[str, Any]],
    score_map: dict[str, float],
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for detail in details:
        raw_qid = str(detail.get("question_id") or "").strip()
        if not raw_qid:
            continue
        qid = _canonical_question_id_for_score(raw_qid, score_map)
        if qid not in merged:
            item = dict(detail)
            item["question_id"] = qid
            item["score_awarded"] = 0.0
            item["_source_question_ids"] = []
            item["_deduction_reasons"] = []
            merged[qid] = item
            order.append(qid)

        item = merged[qid]
        item["score_awarded"] = float(item.get("score_awarded") or 0) + float(detail.get("score_awarded") or 0)
        item["_source_question_ids"].append(raw_qid)
        reason = str(detail.get("deduction_reason") or "").strip()
        if reason:
            item["_deduction_reasons"].append(reason)

    normalized: list[dict[str, Any]] = []
    for qid in order:
        item = merged[qid]
        full_score = float(score_map.get(qid) or 0)
        if full_score > 0 and float(item.get("score_awarded") or 0) > full_score:
            item["score_awarded"] = full_score
        reasons = []
        seen: set[str] = set()
        for reason in item.pop("_deduction_reasons", []):
            if reason not in seen:
                seen.add(reason)
                reasons.append(reason)
        if reasons:
            item["deduction_reason"] = "；".join(reasons)
        normalized.append(item)
    return normalized


def _canonical_question_id_for_score(question_id: str, score_map: dict[str, float]) -> str:
    qid = question_id.strip()
    if qid in score_map:
        return qid
    parent_id = _question_parent_id(qid)
    if parent_id and parent_id in score_map:
        return parent_id
    return qid


def _filter_parent_rows_when_parts_present(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    part_parent_ids = {
        parent_id
        for row in rows
        for parent_id in [_question_parent_id(str(row.get("题号") or ""))]
        if parent_id
    }
    if not part_parent_ids:
        return rows
    return [
        row for row in rows
        if str(row.get("题号") or "") not in part_parent_ids
    ]


def _question_parent_id(question_id: str) -> str | None:
    import re

    match = re.match(r"^(Q\d+)(?:\(|（|-)", question_id.strip())
    return match.group(1) if match else None


def _question_sort_key(question_id: str) -> tuple[int, str]:
    import re

    match = re.search(r"\d+", question_id)
    return (int(match.group()) if match else 9999, question_id)


def _build_answer_region_crop_preview(db: DBManager, item: dict[str, Any]) -> Image.Image | None:
    import re
    session_id = int(item.get("session_id") or 0)
    qid = str(item.get("question_id") or "")
    regions = answer_regions_with_template_source_sizes(db, session_id, data_root=APP_DATA_DIR)
    matched_regions = [
        region for region in regions
        if str(region.get("mapped_question_id") or region.get("detected_question_id") or "") in (qid, f"Q{qid}")
    ]
    if not matched_regions:
        m = re.match(r"^(?:Q)?(\d+)", qid)
        if m:
            base_num = m.group(1)
            matched_regions = [
                region for region in regions
                if str(region.get("mapped_question_id") or region.get("detected_question_id") or "") in (base_num, f"Q{base_num}")
            ]
    if not matched_regions:
        return None
    region = matched_regions[0]
    page = str(region.get("page") or "front")
    image_path = _resolve_session_file_path(item.get("front_image") if page == "front" else item.get("back_image"))
    if not image_path.exists():
        return None

    image = Image.open(image_path).convert("RGB")
    x, y, region_right, region_bottom = scaled_region_bbox(region, image.width, image.height)
    pad = max(18, min(image.width, image.height) // 70)
    left = max(0, x - pad)
    top = max(0, y - pad)
    right = min(image.width, region_right + pad)
    bottom = min(image.height, region_bottom + pad)
    crop = image.crop((left, top, right, bottom))
    draw = ImageDraw.Draw(crop)
    line_width = max(3, crop.width // 220)
    draw.rectangle(
        [
            x - left,
            y - top,
            min(crop.width - 1, region_right - left),
            min(crop.height - 1, region_bottom - top),
        ],
        outline=(220, 38, 38),
        width=line_width,
    )
    return crop


def _answer_text_from_node(node: Any) -> str:
    if not isinstance(node, dict):
        return ""
    return str(
        node.get("answer")
        or node.get("canonical_answer")
        or node.get("standard_answer")
        or node.get("correct_answer")
        or ""
    )


def _display_scoring_unit(qid: str, part_id: str, part_index: int, part_count: int) -> str:
    if part_count <= 1:
        return qid
    suffix = re.search(r"[\(（]([^)）]+)[\)）]$", part_id)
    if suffix:
        return f"第({suffix.group(1)})问"
    return f"第({part_index})问"


def _display_scoring_point(step: dict[str, Any], step_index: int) -> str:
    goal = str(
        step.get("core_goal")
        or step.get("goal")
        or step.get("criterion")
        or step.get("description")
        or ""
    ).strip()
    if goal:
        return goal
    required = step.get("required_elements")
    if isinstance(required, list) and required:
        return str(required[0])
    return f"踩分点{step_index}"


def _part_knowledge_display_label(part: dict[str, Any], fallback: str) -> str:
    raw_points = part.get("knowledge_points")
    labels: list[str] = []
    if isinstance(raw_points, str) and raw_points.strip():
        labels.append(raw_points.strip())
    elif isinstance(raw_points, list):
        for point in raw_points:
            if isinstance(point, dict):
                label = str(point.get("knowledge_name") or point.get("name") or point.get("label") or "").strip()
            else:
                label = str(point or "").strip()
            if label:
                labels.append(label)
    if labels:
        return "；".join(dict.fromkeys(labels))
    part_name = str(part.get("knowledge_name") or "").strip()
    return part_name or fallback


def _answer_match_rule_text(answer_node: dict[str, Any], fallback_node: dict[str, Any] | None = None) -> str:
    source = answer_node if isinstance(answer_node, dict) else {}
    fallback = fallback_node if isinstance(fallback_node, dict) else {}
    match_mode = str(source.get("match_mode") or fallback.get("match_mode") or "").strip()
    if match_mode != "complete_set":
        return ""
    required_values = source.get("required_values")
    if not isinstance(required_values, list) or not required_values:
        required_values = fallback.get("required_values")
    values = [str(value).strip() for value in required_values or [] if str(value).strip()]
    if not values:
        return "必须填写全部正确答案；顺序不限；少写、错写或多写均不得分"
    return f"必须全部填写：{'、'.join(values)}；顺序不限；少写、错写或多写均不得分"


def build_unified_rubric_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rubric = payload.get("rubric", {}) if isinstance(payload, dict) else {}
    answer_key = payload.get("answer_key", {}) if isinstance(payload, dict) else {}
    rubric_questions = rubric.get("questions", []) if isinstance(rubric, dict) else []
    answer_questions = answer_key.get("questions", []) if isinstance(answer_key, dict) else []
    answer_map = {str(q.get("question_id")): q for q in answer_questions if isinstance(q, dict)}
    rows: list[dict[str, Any]] = []

    for question in rubric_questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id", ""))
        qtype = str(question.get("question_type", ""))
        knowledge_label = _knowledge_display_label(
            str(question.get("knowledge_id") or ""),
            str(question.get("knowledge_name") or "")
        )

        ans_q = answer_map.get(qid, {})
        parts = question.get("parts", []) if isinstance(question.get("parts"), list) else []
        ans_parts = ans_q.get("parts", []) if ans_q and isinstance(ans_q.get("parts"), list) else []
        ans_part_map = {str(p.get("part_id")): p for p in ans_parts if isinstance(p, dict)}

        q_require_final = question.get("require_final_answer")
        if q_require_final is None:
            q_require_final = qtype == "comprehensive"

        max_score = _to_float(question.get("max_score"), 0.0)
        q_default_answer_only = max(1, int(round(max_score * 0.25))) if max_score > 0 else 1
        q_answer_only_max = int(round(_to_float(question.get("answer_only_max_score"), q_default_answer_only)))
        q_final_rule = _final_answer_rule_text(question) or "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分"

        if not parts:
            accepted_forms = ans_q.get("accepted_forms", []) if isinstance(ans_q, dict) else []
            eq_text = "；".join(str(item) for item in accepted_forms)
            canonical = _answer_text_from_node(ans_q)

            rows.append({
                "_question_id": qid,
                "_part_id": "整题",
                "_step_id": "整题",
                "题号": qid,
                "评分单元": "整题",
                "评分点": "整题",
                "题型": qtype,
                "分值": max_score,
                "标准答案": canonical,
                "等价答案预案": eq_text,
                "作答匹配规则": _answer_match_rule_text(ans_q),
                "证据要求/关键步骤": "",
                "扣分规则": "",
                "知识点": knowledge_label,
                "需要单独写答": bool(q_require_final) if qtype in {"proof", "calculation", "comprehensive"} else None,
                "无过程结论分上限": q_answer_only_max if qtype in {"proof", "calculation", "comprehensive"} else None,
                "未写答扣分说明": q_final_rule if qtype in {"proof", "calculation", "comprehensive"} else "",
            })
        else:
            for part_index, part in enumerate(parts, start=1):
                if not isinstance(part, dict):
                    continue
                part_id = str(part.get("part_id") or f"{qid}({part_index})")
                ans_p = ans_part_map.get(part_id, {})
                if not ans_p and part_index <= len(ans_parts) and isinstance(ans_parts[part_index - 1], dict):
                    ans_p = ans_parts[part_index - 1]
                part_canonical = _answer_text_from_node(ans_p)
                if not part_canonical and len(parts) == 1:
                    part_canonical = _answer_text_from_node(ans_q)
                part_accepted = ans_p.get("accepted_forms", []) if isinstance(ans_p, dict) else []
                part_eq_text = "；".join(str(item) for item in part_accepted)
                display_unit = _display_scoring_unit(qid, part_id, part_index, len(parts))
                part_knowledge_label = _part_knowledge_display_label(part, knowledge_label)

                part_score = _to_float(part.get("part_score") or part.get("max_score"), 0.0)
                part_default_answer_only = max(1, int(round(part_score * 0.25))) if part_score > 0 else 1

                if "require_final_answer" in part:
                    part_require_final = bool(part.get("require_final_answer"))
                else:
                    part_require_final = bool(q_require_final)

                if "answer_only_max_score" in part:
                    part_answer_only_max = int(round(_to_float(part.get("answer_only_max_score"), part_default_answer_only)))
                else:
                    part_answer_only_max = int(round(_to_float(question.get("answer_only_max_score"), part_default_answer_only)))

                part_final_rule = ""
                rules = part.get("presentation_rules", [])
                if isinstance(rules, list):
                    for r in rules:
                        if isinstance(r, dict) and r.get("rule_id") == "final_answer_required":
                            part_final_rule = str(r.get("rule") or "")
                            break
                if not part_final_rule:
                    part_final_rule = q_final_rule

                steps = part.get("steps", []) if isinstance(part.get("steps"), list) else []

                if not steps:
                    rows.append({
                        "_question_id": qid,
                        "_part_id": part_id,
                        "_step_id": "未拆评分点",
                        "题号": qid,
                        "评分单元": display_unit,
                        "评分点": "未拆评分点",
                        "分值": part_score,
                        "题型": qtype,
                        "标准答案": part_canonical,
                        "等价答案预案": part_eq_text,
                        "作答匹配规则": _answer_match_rule_text(ans_p, ans_q),
                        "证据要求/关键步骤": "",
                        "扣分规则": "",
                        "知识点": part_knowledge_label,
                        "需要单独写答": part_require_final if qtype in {"proof", "calculation", "comprehensive"} else None,
                        "无过程结论分上限": part_answer_only_max if qtype in {"proof", "calculation", "comprehensive"} else None,
                        "未写答扣分说明": part_final_rule if qtype in {"proof", "calculation", "comprehensive"} else "",
                    })
                else:
                    for step_index, step in enumerate(steps, start=1):
                        if not isinstance(step, dict):
                            continue
                        step_id = str(step.get("step_id") or step.get("id") or f"{step_index}")
                        step_score = _to_float(step.get("step_score") or step.get("score") or step.get("point_score") or step.get("max_score"), 0.0)

                        req_elements = step.get("required_elements", [])
                        req_text = "; ".join(str(e) for e in req_elements) if isinstance(req_elements, list) else str(req_elements or "")

                        ded_rules = step.get("deduction_rules", [])
                        ded_text = "; ".join(str(d) for d in ded_rules) if isinstance(ded_rules, list) else str(ded_rules or "")

                        is_first = (step_index == 1)
                        display_point = _display_scoring_point(step, step_index)

                        rows.append({
                            "_question_id": qid,
                            "_part_id": part_id,
                            "_step_id": step_id,
                            "题号": qid,
                            "评分单元": display_unit,
                            "评分点": display_point,
                            "题型": qtype,
                            "分值": step_score,
                            "标准答案": part_canonical if is_first else "",
                            "等价答案预案": part_eq_text if is_first else "",
                            "作答匹配规则": _answer_match_rule_text(ans_p, ans_q) if is_first else "",
                            "证据要求/关键步骤": req_text,
                            "扣分规则": ded_text,
                            "知识点": part_knowledge_label if is_first else "",
                            "需要单独写答": (part_require_final if qtype in {"proof", "calculation", "comprehensive"} else None) if is_first else None,
                            "无过程结论分上限": (part_answer_only_max if qtype in {"proof", "calculation", "comprehensive"} else None) if is_first else None,
                            "未写答扣分说明": (part_final_rule if qtype in {"proof", "calculation", "comprehensive"} else "") if is_first else "",
                        })

    return rows


def _apply_unified_table_to_payload(payload: dict[str, Any], edited_df: pd.DataFrame) -> dict[str, Any]:
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    rubric = next_payload.setdefault("rubric", {})
    answer_key = next_payload.setdefault("answer_key", {})
    rubric_questions = rubric.setdefault("questions", [])
    answer_questions = answer_key.setdefault("questions", [])

    rubric_q_map = {str(q.get("question_id")): q for q in rubric_questions if isinstance(q, dict)}
    answer_q_map = {str(q.get("question_id")): q for q in answer_questions if isinstance(q, dict)}

    def resolve_part_id(qid: str, row: dict[str, Any]) -> str:
        hidden_id = str(row.get("_part_id") or "").strip()
        if hidden_id:
            return hidden_id
        display = str(row.get("小题/评分单元") or row.get("评分单元") or "").strip()
        if display in {"", "整题"}:
            return display
        question = rubric_q_map.get(qid)
        parts = question.get("parts") if isinstance(question, dict) else []
        if not isinstance(parts, list) or not parts:
            return display
        valid_parts = [part for part in parts if isinstance(part, dict)]
        if display == qid and len(valid_parts) == 1:
            return str(valid_parts[0].get("part_id") or display)
        match = re.fullmatch(r"第\((\d+)\)问", display)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(valid_parts):
                return str(valid_parts[index].get("part_id") or display)
        return display

    records = edited_df.to_dict(orient="records")
    grouped = {}
    for r in records:
        qid = str(r.get("_question_id") or r.get("题号") or "").strip()
        part_id = resolve_part_id(qid, r)
        grouped.setdefault((qid, part_id), []).append(r)

    for (qid, part_id), rows in grouped.items():
        question = rubric_q_map.get(qid)
        ans_q = answer_q_map.get(qid)

        if not question or not isinstance(question, dict):
            continue

        qtype = str(question.get("question_type") or "")
        first_row = rows[0]
        req_final = first_row.get("需要单独写答")
        if pd.isna(req_final) or req_final is None:
            req_final = False
        else:
            req_final = bool(req_final)

        ans_only_max = first_row.get("无过程结论分上限")
        if pd.isna(ans_only_max) or ans_only_max is None:
            ans_only_max = 0
        else:
            ans_only_max = int(round(float(ans_only_max)))

        final_rule = str(first_row.get("未写答扣分说明") or "").strip()
        canonical = str(first_row.get("标准答案") or "").strip()
        eq_forms = _unique_texts(_split_semicolon_text(first_row.get("等价答案预案")))

        parts = question.get("parts", []) if isinstance(question.get("parts"), list) else []
        ans_parts = ans_q.get("parts", []) if ans_q and isinstance(ans_q.get("parts"), list) else []

        if part_id in {"", "整题"}:
            question["require_final_answer"] = req_final
            question["answer_only_max_score"] = ans_only_max
            question["_manual_solution_rules"] = True
            question["answer_presentation_policy"] = {
                "require_final_answer": req_final,
                "answer_only_max_score": ans_only_max,
                "note": "教师在界面中手动确认的过程/写答规则。",
            }
            policies = question.setdefault("deduction_policy", [])
            _upsert_dict_by_id(policies, "policy_id", {
                "policy_id": "answer_only_process_missing",
                "issue": f"只写最终答案但没有有效过程，最多给 {ans_only_max} 分，主要过程分不得给分",
                "max_deduction": max(0, int(round(_to_float(question.get("max_score"), 0.0))) - ans_only_max),
                "severity": "major",
            })

            if ans_q and isinstance(ans_q, dict):
                ans_q["canonical_answer"] = canonical
                ans_q["accepted_forms"] = eq_forms
                ans_q["_manual_accepted_forms"] = True

            step_score = _to_float(first_row.get("分值"), 0.0)
            question["max_score"] = step_score
            if ans_q and isinstance(ans_q, dict):
                ans_q["max_score"] = step_score
        else:
            part_node = None
            for p in parts:
                if isinstance(p, dict) and str(p.get("part_id")) == part_id:
                    part_node = p
                    break
            if part_node:
                part_node["require_final_answer"] = req_final
                part_node["answer_only_max_score"] = ans_only_max

                rules = part_node.setdefault("presentation_rules", [])
                rules[:] = [r for r in rules if isinstance(r, dict) and r.get("rule_id") != "final_answer_required"]
                if req_final:
                    rules.append({
                        "rule_id": "final_answer_required",
                        "rule": final_rule or "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分",
                        "max_deduction": 1,
                    })

                steps = part_node.get("steps", []) if isinstance(part_node.get("steps"), list) else []
                part_score_sum = 0.0
                step_rows = [
                    r for r in rows
                    if str(r.get("_step_id") or r.get("评分点") or "") != "未拆评分点"
                ]
                if step_rows:
                    step_map = {str(s.get("step_id") or s.get("id")): s for s in steps if isinstance(s, dict)}
                    new_steps = []
                    for s_row in step_rows:
                        step_id = str(s_row.get("_step_id") or "").strip()
                        if not step_id:
                            display_point = str(s_row.get("评分点") or "").strip()
                            for existing_index, existing_step in enumerate(steps, start=1):
                                if isinstance(existing_step, dict) and _display_scoring_point(existing_step, existing_index) == display_point:
                                    step_id = str(existing_step.get("step_id") or existing_step.get("id") or "")
                                    break
                        if not step_id:
                            step_id = str(s_row.get("评分点") or "")
                        step_node = step_map.get(step_id)
                        if not step_node:
                            step_node = {
                                "step_id": step_id,
                                "core_goal": "",
                                "required_elements": [],
                                "deduction_rules": []
                            }
                        s_score = _to_float(s_row.get("分值"), 0.0)
                        step_node["step_score"] = s_score
                        step_node["score"] = s_score
                        step_node["max_score"] = s_score
                        part_score_sum += s_score

                        req_text = str(s_row.get("证据要求/关键步骤") or "").strip()
                        step_node["required_elements"] = [x.strip() for x in req_text.split(";") if x.strip()] if req_text else []

                        ded_text = str(s_row.get("扣分规则") or "").strip()
                        step_node["deduction_rules"] = [x.strip() for x in ded_text.split(";") if x.strip()] if ded_text else []
                        new_steps.append(step_node)
                    part_node["steps"] = new_steps
                else:
                    part_score_sum = _to_float(first_row.get("分值"), 0.0)
                    req_text = str(first_row.get("证据要求/关键步骤") or "").strip()
                    part_node["required_elements"] = [x.strip() for x in req_text.split(";") if x.strip()] if req_text else []

                part_node["part_score"] = part_score_sum
                part_node["max_score"] = part_score_sum

            ans_part_node = None
            if ans_q and isinstance(ans_q, dict):
                for ap in ans_parts:
                    if isinstance(ap, dict) and str(ap.get("part_id")) == part_id:
                        ans_part_node = ap
                        break
                if ans_part_node:
                    ans_part_node["answer"] = canonical
                    ans_part_node["accepted_forms"] = eq_forms
                    ans_part_node["_manual_accepted_forms"] = True
                    ans_part_node["part_score"] = part_score_sum
                    ans_part_node["max_score"] = part_score_sum

    total_score_sum = 0.0
    for question in rubric_questions:
        if not isinstance(question, dict):
            continue
        parts = question.get("parts", [])
        if isinstance(parts, list) and parts:
            q_sum = 0.0
            for part in parts:
                if isinstance(part, dict):
                    q_sum += _to_float(part.get("part_score") or part.get("max_score"), 0.0)
            question["max_score"] = q_sum
        total_score_sum += _to_float(question.get("max_score"), 0.0)

        policies = question.get("deduction_policy", [])
        if isinstance(policies, list):
            for policy in policies:
                if isinstance(policy, dict) and policy.get("policy_id") == "answer_only_process_missing":
                    ans_only_max = int(round(_to_float(question.get("answer_only_max_score"), 0.0)))
                    policy["max_deduction"] = max(0, int(round(_to_float(question.get("max_score"), 0.0))) - ans_only_max)

    rubric["total_score"] = total_score_sum

    for ans_q in answer_questions:
        if not isinstance(ans_q, dict):
            continue
        qid = str(ans_q.get("question_id"))
        rub_q = rubric_q_map.get(qid)
        if rub_q:
            ans_q["max_score"] = rub_q.get("max_score", 0.0)
            rub_parts = rub_q.get("parts", [])
            ans_parts = ans_q.get("parts", [])
            if isinstance(rub_parts, list) and isinstance(ans_parts, list):
                rub_part_map = {str(p.get("part_id")): p for p in rub_parts if isinstance(p, dict)}
                for ans_p in ans_parts:
                    if isinstance(ans_p, dict):
                        pid = str(ans_p.get("part_id"))
                        rub_p = rub_part_map.get(pid)
                        if rub_p:
                            ans_p["part_score"] = rub_p.get("part_score", 0.0)
                            ans_p["max_score"] = rub_p.get("max_score", 0.0)

    return next_payload


def _render_generated_config_preview(payload: dict[str, Any], doc_name: str) -> bool:
    if not payload.get("_total_score_forced"):
        try:
            force_payload_total_score(payload, target_total=100.0)
            payload["_total_score_forced"] = True
        except Exception as exc:  # noqa: BLE001
            st.error(f"评分规则规范化失败：{exc}")
            return False

    st.markdown(f"**当前预览来源**：{doc_name}")
    refresh_quality_warnings = getattr(_session_manager, "refresh_generated_config_quality_warnings", None)
    if callable(refresh_quality_warnings):
        refresh_quality_warnings(payload)
    warnings = payload.get("meta", {}).get("warnings", [])
    if warnings:
        st.warning("AI 解析提醒：\n- " + "\n- ".join([str(w) for w in warnings]))

    st.markdown("### 统一评分标准编辑表")
    st.markdown(
        '<div style="font-size:0.85rem;color:#64748B;margin-bottom:10px;">'
        "提示：本表已合并总览、等价答案、解答题步骤及写答扣分规则。可以直接在此修改分值、标准答案、证据要求和写答规则。<br>"
        "<b>小问级规则</b>（需要单独写答、无过程结论分上限、未写答扣分说明）以及<b>标准答案/等价答案</b>，"
        "<b>仅在每个评分单元的第一行编辑有效</b>。修改后，请点击下方的『保存表格修改』按钮。"
        "</div>",
        unsafe_allow_html=True
    )

    df_from_payload = pd.DataFrame(build_unified_rubric_rows(payload))
    if df_from_payload.empty:
        st.info("无可用的评分标准。")
        return False

    edited_df = st.data_editor(
        df_from_payload,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "题号", "评分单元", "评分点", "题型", "分值", "标准答案", "等价答案预案",
            "作答匹配规则", "证据要求/关键步骤", "扣分规则", "知识点", "需要单独写答", "无过程结论分上限", "未写答扣分说明",
        ],
        disabled=["题号", "评分单元", "评分点", "题型", "作答匹配规则", "知识点"],
        column_config={
            "题号": st.column_config.TextColumn("题号", help="大题号"),
            "评分单元": st.column_config.TextColumn("评分单元", help="小问/评分单元ID"),
            "评分点": st.column_config.TextColumn("评分点", help="步骤ID/评分点序号"),
            "题型": st.column_config.TextColumn("题型", help="大题题型"),
            "分值": st.column_config.NumberColumn(
                "分值",
                min_value=0.0,
                step=0.5,
                format="%.1f",
                help="本步骤或评分单元的分值",
            ),
            "标准答案": st.column_config.TextColumn(
                "标准答案",
                help="标准答案。仅第一行编辑有效。",
            ),
            "等价答案预案": st.column_config.TextColumn(
                "等价答案预案",
                help="等价答案，多个用分号分隔。仅第一行编辑有效。",
            ),
            "证据要求/关键步骤": st.column_config.TextColumn(
                "证据要求/关键步骤",
                help="评分点对应的证据要求，多个用分号分隔。",
            ),
            "扣分规则": st.column_config.TextColumn(
                "扣分规则",
                help="评分点对应的扣分规则，多个用分号分隔。",
            ),
            "知识点": st.column_config.TextColumn("知识点", help="本题考查的知识点"),
            "需要单独写答": st.column_config.CheckboxColumn(
                "需要单独写答",
                help="是否需要单独写“答”（仅第一行编辑有效）。",
            ),
            "无过程结论分上限": st.column_config.NumberColumn(
                "无过程结论分上限",
                min_value=0,
                step=1,
                format="%d",
                help="缺失步骤仅有答案时本评分单元最多给几分（仅第一行编辑有效）。",
            ),
            "未写答扣分说明": st.column_config.TextColumn(
                "未写答扣分说明",
                help="未写答扣分规则描述（仅第一行编辑有效）。",
            ),
        },
        key=f"unified_rubric_editor_{doc_name}"
    )

    total_score = float(edited_df["分值"].sum())
    has_changes = not edited_df.equals(df_from_payload)

    if has_changes:
        if abs(total_score - 100.0) > 1e-4:
            st.error(f"⚠️ 当前表格总分为 {total_score:.1f} 分，必须调整至 100 分才能保存修改。")
            st.button("保存表格修改", key=f"save_unified_rubric_btn_{doc_name}", disabled=True)
        else:
            if st.button("保存表格修改", key=f"save_unified_rubric_btn_{doc_name}", type="primary"):
                try:
                    next_payload = _apply_unified_table_to_payload(payload, edited_df)
                    st.session_state.generated_config_payload = next_payload
                    st.success("已成功保存表格修改到内存预览！")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"保存表格修改失败：{exc}")
    else:
        if abs(total_score - 100.0) > 1e-4:
            st.error(f"⚠️ 当前整卷总分为 {total_score:.1f} 分，必须调整至 100 分。")

    health_warnings: list[str] = []
    warning_text = "\n".join(str(w) for w in warnings)
    objective_omission_patterns = [
        "选择题与填空题未包含",
        "选择题和填空题未包含",
        "选择题未包含",
        "填空题未包含",
        "仅解答题",
        "仅对解答题",
        "抽象化处理",
    ]
    if any(pattern in warning_text for pattern in objective_omission_patterns):
        health_warnings.append("AI 明确提示选择题/填空题未进入详细评分标准，不能直接保存")
    if any(token in warning_text for token in ["placeholder added", "unmergeable single-question schema"]):
        health_warnings.append("存在单题解析占位或不可合并结构，不能直接保存")
    health_warnings.extend(
        str(warning).replace("[质量检查-阻断] ", "", 1)
        for warning in warnings
        if str(warning).startswith("[质量检查-阻断]")
    )

    answer_key = payload.get("answer_key", {})
    answer_questions = answer_key.get("questions", []) if isinstance(answer_key, dict) else []
    answer_map = {str(q.get("question_id")): q for q in answer_questions if isinstance(q, dict)}

    qtypes = {str(row["题型"]) for row in build_unified_rubric_rows(payload)}
    if warnings and qtypes <= {"proof", "calculation", "comprehensive"} and any(
        token in warning_text for token in ["选择题", "填空题", "客观题"]
    ):
        health_warnings.append("Word 中疑似存在客观题，但当前总览没有 choice/fill_blank 题型")

    for row in build_unified_rubric_rows(payload):
        if str(row.get("评分点") or "") == "未拆评分点" and row["题型"] in {"proof", "calculation", "comprehensive"}:
            health_warnings.append(f"{row['题号']} 为 {row['题型']} 但未拆分步骤")
        if row["题型"] == "fill_blank" and str(row.get("评分点") or "") == "未拆评分点":
            canonical = str(answer_map.get(str(row["题号"]), {}).get("canonical_answer") or "").strip()
            if not canonical:
                health_warnings.append(f"{row['题号']} 为填空题但未提供标准答案")

    if health_warnings:
        st.error("结构检查提醒：\n- " + "\n- ".join(health_warnings))
        return False

    if abs(total_score - 100.0) > 1e-4:
        return False

    if has_changes:
        st.caption("⚠️ 检测到表格有未保存的修改，请先点击表格下方的『保存表格修改』。")
        return False

    st.success("结构检查通过：已具备步骤给分/等价答案所需核心字段，总分刚好为 100 分。")
    return True


def _render_active_session_rubric_editor(
    db: DBManager,
    session_id: int | None,
    rubric_path: Path,
    answer_key_path: Path,
    active_payload: dict[str, Any],
) -> None:
    editor_id = str(session_id) if session_id is not None else rubric_path.name
    state_key = f"active_session_payload_{editor_id}"
    if state_key not in st.session_state:
        st.session_state[state_key] = json.loads(json.dumps(active_payload, ensure_ascii=False))

    payload = st.session_state[state_key]
    df_from_payload = pd.DataFrame(build_unified_rubric_rows(payload))
    if df_from_payload.empty:
        st.info("无可用的评分标准。")
        return

    st.markdown(
        '<div style="font-size:0.85rem;color:#64748B;margin-bottom:10px;">'
        "提示：此处为当前考试正在使用的评分标准。修改分值、答案等规则并保存后，将<b>即时更新本地 JSON 评分依据并清除系统内存缓存</b>，后续对新提交的试卷将采用新标准进行批改。"
        "</div>",
        unsafe_allow_html=True
    )

    edited_df = st.data_editor(
        df_from_payload,
        use_container_width=True,
        hide_index=True,
        column_order=[
            "题号", "评分单元", "评分点", "题型", "分值", "标准答案", "等价答案预案",
            "作答匹配规则", "证据要求/关键步骤", "扣分规则", "知识点", "需要单独写答", "无过程结论分上限", "未写答扣分说明",
        ],
        disabled=["题号", "评分单元", "评分点", "题型", "作答匹配规则", "知识点"],
        column_config={
            "题号": st.column_config.TextColumn("题号", help="大题号"),
            "评分单元": st.column_config.TextColumn("评分单元", help="小问/评分单元ID"),
            "评分点": st.column_config.TextColumn("评分点", help="步骤ID/评分点序号"),
            "题型": st.column_config.TextColumn("题型", help="大题题型"),
            "分值": st.column_config.NumberColumn(
                "分值",
                min_value=0.0,
                step=0.5,
                format="%.1f",
                help="本步骤或评分单元的分值",
            ),
            "标准答案": st.column_config.TextColumn(
                "标准答案",
                help="标准答案。仅第一行编辑有效。",
            ),
            "等价答案预案": st.column_config.TextColumn(
                "等价答案预案",
                help="等价答案，多个用分号分隔。仅第一行编辑有效。",
            ),
            "作答匹配规则": st.column_config.TextColumn(
                "作答匹配规则",
                help="例如多答案填空必须写全、顺序不限、少写错写多写均不得分。",
            ),
            "作答匹配规则": st.column_config.TextColumn(
                "作答匹配规则",
                help="例如多答案填空必须写全、顺序不限、少写错写多写均不得分。",
            ),
            "证据要求/关键步骤": st.column_config.TextColumn(
                "证据要求/关键步骤",
                help="评分点对应的证据要求，多个用分号分隔。",
            ),
            "扣分规则": st.column_config.TextColumn(
                "扣分规则",
                help="评分点对应的扣分规则，多个用分号分隔。",
            ),
            "知识点": st.column_config.TextColumn("知识点", help="本题考查的知识点"),
            "需要单独写答": st.column_config.CheckboxColumn(
                "需要单独写答",
                help="是否需要单独写“答”（仅第一行编辑有效）。",
            ),
            "无过程结论分上限": st.column_config.NumberColumn(
                "无过程结论分上限",
                min_value=0,
                step=1,
                format="%d",
                help="缺失步骤仅有答案时本评分单元最多给几分（仅第一行编辑有效）。",
            ),
            "未写答扣分说明": st.column_config.TextColumn(
                "未写答扣分说明",
                help="未写答扣分规则描述（仅第一行编辑有效）。",
            ),
        },
        key=f"active_rubric_editor_{editor_id}"
    )

    total_score = float(edited_df["分值"].sum())
    has_changes = not edited_df.equals(df_from_payload)

    if has_changes:
        if abs(total_score - 100.0) > 1e-4:
            st.error(f"⚠️ 当前表格总分为 {total_score:.1f} 分，必须调整至 100 分才能保存修改。")
            st.button("确认修改评分标准", key=f"save_active_rubric_btn_{editor_id}", disabled=True)
        else:
            if st.button("确认修改评分标准", key=f"save_active_rubric_btn_{editor_id}", type="primary"):
                try:
                    next_payload = _apply_unified_table_to_payload(payload, edited_df)
                    _write_compact_json_file(rubric_path, next_payload["rubric"])
                    _write_compact_json_file(answer_key_path, next_payload["answer_key"])
                    if session_id is not None and hasattr(db, "_rubric_map_cache"):
                        db._rubric_map_cache.pop(session_id, None)
                    st.session_state[state_key] = next_payload
                    st.success("评分标准已成功更新并写入本地磁盘，缓存已清空，即时生效！")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"更新失败：{exc}")
    else:
        if abs(total_score - 100.0) > 1e-4:
            st.error(f"⚠️ 当前整卷总分为 {total_score:.1f} 分，必须调整至 100 分。")
def _render_manual_scoring_unit_tools(payload: dict[str, Any], llm_settings: LLMSettings | None) -> None:
    rubric_questions = payload.get("rubric", {}).get("questions", []) if isinstance(payload, dict) else []
    if not isinstance(rubric_questions, list) or not rubric_questions:
        return

    with st.expander("手动拆分评分单元（可选：一题多空 / 大题多得分点）", expanded=False):
        st.caption(
            "适合“一道大题包含多个填空/多个证明目标”的情况。先由老师固定评分单元，"
            "再让 AI 只在这个结构上补全答案、等价形式、证明义务和扣分规则。"
        )
        qid_options = [
            str(question.get("question_id") or "")
            for question in rubric_questions
            if isinstance(question, dict) and str(question.get("question_id") or "").strip()
        ]
        if not qid_options:
            st.info("当前没有可拆分题目。")
            return

        cols = st.columns([1.1, 0.75, 0.9])
        with cols[0]:
            selected_qid = st.selectbox("选择要拆分的题目", qid_options, key="manual_split_question_id")
        with cols[1]:
            split_count = int(
                st.number_input("拆成几个评分单元", min_value=2, max_value=30, value=4, step=1, key="manual_split_count")
            )
        with cols[2]:
            part_style = st.selectbox(
                "评分单元命名",
                ["Qx-B1（适合多空）", "Qx(1)（适合小问）"],
                key="manual_split_part_style",
            )

        target_question = _find_rubric_question(payload, selected_qid)
        if target_question:
            current_parts = target_question.get("parts", []) if isinstance(target_question.get("parts"), list) else []
            st.caption(
                f"当前 {selected_qid}：总分 {target_question.get('max_score', 0)}，"
                f"已有 {len(current_parts)} 个评分单元。"
            )
            if current_parts:
                manual_part_rows = []
                for idx, part in enumerate(current_parts, start=1):
                    if not isinstance(part, dict):
                        continue
                    steps = part.get("steps", []) if isinstance(part.get("steps"), list) else []
                    first_step = steps[0] if steps and isinstance(steps[0], dict) else {}
                    manual_part_rows.append(
                        {
                            "序号": idx,
                            "评分单元ID": str(part.get("part_id") or f"{selected_qid}({idx})"),
                            "分值": int(round(float(part.get("part_score") or 0))),
                            "核心目标": str(first_step.get("core_goal") or ""),
                        }
                    )
                edited_parts = st.data_editor(
                    pd.DataFrame(manual_part_rows),
                    use_container_width=True,
                    hide_index=True,
                    disabled=["序号"],
                    key=f"manual_part_editor_{selected_qid}",
                    column_config={
                        "分值": st.column_config.NumberColumn("分值", min_value=0, step=1, format="%d"),
                    },
                )
                if st.button("保存当前题评分单元编辑", key=f"manual_part_save_{selected_qid}", type="secondary"):
                    try:
                        next_payload = _apply_manual_part_rows(payload, selected_qid, edited_parts)
                        st.session_state.generated_config_payload = next_payload
                        st.success(f"已保存 {selected_qid} 的评分单元编辑。")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"保存评分单元失败：{exc}")

        action_cols = st.columns([1, 1.2])
        with action_cols[0]:
            if st.button("按当前题目拆分", key="manual_split_apply_btn", type="secondary"):
                try:
                    next_payload = _split_payload_question_parts(
                        payload,
                        selected_qid,
                        split_count,
                        part_style="blank" if part_style.startswith("Qx-B") else "subquestion",
                    )
                    st.session_state.generated_config_payload = next_payload
                    st.success(f"已把 {selected_qid} 拆成 {split_count} 个评分单元。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"拆分失败：{exc}")

        with action_cols[1]:
            if st.button("让 AI 基于当前拆分完善并重新赋分", key="manual_split_ai_refine_btn", type="primary"):
                try:
                    if llm_settings is None:
                        raise ValueError("请先在左侧配置 API Key/Base URL")

                    def refine_work(report) -> dict[str, Any]:
                        report(0.08, "读取人工拆分结构")
                        llm_client = LLMClient(llm_settings)
                        report(0.18, "请求 AI 按人工结构二次完善")
                        return refine_grading_config_from_manual_structure(
                            st.session_state.generated_config_payload or payload,
                            llm_client=llm_client,
                            model_name=llm_settings.config_model,
                        )

                    refined_payload = _run_with_stage_progress(
                        "AI 完善手动拆分",
                        refine_work,
                        done_text="已按人工结构重新生成评分标准",
                    )
                    st.session_state.generated_config_payload = refined_payload
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    refined_path = UPLOAD_CONFIG_DIR / f"generated_config_manual_refined_{ts}.json"
                    _write_compact_json_file(refined_path, refined_payload)
                    st.success(f"AI 已按人工拆分完善评分标准，本地调试文件：{refined_path.name}")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"AI 完善失败：{exc}")

        st.markdown("**当前评分单元预览**")
        unit_rows = _generated_config_part_rows(st.session_state.generated_config_payload or payload)
        if unit_rows:
            st.dataframe(pd.DataFrame(unit_rows), use_container_width=True, hide_index=True)
        else:
            st.caption("暂无评分单元。")


def _find_rubric_question(payload: dict[str, Any], question_id: str) -> dict[str, Any] | None:
    questions = payload.get("rubric", {}).get("questions", []) if isinstance(payload, dict) else []
    if not isinstance(questions, list):
        return None
    for question in questions:
        if isinstance(question, dict) and str(question.get("question_id") or "") == question_id:
            return question
    return None


def _find_answer_question(payload: dict[str, Any], question_id: str) -> dict[str, Any] | None:
    questions = payload.get("answer_key", {}).get("questions", []) if isinstance(payload, dict) else []
    if not isinstance(questions, list):
        return None
    for question in questions:
        if isinstance(question, dict) and str(question.get("question_id") or "") == question_id:
            return question
    return None


def _split_payload_question_parts(
    payload: dict[str, Any],
    question_id: str,
    split_count: int,
    *,
    part_style: str,
) -> dict[str, Any]:
    if split_count < 2:
        raise ValueError("拆分数量至少为 2")
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    rubric_question = _find_rubric_question(next_payload, question_id)
    if rubric_question is None:
        raise ValueError(f"未找到题目：{question_id}")
    answer_question = _find_answer_question(next_payload, question_id)
    if answer_question is None:
        answer_question = {"question_id": question_id, "canonical_answer": "", "accepted_forms": [], "method_variants": [], "parts": []}
        next_payload.setdefault("answer_key", {}).setdefault("questions", []).append(answer_question)

    total_score = int(round(float(rubric_question.get("max_score") or split_count)))
    if total_score <= 0:
        total_score = split_count
    part_scores = _integer_even_split(total_score, split_count)
    qtype = str(rubric_question.get("question_type") or "comprehensive")
    is_direct = qtype in {"choice", "fill_blank"} or part_style == "blank"

    rubric_parts: list[dict[str, Any]] = []
    answer_parts: list[dict[str, Any]] = []
    for idx, score in enumerate(part_scores, start=1):
        part_id = f"{question_id}-B{idx}" if part_style == "blank" else f"{question_id}({idx})"
        core_goal = f"完成 {question_id} 第 {idx} 个填空/评分单元"
        required = ["答案正确或与标准答案等价"] if is_direct else ["关键过程合理", "结论或证明目标成立"]
        rubric_parts.append(
            {
                "part_id": part_id,
                "part_score": score,
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": score,
                        "core_goal": core_goal,
                        "required_elements": required,
                        "allow_alternative_methods": not (qtype == "choice"),
                    }
                ],
                "presentation_rules": [],
            }
        )
        answer_parts.append(
            {
                "part_id": part_id,
                "answer": "",
                "analysis": "用户手动拆分的评分单元，请 AI 基于题干与参考答案补全。",
                "step_milestones": [],
            }
        )

    rubric_question["parts"] = rubric_parts
    if part_style == "blank" and qtype in {"comprehensive", "calculation", "proof"}:
        rubric_question["grading_mode"] = "direct_answer"
    answer_question["parts"] = answer_parts
    answer_question.setdefault("canonical_answer", "")
    answer_question.setdefault("accepted_forms", [])
    answer_question.setdefault("method_variants", [])

    meta = next_payload.setdefault("meta", {})
    warnings = meta.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.append(f"教师手动将 {question_id} 拆分为 {split_count} 个评分单元，AI 二次完善时必须保留 part_id。")
    return next_payload


def _apply_manual_part_rows(payload: dict[str, Any], question_id: str, edited_parts: pd.DataFrame) -> dict[str, Any]:
    rows = edited_parts.to_dict(orient="records")
    if not rows:
        raise ValueError("评分单元不能为空")
    part_ids = [str(row.get("评分单元ID") or "").strip() for row in rows]
    if any(not part_id for part_id in part_ids):
        raise ValueError("评分单元ID不能为空")
    if len(set(part_ids)) != len(part_ids):
        raise ValueError("评分单元ID不能重复")

    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    rubric_question = _find_rubric_question(next_payload, question_id)
    if rubric_question is None:
        raise ValueError(f"未找到题目：{question_id}")
    current_question_score = int(round(float(rubric_question.get("max_score") or 0)))
    edited_score_total = sum(int(round(float(row.get("分值") or 0))) for row in rows)
    if current_question_score > 0 and edited_score_total != current_question_score:
        raise ValueError(f"评分单元分值之和必须等于当前题总分 {current_question_score}，当前为 {edited_score_total}")
    answer_question = _find_answer_question(next_payload, question_id)
    if answer_question is None:
        answer_question = {"question_id": question_id, "canonical_answer": "", "accepted_forms": [], "method_variants": [], "parts": []}
        next_payload.setdefault("answer_key", {}).setdefault("questions", []).append(answer_question)

    old_rubric_parts = rubric_question.get("parts", []) if isinstance(rubric_question.get("parts"), list) else []
    old_answer_parts = answer_question.get("parts", []) if isinstance(answer_question.get("parts"), list) else []
    new_rubric_parts: list[dict[str, Any]] = []
    new_answer_parts: list[dict[str, Any]] = []

    for idx, row in enumerate(rows, start=1):
        part_id = str(row.get("评分单元ID") or "").strip()
        score = int(round(float(row.get("分值") or 0)))
        core_goal = str(row.get("核心目标") or f"完成 {question_id} 第 {idx} 个评分单元").strip()
        old_part = old_rubric_parts[idx - 1] if idx - 1 < len(old_rubric_parts) and isinstance(old_rubric_parts[idx - 1], dict) else {}
        old_answer = old_answer_parts[idx - 1] if idx - 1 < len(old_answer_parts) and isinstance(old_answer_parts[idx - 1], dict) else {}

        steps = old_part.get("steps") if isinstance(old_part.get("steps"), list) else []
        if steps and isinstance(steps[0], dict):
            steps = json.loads(json.dumps(steps, ensure_ascii=False))
            steps[0]["core_goal"] = core_goal
            steps[0]["step_score"] = score
            if len(steps) > 1:
                for extra_step in steps[1:]:
                    if isinstance(extra_step, dict):
                        extra_step["step_score"] = 0
        else:
            steps = [
                {
                    "step_id": "S1",
                    "step_score": score,
                    "core_goal": core_goal,
                    "required_elements": ["答案正确或过程目标完成"],
                    "allow_alternative_methods": True,
                }
            ]

        new_rubric_parts.append(
            {
                **old_part,
                "part_id": part_id,
                "part_score": score,
                "steps": steps,
                "presentation_rules": old_part.get("presentation_rules", []) if isinstance(old_part.get("presentation_rules"), list) else [],
            }
        )
        new_answer_parts.append(
            {
                **old_answer,
                "part_id": part_id,
                "answer": str(old_answer.get("answer") or ""),
                "analysis": str(old_answer.get("analysis") or "用户手动编辑的评分单元，请 AI 补全。"),
                "step_milestones": old_answer.get("step_milestones", []) if isinstance(old_answer.get("step_milestones"), list) else [],
            }
        )

    rubric_question["parts"] = new_rubric_parts
    rubric_question["max_score"] = current_question_score or sum(int(part.get("part_score") or 0) for part in new_rubric_parts)
    answer_question["parts"] = new_answer_parts
    meta = next_payload.setdefault("meta", {})
    warnings = meta.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.append(f"教师手动编辑了 {question_id} 的评分单元结构，AI 二次完善时必须保留这些 part_id。")
    return next_payload


def _integer_even_split(total: int, count: int) -> list[int]:
    base = int(total) // int(count)
    remainder = int(total) % int(count)
    return [base + (1 if idx < remainder else 0) for idx in range(count)]


def _generated_config_part_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rubric = payload.get("rubric", {}) if isinstance(payload, dict) else {}
    answer_key = payload.get("answer_key", {}) if isinstance(payload, dict) else {}
    rubric_questions = rubric.get("questions", []) if isinstance(rubric, dict) else []
    answer_questions = answer_key.get("questions", []) if isinstance(answer_key, dict) else []
    answer_map = {
        str(question.get("question_id") or ""): question
        for question in answer_questions
        if isinstance(question, dict)
    }
    rows: list[dict[str, Any]] = []
    if not isinstance(rubric_questions, list):
        return rows
    for question in rubric_questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "")
        answer_parts = {
            str(part.get("part_id") or ""): part
            for part in answer_map.get(qid, {}).get("parts", [])
            if isinstance(part, dict)
        }
        parts = question.get("parts", []) if isinstance(question.get("parts"), list) else []
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_id = str(part.get("part_id") or qid)
            steps = part.get("steps", []) if isinstance(part.get("steps"), list) else []
            answer_text = str(answer_parts.get(part_id, {}).get("answer") or "")
            accepted_forms = answer_parts.get(part_id, {}).get("accepted_forms") or []
            rows.append(
                {
                    "题号": qid,
                    "评分单元": part_id,
                    "分值": part.get("part_score", 0),
                    "步骤数": len(steps),
                    "答案/目标摘要": answer_text[:80],
                    "本地等价数": len(accepted_forms) if isinstance(accepted_forms, list) else 0,
                }
            )
    return rows


def _generated_equivalence_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    answer_key = payload.get("answer_key", {}) if isinstance(payload, dict) else {}
    questions = answer_key.get("questions", []) if isinstance(answer_key, dict) else []
    rows: list[dict[str, Any]] = []
    if not isinstance(questions, list):
        return rows
    for question in questions:
        if not isinstance(question, dict):
            continue
        qid = str(question.get("question_id") or "")
        accepted_forms = question.get("accepted_forms") if isinstance(question.get("accepted_forms"), list) else []
        parts = [p for p in question.get("parts", []) if isinstance(p, dict)]
        
        skip_question = False
        if len(parts) == 1:
            part_forms = parts[0].get("accepted_forms") if isinstance(parts[0].get("accepted_forms"), list) else []
            if accepted_forms and part_forms and set(str(x).strip() for x in accepted_forms) == set(str(x).strip() for x in part_forms):
                skip_question = True

        if accepted_forms and not skip_question:
            rows.append(
                {
                    "题号": qid,
                    "评分单元": "整题",
                    "标准答案": str(question.get("canonical_answer") or ""),
                    "等价答案预案": "；".join(str(item) for item in accepted_forms),
                }
            )
        for part in parts:
            part_forms = part.get("accepted_forms") if isinstance(part.get("accepted_forms"), list) else []
            if not part_forms:
                continue
            rows.append(
                {
                    "题号": qid,
                    "评分单元": str(part.get("part_id") or ""),
                    "标准答案": str(part.get("answer") or ""),
                    "等价答案预案": "；".join(str(item) for item in part_forms),
                }
            )
    return rows


def _generated_solution_rule_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rubric = payload.get("rubric", {}) if isinstance(payload, dict) else {}
    questions = rubric.get("questions", []) if isinstance(rubric, dict) else []
    rows: list[dict[str, Any]] = []
    if not isinstance(questions, list):
        return rows
    for question in questions:
        if not isinstance(question, dict):
            continue
        qtype = str(question.get("question_type") or "")
        if qtype not in {"proof", "calculation", "comprehensive"}:
            continue
        max_score = _to_float(question.get("max_score"), 0.0)
        default_answer_only = max(1, int(round(max_score * 0.25))) if max_score > 0 else 1
        require_final = question.get("require_final_answer")
        if require_final is None:
            require_final = qtype == "comprehensive"
        rows.append(
            {
                "题号": str(question.get("question_id") or ""),
                "题型": qtype,
                "总分": int(round(max_score)),
                "需要单独写答": bool(require_final),
                "只写答案最多得分": int(round(_to_float(question.get("answer_only_max_score"), default_answer_only))),
                "未写答扣分说明": _final_answer_rule_text(question)
                or "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分",
            }
        )
    return rows


def _apply_solution_rule_rows_to_payload(payload: dict[str, Any], edited: pd.DataFrame) -> dict[str, Any]:
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    questions = next_payload.get("rubric", {}).get("questions", []) if isinstance(next_payload, dict) else []
    if not isinstance(questions, list):
        raise ValueError("rubric.questions 不是有效列表")
    question_map = {
        str(question.get("question_id") or "").strip(): question
        for question in questions
        if isinstance(question, dict)
    }
    for row in edited.to_dict(orient="records"):
        qid = str(row.get("题号") or "").strip()
        question = question_map.get(qid)
        if not isinstance(question, dict):
            continue
        max_score = int(round(_to_float(question.get("max_score"), row.get("总分") or 0)))
        answer_only_max = int(round(_to_float(row.get("只写答案最多得分"), max(1, max_score * 0.25))))
        answer_only_max = max(0, min(answer_only_max, max_score))
        require_final = bool(row.get("需要单独写答"))
        final_rule = str(row.get("未写答扣分说明") or "").strip()
        _sync_solution_rule_to_question(
            question,
            require_final_answer=require_final,
            answer_only_max_score=answer_only_max,
            final_answer_rule=final_rule,
        )
    return next_payload


def _sync_solution_rule_to_question(
    question: dict[str, Any],
    *,
    require_final_answer: bool,
    answer_only_max_score: int,
    final_answer_rule: str,
) -> None:
    max_score = int(round(_to_float(question.get("max_score"), 0)))
    question["require_final_answer"] = bool(require_final_answer)
    question["_manual_solution_rules"] = True
    question["answer_only_max_score"] = max(0, min(int(answer_only_max_score), max_score if max_score > 0 else int(answer_only_max_score)))
    question["answer_presentation_policy"] = {
        "require_final_answer": question["require_final_answer"],
        "answer_only_max_score": question["answer_only_max_score"],
        "note": "教师在界面中手动确认的过程/写答规则。",
    }

    policies = question.get("deduction_policy")
    if not isinstance(policies, list):
        policies = []
        question["deduction_policy"] = policies
    _upsert_dict_by_id(
        policies,
        "policy_id",
        {
            "policy_id": "answer_only_process_missing",
            "issue": f"只写最终答案但没有有效过程，最多给 {question['answer_only_max_score']} 分，主要过程分不得给分",
            "max_deduction": max(0, max_score - question["answer_only_max_score"]),
            "severity": "major",
        },
    )
    _upsert_dict_by_id(
        policies,
        "policy_id",
        {
            "policy_id": "core_process_missing",
            "issue": "关键过程、证明义务或推理链缺失，应扣除对应过程分",
            "max_deduction": max_score,
            "severity": "fatal",
        },
    )

    parts = question.get("parts")
    if not isinstance(parts, list):
        return
    for part in parts:
        if not isinstance(part, dict):
            continue
        rules = part.get("presentation_rules")
        if not isinstance(rules, list):
            rules = []
            part["presentation_rules"] = rules
        rules[:] = [
            rule
            for rule in rules
            if not (isinstance(rule, dict) and str(rule.get("rule_id") or "") == "final_answer_required")
        ]
        if require_final_answer:
            rules.append(
                {
                    "rule_id": "final_answer_required",
                    "rule": final_answer_rule or "一般解答题需要写出最终答或明确结论；未写答/结论不完整，酌情扣1分",
                    "max_deduction": 1,
                }
            )


def _final_answer_rule_text(question: dict[str, Any]) -> str:
    parts = question.get("parts")
    if not isinstance(parts, list):
        return ""
    for part in parts:
        if not isinstance(part, dict):
            continue
        rules = part.get("presentation_rules")
        if not isinstance(rules, list):
            continue
        for rule in rules:
            if isinstance(rule, dict) and str(rule.get("rule_id") or "") == "final_answer_required":
                return str(rule.get("rule") or "")
    return ""


def _upsert_dict_by_id(items: list[Any], id_key: str, new_item: dict[str, Any]) -> None:
    new_id = str(new_item.get(id_key) or "")
    for idx, item in enumerate(items):
        if isinstance(item, dict) and str(item.get(id_key) or "") == new_id:
            items[idx] = {**item, **new_item}
            return
    items.append(new_item)


def _to_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _apply_equivalence_rows_to_payload(payload: dict[str, Any], edited: pd.DataFrame) -> dict[str, Any]:
    next_payload = json.loads(json.dumps(payload, ensure_ascii=False))
    answer_key = next_payload.get("answer_key", {}) if isinstance(next_payload, dict) else {}
    questions = answer_key.get("questions", []) if isinstance(answer_key, dict) else []
    if not isinstance(questions, list):
        raise ValueError("answer_key.questions 不是有效列表")

    question_map = {
        str(question.get("question_id") or "").strip(): question
        for question in questions
        if isinstance(question, dict)
    }
    for row in edited.to_dict(orient="records"):
        qid = str(row.get("题号") or "").strip()
        unit = str(row.get("评分单元") or "").strip()
        forms = _unique_texts(_split_semicolon_text(row.get("等价答案预案")))
        question = question_map.get(qid)
        if not isinstance(question, dict):
            continue
        if unit in {"", "整题", qid}:
            question["accepted_forms"] = forms
            question["_manual_accepted_forms"] = True
            continue
        for part in question.get("parts", []):
            if isinstance(part, dict) and str(part.get("part_id") or "").strip() == unit:
                part["accepted_forms"] = forms
                part["_manual_accepted_forms"] = True
                break
    return next_payload


def _unique_texts(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _render_template_config_editor(db: DBManager, session_id: int, template: dict[str, Any]) -> None:
    config_path = Path(str(template.get("template_config_path") or ""))
    if not config_path.exists():
        st.info("尚未生成样卷解析配置。上传正反面样卷后，可在这里预览并批量调整题目分值与答案预案。")
        return

    config = _read_json_safely(config_path)
    questions = config.get("questions") if isinstance(config, dict) else []
    if not isinstance(questions, list) or not questions:
        st.warning("样卷映射包中没有题目候选。请确认当前考试已经绑定 Word 生成的评分标准。")
        return

    with st.container(border=True):
        st.markdown("### 样卷映射预览")
        st.caption(
            "样卷阶段只负责版面与题框映射，不再重新生成分值、答案、知识点或评分规则。"
            "这些内容统一来自 Word 试卷生成的评分标准。"
        )
        paths = {
            "映射来源": template.get("ai_analysis_path"),
            "题号候选": template.get("template_config_path"),
            "题框文件": template.get("regions_path"),
        }
        st.caption("本地调试文件：" + " · ".join([f"{k}: {Path(str(v)).name}" for k, v in paths.items() if v]))

        if _template_mapping_differs_from_session_rubric(db, session_id, config):
            st.warning("当前样卷映射表与考试批改绑定的 Word 评分标准不一致，建议先刷新映射表再标定题框。")
            if st.button("按当前 Word 评分标准刷新样卷映射表", key=f"refresh_template_mapping_{session_id}"):
                try:
                    if not _refresh_template_mapping_from_session(db, session_id):
                        raise ValueError("未找到可刷新的样卷图片或评分标准")
                    st.success("样卷映射表已刷新。")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(f"刷新失败：{exc}")

        rows = _template_config_to_editor_rows(config)
        summary_df = pd.DataFrame(rows)
        summary_columns = ["题号", "题型", "评分模式", "总分", "知识点", "题干摘要", "标准答案", "小问数"]
        existing_columns = [col for col in summary_columns if col in summary_df.columns]
        st.dataframe(
            summary_df[existing_columns] if existing_columns else summary_df,
            use_container_width=True,
            hide_index=True,
        )
        warnings = config.get("meta", {}).get("warnings", [])
        if warnings:
            st.info("映射提醒：\n- " + "\n- ".join(str(item) for item in warnings))
    return

def _template_config_to_editor_rows(config: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for question in config.get("questions", []):
        if not isinstance(question, dict):
            continue
        rows.append(
            {
                "确认": bool(question.get("is_confirmed")),
                "题号": str(question.get("question_id") or ""),
                "题型": str(question.get("question_type") or "comprehensive"),
                "评分模式": str(question.get("grading_mode") or "direct_answer"),
                "总分": float(question.get("max_score") or 0),
                "知识点": ", ".join(_template_question_knowledge_labels(question)),
                "题干摘要": str(question.get("stem_summary") or ""),
                "标准答案": str(question.get("canonical_answer") or ""),
                "证明义务": "; ".join(
                    [
                        str(item.get("description") or "")
                        for item in question.get("proof_obligations", [])
                        if isinstance(item, dict)
                    ]
                ),
                "扣分规则": "; ".join(
                    [
                        str(item.get("issue") or item.get("rule") or "")
                        for item in question.get("deduction_policy", [])
                        if isinstance(item, dict)
                    ]
                ),
                "等价答案预案": "; ".join([str(x) for x in question.get("accepted_forms", [])]),
                "可接受解法": "; ".join(
                    [
                        f"{item.get('name', '')}: {item.get('outline', '')}" if isinstance(item, dict) else str(item)
                        for item in question.get("method_variants", [])
                    ]
                ),
                "步骤数": sum(
                    len(part.get("steps", []))
                    for part in question.get("parts", [])
                    if isinstance(part, dict) and isinstance(part.get("steps"), list)
                ),
                "作答区数": len(question.get("regions", [])) if isinstance(question.get("regions"), list) else 0,
            }
        )
    return rows


def _apply_editor_rows_to_template_config(config: dict[str, Any], edited: pd.DataFrame) -> dict[str, Any]:
    next_config = json.loads(json.dumps(config, ensure_ascii=False))
    questions = next_config.get("questions")
    if not isinstance(questions, list):
        raise ValueError("样卷配置缺少 questions")

    edited_rows = edited.to_dict(orient="records")
    if len(edited_rows) != len(questions):
        raise ValueError("编辑行数与配置题目数不一致")

    for question, row in zip(questions, edited_rows):
        old_score = float(question.get("max_score") or 0)
        new_score = float(row.get("总分") or 0)
        question["is_confirmed"] = bool(row.get("确认"))
        question["question_id"] = str(row.get("题号") or question.get("question_id") or "").strip()
        question["question_type"] = str(row.get("题型") or question.get("question_type") or "comprehensive").strip()
        question["grading_mode"] = str(row.get("评分模式") or question.get("grading_mode") or "direct_answer").strip()
        question["max_score"] = new_score
        knowledge_ids = _split_knowledge_ids(row.get("知识点"))
        question["knowledge_ids"] = knowledge_ids
        question["knowledge_id"] = knowledge_ids[0] if knowledge_ids else "UNKNOWN"
        question["knowledge_points"] = [
            {"knowledge_id": kid, "knowledge_name": str(question.get("knowledge_name") or "") if idx == 0 else ""}
            for idx, kid in enumerate(knowledge_ids)
        ]
        question["stem_summary"] = str(row.get("题干摘要") or "").strip()
        question["canonical_answer"] = str(row.get("标准答案") or "").strip()
        obligation_texts = _split_semicolon_text(row.get("证明义务"))
        question["proof_obligations"] = [
            {
                "obligation_id": f"O{idx}",
                "description": text,
                "weight": round(new_score / max(len(obligation_texts), 1), 2) if obligation_texts else 0,
                "acceptable_evidence": [],
            }
            for idx, text in enumerate(obligation_texts, start=1)
        ]
        question["deduction_policy"] = [
            {"issue": text, "max_deduction": 0.0, "severity": "major"}
            for text in _split_semicolon_text(row.get("扣分规则"))
        ]
        question["accepted_forms"] = _split_semicolon_text(row.get("等价答案预案"))
        question["method_variants"] = [
            {"name": f"方法{idx}", "outline": text}
            for idx, text in enumerate(_split_semicolon_text(row.get("可接受解法")), start=1)
        ]
        _rescale_question_scores(question, old_score=old_score, new_score=new_score)

    enforce_integer_scores_by_type(
        questions,
        target_total=100,
        max_question_score=MAX_QUESTION_SCORE,
    )
    return next_config


def _rescale_question_scores(question: dict[str, Any], *, old_score: float, new_score: float) -> None:
    parts = question.get("parts")
    if not isinstance(parts, list) or not parts:
        question["parts"] = [
            {
                "part_id": question.get("question_id") or "Q",
                "part_score": new_score,
                "answer": question.get("canonical_answer") or "",
                "analysis": "",
                "steps": [
                    {
                        "step_id": "S1",
                        "step_score": new_score,
                        "core_goal": "得到正确答案或完成核心证明逻辑",
                        "required_elements": ["答案正确", "关键过程合理"],
                        "allow_alternative_methods": True,
                    }
                ],
                "step_milestones": [],
                "presentation_rules": [{"rule": "过程表达规范、结论完整", "max_deduction": min(1.0, new_score)}],
            }
        ]
        return

    if old_score <= 0:
        part_total = sum(float(part.get("part_score") or 0) for part in parts if isinstance(part, dict))
        old_score = part_total if part_total > 0 else new_score
    ratio = (new_score / old_score) if old_score > 0 else 1.0
    for part in parts:
        if not isinstance(part, dict):
            continue
        old_part_score = float(part.get("part_score") or 0)
        new_part_score = round(old_part_score * ratio, 2)
        part["part_score"] = new_part_score
        steps = part.get("steps")
        if not isinstance(steps, list) or not steps:
            part["steps"] = [
                {
                    "step_id": "S1",
                    "step_score": new_part_score,
                    "core_goal": "得到正确答案或完成核心证明逻辑",
                    "required_elements": ["答案正确", "关键过程合理"],
                    "allow_alternative_methods": True,
                }
            ]
            continue
        for step in steps:
            if isinstance(step, dict):
                step["step_score"] = round(float(step.get("step_score") or 0) * ratio, 2)
        step_sum = sum(float(step.get("step_score") or 0) for step in steps if isinstance(step, dict))
        if steps and abs(step_sum - new_part_score) > 0.01 and isinstance(steps[-1], dict):
            steps[-1]["step_score"] = round(float(steps[-1].get("step_score") or 0) + new_part_score - step_sum, 2)

    part_sum = sum(float(part.get("part_score") or 0) for part in parts if isinstance(part, dict))
    if parts and abs(part_sum - new_score) > 0.01 and isinstance(parts[-1], dict):
        parts[-1]["part_score"] = round(float(parts[-1].get("part_score") or 0) + new_score - part_sum, 2)


def _split_semicolon_text(value: Any) -> list[str]:
    text = str(value or "").strip()
    if not text:
        return []
    return [item.strip() for item in text.replace("；", ";").split(";") if item.strip()]


def _template_question_knowledge_ids(question: dict[str, Any]) -> list[str]:
    return _split_knowledge_ids(
        question.get("knowledge_ids")
        or question.get("knowledge_points")
        or question.get("knowledge_id")
        or "UNKNOWN"
    )


def _template_question_knowledge_labels(question: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for point in question.get("knowledge_points", []) or []:
        if not isinstance(point, dict):
            continue
        kid = str(point.get("knowledge_id") or point.get("id") or "").strip()
        label = _knowledge_display_label(kid, str(point.get("knowledge_name") or point.get("name") or ""))
        if label and label != "未命名知识点":
            labels.append(label)
    if not labels:
        kid = str(question.get("knowledge_id") or "").strip()
        label = _knowledge_display_label(kid, str(question.get("knowledge_name") or question.get("stem_summary") or ""))
        if label and label != "未命名知识点":
            labels.append(label)
    return _unique_texts(labels) or ["未命名知识点"]


def _split_knowledge_ids(value: Any) -> list[str]:
    raw_values: list[Any] = []
    if isinstance(value, list):
        raw_values.extend(value)
    elif isinstance(value, str):
        raw_values.extend(
            part.strip()
            for part in value.replace("，", ",").replace("；", ",").replace(";", ",").replace("|", ",").split(",")
        )
    elif value:
        raw_values.append(value)

    result: list[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        if isinstance(raw, dict):
            kid = str(raw.get("knowledge_id") or raw.get("id") or "").strip()
        else:
            kid = str(raw or "").strip()
        if not kid or kid in seen:
            continue
        seen.add(kid)
        result.append(kid)
    return result or ["UNKNOWN"]


def _read_json_safely(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _resolve_session_file_path(path_value: object) -> Path:
    return resolve_stored_file_path(path_value, data_root=APP_DATA_DIR)


def _session_exam_upload_dir(session_id: int) -> Path:
    path = DEFAULT_EXAMS_DIR / f"session_{session_id}" / "uploaded_scans"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_uploaded_exam_files(uploaded_files: list[Any] | None, target_dir: Path, *, enhance_pdf_pages: bool = False) -> list[Path]:
    if not uploaded_files:
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, uploaded in enumerate(uploaded_files, start=1):
        original_name = str(getattr(uploaded, "name", "") or f"scan_{index}").strip()
        suffix = Path(original_name).suffix.lower()
        if suffix not in {".pdf", ".jpg", ".jpeg", ".png"}:
            continue
        stem = safe_filename_fragment(Path(original_name).stem, f"scan_{index}")
        target = target_dir / f"{index:03d}_{stem}{suffix}"
        data = uploaded.getbuffer()
        if target.exists() and target.stat().st_size == len(data):
            if suffix == ".pdf":
                try:
                    pages = render_pdf_to_standard_pages(
                        target,
                        target_dir / "_pdf_pages",
                        enhance_images=enhance_pdf_pages,
                        delete_source_pdf=True,
                    )
                    saved.extend(page.image_path for page in pages)
                except Exception:
                    saved.append(target)
            else:
                saved.append(target)
            continue
        target.write_bytes(bytes(data))
        if suffix == ".pdf":
            try:
                pages = render_pdf_to_standard_pages(
                    target,
                    target_dir / "_pdf_pages",
                    enhance_images=enhance_pdf_pages,
                    delete_source_pdf=True,
                )
                saved.extend(page.image_path for page in pages)
            except Exception:
                saved.append(target)
        else:
            saved.append(target)
    return saved


def _list_scan_input_files(path: Path) -> list[Path]:
    if not path.exists() or not path.is_dir():
        return []
    direct_files = [
        item
        for item in path.iterdir()
        if item.is_file() and item.suffix.lower() in {".pdf", ".jpg", ".jpeg", ".png"}
    ]
    rendered_pages_dir = path / "_pdf_pages"
    rendered_pages = list(rendered_pages_dir.rglob("page_*.jpg")) if rendered_pages_dir.exists() else []
    return sorted([*direct_files, *rendered_pages], key=lambda item: str(item))


def _session_work_dir(session_id: int) -> Path:
    path = TEMPLATE_DIR / f"session_{session_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_compact_json_file(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def _write_regions_snapshot(db: DBManager, session_id: int) -> Path | None:
    template = db.get_session_template(session_id)
    if not template:
        return None
    regions_path = Path(str(template.get("regions_path") or ""))
    if not regions_path:
        regions_path = _session_work_dir(session_id) / f"regions_confirmed_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    regions = db.list_answer_regions(session_id)
    _write_json_file(regions_path, regions)
    return regions_path


def _write_session_workflow_state(
    db: DBManager,
    session_id: int,
    stage: str,
    extra: dict[str, Any] | None = None,
) -> Path | None:
    with get_answer_region_session_lock(_session_work_dir(session_id)):
        session = db.get_grading_session(session_id)
        if not session:
            return None
        template = db.get_session_template(session_id)
        progress = db.get_session_progress(session_id)
        regions = db.list_answer_regions(session_id) if template else []
        state_path = _session_work_dir(session_id) / "workflow_state.json"
        previous = _read_json_safely(state_path) if state_path.exists() else {}
        previous_extra = previous.get("extra") if isinstance(previous, dict) else {}
        merged_extra = dict(previous_extra) if isinstance(previous_extra, dict) else {}
        merged_extra.update(extra or {})
        state = {
            "session_id": session_id,
            "session_name": session.get("session_name"),
            "stage": stage,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "active_paths": {
                "rubric_path": session.get("rubric_path"),
                "answer_key_path": session.get("answer_key_path"),
                "template_config_path": session.get("template_config_path"),
                "front_template_path": template.get("front_template_path") if template else None,
                "back_template_path": template.get("back_template_path") if template else None,
                "mapping_source_path": template.get("ai_analysis_path") if template else None,
                "template_mapping_path": template.get("template_config_path") if template else None,
                "regions_path": template.get("regions_path") if template else None,
            },
            "template_ready": db.is_template_ready(session_id),
            "region_count": len(regions),
            "progress": progress,
            "extra": merged_extra,
        }
        _write_json_file(state_path, state)
        return state_path


def _front_page_parity_from_first_page_role(first_page_role: str | None) -> str:
    return "even" if str(first_page_role or "").strip().lower() == "back" else "odd"


def _session_front_page_parity(session_id: int) -> str:
    session_key = f"template_pdf_first_page_role_{session_id}"
    if session_key in st.session_state:
        return _front_page_parity_from_first_page_role(str(st.session_state.get(session_key) or "front"))

    state_path = _session_work_dir(session_id) / "workflow_state.json"
    state = _read_json_safely(state_path) if state_path.exists() else {}
    extra = state.get("extra") if isinstance(state, dict) else {}
    if isinstance(extra, dict):
        parity = str(extra.get("front_page_parity") or "").strip().lower()
        if parity in {"odd", "even"}:
            return parity
        role = str(extra.get("template_first_page_role") or "").strip().lower()
        if role in {"front", "back"}:
            return _front_page_parity_from_first_page_role(role)
    return "odd"


def _student_name_region_for_scan(db: DBManager, session_id: int) -> dict[str, Any] | None:
    regions = answer_regions_with_template_source_sizes(db, session_id, data_root=APP_DATA_DIR)
    return student_name_region_from_regions(regions)


def _save_template_pages_from_pdf_upload(
    pdf_upload: Any,
    session_dir: Path,
    *,
    front_page_number: int,
    back_page_number: int,
) -> tuple[Path, Path]:
    try:
        import fitz  # type: ignore
    except ImportError as exc:
        raise RuntimeError("缺少 PyMuPDF，无法从 PDF 抽取样卷页面。请确认 requirements.txt 已安装。") from exc

    with get_answer_region_session_lock(session_dir):
        pdf_bytes = pdf_upload.getvalue()
        pdf_path = session_dir / "template_source_full_class.pdf"
        pdf_path.write_bytes(pdf_bytes)

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        try:
            if doc.page_count < 2:
                raise ValueError("上传的 PDF 少于 2 页，无法建立正反面样卷。")
            for page_number in (front_page_number, back_page_number):
                if page_number < 1 or page_number > doc.page_count:
                    raise ValueError(f"PDF 不存在第 {page_number} 页。")

            front_path = session_dir / "template_front_from_pdf_page.jpg"
            back_path = session_dir / "template_back_from_pdf_page.jpg"
            _render_pdf_page_to_image(doc, front_page_number - 1, front_path)
            _render_pdf_page_to_image(doc, back_page_number - 1, back_path)
            return front_path, back_path
        finally:
            doc.close()


def _render_pdf_page_to_image(doc: Any, page_index: int, output_path: Path) -> None:
    import fitz  # type: ignore

    page = doc.load_page(page_index)
    pix = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
    Image.frombytes("RGB", (pix.width, pix.height), pix.samples).save(output_path, format="JPEG", quality=90)


def _refresh_template_mapping_from_session(db: DBManager, session_id: int) -> bool:
    session = db.get_grading_session(session_id)
    template = db.get_session_template(session_id)
    if not session or not template:
        return False

    front_path = Path(str(template.get("front_template_path") or ""))
    back_path = Path(str(template.get("back_template_path") or ""))
    if not front_path.exists() or not back_path.exists():
        return False

    rubric = _read_json_safely(_resolve_session_file_path(session.get("rubric_path")))
    answer_key = _read_json_safely(_resolve_session_file_path(session.get("answer_key_path")))
    if not rubric or not answer_key:
        return False

    package = create_template_mapping_package(
        front_path,
        back_path,
        rubric=rubric,
        answer_key=answer_key,
        output_dir=TEMPLATE_DIR / f"session_{session_id}",
    )
    db.update_session_template_analysis(
        session_id,
        ai_analysis_path=package["paths"]["raw_path"],
        template_config_path=package["paths"]["config_path"],
        regions_path=package["paths"]["regions_path"],
    )
    _write_regions_snapshot(db, session_id)
    _write_session_workflow_state(db, session_id, "template_mapping_refreshed", {"package_paths": package["paths"]})
    return True


def _template_mapping_differs_from_session_rubric(db: DBManager, session_id: int, config: dict[str, Any]) -> bool:
    session = db.get_grading_session(session_id)
    if not session:
        return False
    rubric = _read_json_safely(_resolve_session_file_path(session.get("rubric_path")))
    rubric_questions = rubric.get("questions") if isinstance(rubric, dict) else []
    config_questions = config.get("questions") if isinstance(config, dict) else []
    if not isinstance(rubric_questions, list) or not isinstance(config_questions, list):
        return False

    rubric_map = {
        str(q.get("question_id") or "").strip(): (
            str(q.get("question_type") or "").strip(),
            round(float(q.get("max_score") or 0), 4),
        )
        for q in rubric_questions
        if isinstance(q, dict) and str(q.get("question_id") or "").strip()
    }
    config_map = {
        str(q.get("question_id") or "").strip(): (
            str(q.get("question_type") or "").strip(),
            round(float(q.get("max_score") or 0), 4),
        )
        for q in config_questions
        if isinstance(q, dict) and str(q.get("question_id") or "").strip()
    }
    return rubric_map != config_map


def _render_workflow_state_card(db: DBManager, session_id: int) -> None:
    state_path = _write_session_workflow_state(db, session_id, "state_viewed")
    session = db.get_grading_session(session_id)
    template = db.get_session_template(session_id)
    if not session:
        return

    with st.container(border=True):
        st.markdown("### 当前流程本地状态")
        st.caption("后续步骤只读取当前考试批改绑定的活动文件；历史调试文件不会自动参与流程。")
        rows = [
            {"步骤": "评分标准", "当前文件": Path(str(session.get("rubric_path") or "")).name or "未设置"},
            {"步骤": "答案依据", "当前文件": Path(str(session.get("answer_key_path") or "")).name or "未设置"},
            {"步骤": "样卷正面", "当前文件": Path(str(template.get("front_template_path") or "")).name if template else "未上传"},
            {"步骤": "样卷反面", "当前文件": Path(str(template.get("back_template_path") or "")).name if template else "未上传"},
            {"步骤": "题号候选映射", "当前文件": Path(str(template.get("template_config_path") or "")).name if template else "未生成"},
            {"步骤": "答题区域标定", "当前文件": Path(str(template.get("regions_path") or "")).name if template else "未保存"},
            {"步骤": "流程状态快照", "当前文件": state_path.name if state_path else "未生成"},
        ]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _enable_drawable_canvas_compat() -> bool:
    """Patch streamlit-drawable-canvas for Streamlit versions that moved image_to_url."""
    try:
        import streamlit.elements.image as st_image
        from streamlit.elements.lib import image_utils
        from types import SimpleNamespace

        if not hasattr(st_image, "image_to_url"):
            def image_to_url_compat(image, width, clamp, channels, output_format, image_id):
                return image_utils.image_to_url(
                    image,
                    SimpleNamespace(width=width),
                    clamp,
                    channels,
                    output_format,
                    image_id,
                )

            st_image.image_to_url = image_to_url_compat  # type: ignore[attr-defined]
        return True
    except Exception:
        return False


def _render_region_editor_legacy(
    db: DBManager,
    session_id: int,
    session: dict[str, Any],
    template: dict[str, Any],
    llm_settings: Any,
) -> None:
    """Interactive region editor: drawable canvas (left) + mapping table (right)."""
    try:
        from streamlit_drawable_canvas import st_canvas  # type: ignore[import]
        _has_canvas = _enable_drawable_canvas_compat()
    except ImportError:
        _has_canvas = False

    question_candidates = _load_question_id_candidates(session)
    parent_question_ids = _load_parent_question_ids(session)
    regions: list[dict[str, Any]] = db.list_answer_regions(session_id)

    # Cache regions in session_state so edits survive reruns within the same interaction
    cache_key = f"regions_{session_id}"
    sel_key = f"sel_region_idx_{session_id}"

    if cache_key not in st.session_state:
        st.session_state[cache_key] = [dict(r) for r in regions]
    regions = st.session_state[cache_key]

    if sel_key not in st.session_state:
        st.session_state[sel_key] = None

    page_tabs = st.tabs(["正面（Front）", "反面（Back）"])
    for tab_idx, (tab, page) in enumerate(zip(page_tabs, ["front", "back"])):
        with tab:
            template_path = Path(
                template["front_template_path"] if page == "front" else template["back_template_path"]
            )
            page_regions = [r for r in regions if str(r.get("page")) == page]
            page_indices = [i for i, r in enumerate(regions) if str(r.get("page")) == page]

            if not template_path.exists():
                st.warning(f"模板图片不存在：{template_path}")
                continue

            img = Image.open(template_path).convert("RGB")
            orig_w, orig_h = img.size

            # Canvas width capped at 900px for display; compute scale factor
            CANVAS_W = 900
            scale = CANVAS_W / orig_w
            canvas_h = int(orig_h * scale)

            col_canvas, col_table = st.columns([6, 4])

            with col_canvas:
                st.markdown(f"**{page} 面 — 编辑作答区域**")

                if _has_canvas:
                    default_mode = "编辑已有框" if page_regions else "新增框"
                    mode_label = st.radio(
                        "画布模式",
                        options=["编辑已有框", "新增框"],
                        index=0 if default_mode == "编辑已有框" else 1,
                        horizontal=True,
                        key=f"canvas_mode_{session_id}_{page}",
                    )
                    drawing_mode = "transform" if mode_label == "编辑已有框" else "rect"

                    # Build initial_drawing objects from regions
                    sel_idx = st.session_state[sel_key]
                    objects = []
                    for local_i, r in enumerate(page_regions):
                        is_sel = (page_indices[local_i] == sel_idx)
                        stroke_color = "#FF0000" if not is_sel else "#00CC44"
                        stroke_w = 2 if not is_sel else 4
                        fill = "rgba(255,0,0,0.05)" if not is_sel else "rgba(0,200,68,0.15)"
                        qid = str(r.get("mapped_question_id") or r.get("detected_question_id") or "?")
                        objects.append({
                            "type": "rect",
                            "left": int(r["x"] * scale),
                            "top": int(r["y"] * scale),
                            "width": int(r["w"] * scale),
                            "height": int(r["h"] * scale),
                            "strokeColor": stroke_color,
                            "strokeWidth": stroke_w,
                            "fill": fill,
                        })

                    canvas_result = st_canvas(
                        fill_color="rgba(255, 0, 0, 0.05)",
                        stroke_width=2,
                        stroke_color="#FF0000",
                        background_image=img,
                        update_streamlit=True,
                        height=canvas_h,
                        width=CANVAS_W,
                        drawing_mode=drawing_mode,
                        initial_drawing={"version": "4.4.0", "objects": objects},
                        key=f"canvas_{session_id}_{page}",
                        display_toolbar=True,
                    )

                    # Handle canvas output: detect newly drawn rect
                    if canvas_result.json_data is not None:
                        drawn_objs = canvas_result.json_data.get("objects", [])
                        changed = False
                        # Existing rects may be moved/resized by the user. Persist edits into the in-memory region cache.
                        known_count = len(page_regions)
                        for obj_idx, drawn_obj in enumerate(drawn_objs[:known_count]):
                            glob_i = page_indices[obj_idx]
                            nx = int(float(drawn_obj.get("left", 0)) / scale)
                            ny = int(float(drawn_obj.get("top", 0)) / scale)
                            nw = int(float(drawn_obj.get("width", 0)) / scale)
                            nh = int(float(drawn_obj.get("height", 0)) / scale)
                            if nw > 10 and nh > 10:
                                next_bbox = {"x": nx, "y": ny, "w": nw, "h": nh}
                                if any(int(regions[glob_i].get(k, 0)) != v for k, v in next_bbox.items()):
                                    regions[glob_i].update(next_bbox)
                                    regions[glob_i]["is_confirmed"] = False
                                    changed = True

                        # New rects beyond known count = user drew one
                        if len(drawn_objs) > known_count:
                            for new_obj in drawn_objs[known_count:]:
                                nx = int(new_obj.get("left", 0) / scale)
                                ny = int(new_obj.get("top", 0) / scale)
                                nw = int(new_obj.get("width", 0) / scale)
                                nh = int(new_obj.get("height", 0) / scale)
                                if nw > 10 and nh > 10:
                                    new_order = max((r["region_order"] for r in regions), default=0) + 1
                                    new_region: dict[str, Any] = {
                                        "id": None,
                                        "page": page,
                                        "region_order": new_order,
                                        "x": nx, "y": ny, "w": nw, "h": nh,
                                        "detected_question_id": None,
                                        "mapped_question_id": None,
                                        "confidence": 0.0,
                                        "is_confirmed": False,
                                    }
                                    regions.append(new_region)
                                    changed = True
                        if changed:
                            st.session_state[cache_key] = regions
                            st.rerun()
                else:
                    # Fallback: static image with PIL-drawn boxes
                    annotated = _draw_template_regions_with_highlight(
                        img, page_regions, sel_idx=st.session_state[sel_key],
                        all_region_indices=page_indices,
                    )
                    st.image(annotated, use_container_width=True)
                    st.caption("当前 Streamlit 版本与画布组件不兼容，已切换为稳定预览模式。请在右侧表格中修改坐标。")

            with col_table:
                st.markdown("**题框映射表（选中行高亮对应框）**")

                if question_candidates:
                    _units_legacy = [
                        o for o in _region_binding_options(question_candidates, parent_question_ids)
                        if o and o != STUDENT_NAME_REGION_ID
                    ]
                    st.caption("可用题号：" + "、".join(_units_legacy))

                if st.button("新增作答区", key=f"add_region_{session_id}_{page}", use_container_width=True):
                    new_order = max((int(r.get("region_order", 0)) for r in regions), default=0) + 1
                    regions.append(
                        {
                            "id": None,
                            "page": page,
                            "region_order": new_order,
                            "x": max(0, int(orig_w * 0.08)),
                            "y": max(0, int(orig_h * 0.18)),
                            "w": max(120, int(orig_w * 0.72)),
                            "h": max(80, int(orig_h * 0.12)),
                            "detected_question_id": None,
                            "mapped_question_id": None,
                            "confidence": 0.0,
                            "is_confirmed": False,
                        }
                    )
                    st.session_state[cache_key] = regions
                    st.rerun()

                for local_i, (glob_i, r) in enumerate(zip(page_indices, page_regions)):
                    is_sel = (glob_i == st.session_state[sel_key])
                    bg = "background-color:#e8f5e9;" if is_sel else ""
                    label = (
                        f"{'✅' if r.get('is_confirmed') else '⬜'} "
                        f"#{r.get('region_order', local_i + 1)} "
                        f"{r.get('mapped_question_id') or r.get('detected_question_id') or '（未指定）'}"
                    )
                    with st.container():
                        row_cols = st.columns([3, 3, 1])
                        with row_cols[0]:
                            if st.button(label, key=f"sel_row_{session_id}_{page}_{glob_i}", use_container_width=True):
                                st.session_state[sel_key] = glob_i
                                st.rerun()
                        with row_cols[1]:
                            qid_options = _region_binding_options(question_candidates, parent_question_ids)
                            cur_qid = str(r.get("mapped_question_id") or "")
                            try:
                                cur_idx = qid_options.index(cur_qid)
                            except ValueError:
                                cur_idx = 0
                            chosen = st.selectbox(
                                "题号",
                                options=qid_options,
                                index=cur_idx,
                                format_func=lambda v: _region_binding_label(v, parent_question_ids),
                                key=f"qid_sel_{session_id}_{page}_{glob_i}",
                                label_visibility="collapsed",
                            )
                            if chosen != cur_qid:
                                regions[glob_i]["mapped_question_id"] = chosen or None
                                regions[glob_i]["is_confirmed"] = bool(chosen)
                                st.session_state[cache_key] = regions
                                st.rerun()
                        with row_cols[2]:
                            if st.button("🗑", key=f"del_region_{session_id}_{page}_{glob_i}", help="删除此框"):
                                rid = r.get("id")
                                if rid is not None:
                                    db.delete_answer_region(int(rid))
                                regions.pop(glob_i)
                                # Re-index region_order
                                for new_i, reg in enumerate(regions, start=1):
                                    reg["region_order"] = new_i
                                st.session_state[cache_key] = regions
                                if st.session_state[sel_key] == glob_i:
                                    st.session_state[sel_key] = None
                                st.rerun()

                        coord_cols = st.columns(4)
                        coord_specs = [("x", "X"), ("y", "Y"), ("w", "宽"), ("h", "高")]
                        for coord_col, (field, label_text) in zip(coord_cols, coord_specs):
                            with coord_col:
                                value = st.number_input(
                                    label_text,
                                    min_value=0,
                                    max_value=max(orig_w, orig_h) * 2,
                                    value=int(r.get(field, 0)),
                                    step=5,
                                    key=f"bbox_{field}_{session_id}_{page}_{glob_i}",
                                )
                                if int(value) != int(regions[glob_i].get(field, 0)):
                                    regions[glob_i][field] = int(value)
                                    regions[glob_i]["is_confirmed"] = False
                                    st.session_state[cache_key] = regions

    # ---- Save confirmation button ----
    st.divider()
    if regions:
        if st.button("保存题框映射并确认", key=f"save_region_mapping_{session_id}", type="primary"):
            # Persist bbox changes and new regions not yet in DB
            template_row = db.get_session_template(session_id)
            template_id = int(template_row["id"]) if template_row else 0

            # Separate into existing (have DB id) and new
            existing_updates: list[dict[str, Any]] = []
            for r in regions:
                rid = r.get("id")
                if rid is not None:
                    # Update bbox in case it was modified (canvas edit not yet wired; keep for future)
                    db.update_answer_region_bbox(int(rid), int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"]))
                    existing_updates.append({
                        "id": int(rid),
                        "mapped_question_id": (str(r.get("mapped_question_id") or "").strip() or None),
                        "is_confirmed": bool(r.get("is_confirmed")),
                    })
                else:
                    # New region not yet in DB
                    db.add_answer_region(session_id, template_id, r)

            if existing_updates:
                db.bulk_update_answer_region_mapping(session_id, existing_updates)

            # Refresh from DB for confirmation check
            saved_regions = db.list_answer_regions(session_id)
            all_confirmed = all(
                bool(r.get("is_confirmed")) and bool(r.get("mapped_question_id"))
                for r in saved_regions
            )
            db.mark_template_confirmed(session_id, confirmed=all_confirmed)
            regions_path = _write_regions_snapshot(db, session_id)
            _write_session_workflow_state(
                db,
                session_id,
                "regions_confirmed" if all_confirmed else "regions_saved_incomplete",
                {"regions_path": str(regions_path) if regions_path else None},
            )

            # Clear cache so next load re-reads DB
            st.session_state.pop(cache_key, None)

            if all_confirmed:
                st.success("模板映射已确认，当前考试批改可开始批改")
            else:
                st.warning("已保存映射，但仍有未确认或未绑定题号的题框")
            st.rerun()
    else:
        st.info("暂无作答区域。请先上传样卷建立映射包，或使用右侧“新增作答区”。")


def _draw_template_regions_with_highlight_legacy(
    image: Image.Image,
    page_regions: list[dict[str, Any]],
    sel_idx: int | None,
    all_region_indices: list[int],
) -> Image.Image:
    """Draw region boxes on image with highlight for selected region."""
    image = image.copy()
    draw = ImageDraw.Draw(image)
    font = _load_preview_font()

    for local_i, r in enumerate(page_regions):
        glob_i = all_region_indices[local_i] if local_i < len(all_region_indices) else -1
        is_sel = (glob_i == sel_idx)
        x, y, w, h = int(r.get("x", 0)), int(r.get("y", 0)), int(r.get("w", 0)), int(r.get("h", 0))
        qid = str(r.get("mapped_question_id") or r.get("detected_question_id") or "")
        outline = (0, 180, 50) if is_sel else (255, 0, 0)
        lw = 4 if is_sel else 2
        draw.rectangle([x, y, x + w, y + h], outline=outline, width=lw)
        label = f"#{r.get('region_order', '')} {qid}".strip()
        tx, ty = x + 2, max(0, y - 22)
        bb = draw.textbbox((tx, ty), label, font=font)
        draw.rectangle([bb[0] - 2, bb[1] - 2, bb[2] + 2, bb[3] + 2], fill=(255, 255, 224), outline=outline)
        draw.text((tx, ty), label, fill=outline, font=font)

    return image



    if not image_path.exists():
        return Image.new("RGB", (800, 500), color=(240, 240, 240))

    image = Image.open(image_path).convert("RGB")
    draw = ImageDraw.Draw(image)
    font = _load_preview_font()

    page_regions = [r for r in regions if str(r.get("page")) == page]
    for region in page_regions:
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "")

        draw.rectangle([x, y, x + w, y + h], outline=(255, 0, 0), width=2)

        label = f"#{region.get('region_order', '')} {qid}".strip()
        tx, ty = x + 2, max(0, y - 22)
        bb = draw.textbbox((tx, ty), label, font=font)
        draw.rectangle([bb[0] - 2, bb[1] - 2, bb[2] + 2, bb[3] + 2], fill=(255, 255, 224), outline=(255, 0, 0))
        draw.text((tx, ty), label, fill=(180, 0, 0), font=font)

    return image


def _region_page_label(page: str) -> str:
    return "正面 Front" if page == "front" else "反面 Back"


def _region_question_label(region: dict[str, Any], fallback_order: int) -> str:
    qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "未绑定")
    if qid == STUDENT_NAME_REGION_ID:
        qid = "姓名识别区域"
    status = "已确认" if region.get("is_confirmed") and region.get("mapped_question_id") else "待确认"
    return f"{qid} ｜框 #{region.get('region_order', fallback_order)} ｜{status}"


def _region_canvas_label(region: dict[str, Any], fallback_order: int) -> str:
    qid = str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
    if qid == STUDENT_NAME_REGION_ID:
        return "姓名"
    return qid or f"#{region.get('region_order', fallback_order)}"


def _load_parent_question_ids(session: dict[str, Any]) -> list[str]:
    """提取有小问的大题题号（如 Q10、Q11），用于在映射表下拉中释放"整道大题"选项。

    _load_question_id_candidates 默认只列小问以保证自动增量绑定按最小单元顺序进行；
    本函数单独提供大题题号，仅用于人工下拉选择，不影响自动绑定顺序。
    """
    path = _resolve_session_file_path(session.get("rubric_path"))
    if not path.exists():
        return []
    try:
        rubric = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    parent_ids: list[str] = []
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return parent_ids
    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("question_id") or "").strip()
        parts = q.get("parts")
        has_multi_parts = isinstance(parts, list) and sum(
            1 for part in parts if isinstance(part, dict) and str(part.get("part_id") or "").strip()
        ) > 1
        if qid and has_multi_parts:
            parent_ids.append(qid)
    return parent_ids


def _region_binding_options(
    question_candidates: list[str],
    parent_question_ids: list[str] | None = None,
) -> list[str]:
    parents = [str(p).strip() for p in (parent_question_ids or []) if str(p).strip()]
    parent_set = set(parents)

    def _parent_of(candidate: str) -> str | None:
        # 小问 Q10(1)/Q10-1/Q10. 等都归属大题 Q10；候选本身等于大题号也算
        for p in parents:
            if candidate == p or candidate.startswith(p + "(") or candidate.startswith(p + "（") \
                    or candidate.startswith(p + "-") or candidate.startswith(p + "_") \
                    or candidate.startswith(p + "."):
                return p
        return None

    body: list[str] = []
    emitted_parents: set[str] = set()
    for cand in question_candidates:
        cand = str(cand).strip()
        if not cand:
            continue
        parent = _parent_of(cand)
        # 在某个大题的第一个小问之前，先插入"整道大题"选项，使下拉为 …Q10、Q10(1)、Q10(2)…
        if parent and parent not in emitted_parents:
            if parent not in body:
                body.append(parent)
            emitted_parents.add(parent)
        if cand not in body:
            body.append(cand)

    # 兜底：仍未出现的大题号（其小问不在候选里时）追加到末尾，避免遗漏
    for p in parents:
        if p not in body:
            body.append(p)

    return ["", STUDENT_NAME_REGION_ID, *body]


def _region_binding_label(value: str, parent_question_ids: "set[str] | list[str] | None" = None) -> str:
    if value == STUDENT_NAME_REGION_ID:
        return "姓名识别区域"
    if value and parent_question_ids and value in set(parent_question_ids):
        return f"{value}（整道大题）"
    return value


def _next_region_question_id(regions: list[dict[str, Any]], question_candidates: list[str]) -> str | None:
    if not question_candidates:
        return None
    used = {
        str(region.get("mapped_question_id") or region.get("detected_question_id") or "").strip()
        for region in regions
    }
    used.discard(STUDENT_NAME_REGION_ID)
    for candidate in question_candidates:
        if candidate and candidate not in used:
            return candidate
    return question_candidates[min(len(used), len(question_candidates) - 1)]


def _canvas_object_to_bbox(obj: dict[str, Any], scale: float) -> dict[str, int] | None:
    if scale <= 0:
        return None
    try:
        left = float(obj.get("left", 0))
        top = float(obj.get("top", 0))
        width = float(obj.get("width", 0)) * float(obj.get("scaleX", 1) or 1)
        height = float(obj.get("height", 0)) * float(obj.get("scaleY", 1) or 1)
    except (TypeError, ValueError):
        return None

    bbox = {
        "x": max(0, int(round(left / scale))),
        "y": max(0, int(round(top / scale))),
        "w": max(0, int(round(width / scale))),
        "h": max(0, int(round(height / scale))),
    }
    if bbox["w"] < 12 or bbox["h"] < 12:
        return None
    return bbox


def _build_canvas_objects(
    regions: list[dict[str, Any]],
    page_indices: list[int],
    selected_idx: int | None,
    scale: float,
) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for local_i, region in enumerate(regions):
        global_i = page_indices[local_i] if local_i < len(page_indices) else -1
        selected = global_i == selected_idx
        stroke = "#2563EB" if selected else "#EF4444"
        left = int(float(region.get("x", 0)) * scale)
        top = int(float(region.get("y", 0)) * scale)
        width = max(24, int(float(region.get("w", 0)) * scale))
        height = max(24, int(float(region.get("h", 0)) * scale))
        label = _region_canvas_label(region, local_i + 1)
        label_w = max(46, min(110, 18 + len(label) * 16))
        label_h = 30
        # Group the rectangle and label so the question tag moves with the box.
        objects.append(
            {
                "type": "group",
                "left": left,
                "top": top,
                "width": width,
                "height": height,
                "scaleX": 1,
                "scaleY": 1,
                "selectable": True,
                "hasControls": True,
                "hasBorders": True,
                "lockMovementX": False,
                "lockMovementY": False,
                "strokeUniform": True,
                "borderColor": stroke,
                "cornerColor": "#2563EB",
                "cornerStrokeColor": "#FFFFFF",
                "cornerSize": 10,
                "transparentCorners": False,
                "objects": [
                    {
                        "type": "rect",
                        "left": -width / 2,
                        "top": -height / 2,
                        "width": width,
                        "height": height,
                        "fill": "rgba(255, 255, 255, 0)",
                        "stroke": stroke,
                        "strokeColor": stroke,
                        "strokeWidth": 3,
                        "strokeUniform": True,
                        "selectable": False,
                        "evented": False,
                    },
                    {
                        "type": "rect",
                        "left": -width / 2 + 6,
                        "top": -height / 2 + 6,
                        "width": label_w,
                        "height": label_h,
                        "rx": 6,
                        "ry": 6,
                        "fill": stroke,
                        "stroke": "#FFFFFF",
                        "strokeWidth": 1,
                        "selectable": False,
                        "evented": False,
                    },
                    {
                        "type": "text",
                        "left": -width / 2 + 14,
                        "top": -height / 2 + 9,
                        "text": label,
                        "fontSize": 20,
                        "fontWeight": "bold",
                        "fontFamily": "Arial",
                        "fill": "#FFFFFF",
                        "selectable": False,
                        "evented": False,
                    },
                ],
            }
        )
    return objects


def _sync_canvas_objects_to_regions(
    drawn_objects: list[dict[str, Any]],
    page: str,
    page_regions: list[dict[str, Any]],
    page_indices: list[int],
    all_regions: list[dict[str, Any]],
    scale: float,
    question_candidates: list[str] | None = None,
) -> bool:
    changed = False
    region_objects = [
        obj for obj in drawn_objects
        if str(obj.get("type")) in {"rect", "group"} and not obj.get("excludeFromExport")
    ]
    known_count = len(page_regions)

    for obj_idx, drawn_obj in enumerate(region_objects[:known_count]):
        if obj_idx >= len(page_indices):
            continue
        bbox = _canvas_object_to_bbox(drawn_obj, scale)
        if not bbox:
            continue
        global_i = page_indices[obj_idx]
        if any(int(all_regions[global_i].get(field, 0)) != bbox[field] for field in ("x", "y", "w", "h")):
            all_regions[global_i].update(bbox)
            all_regions[global_i]["is_confirmed"] = False
            changed = True

    for drawn_obj in region_objects[known_count:]:
        bbox = _canvas_object_to_bbox(drawn_obj, scale)
        if not bbox:
            continue
        duplicate = any(
            str(region.get("page")) == page
            and abs(int(region.get("x", 0)) - bbox["x"]) < 3
            and abs(int(region.get("y", 0)) - bbox["y"]) < 3
            and abs(int(region.get("w", 0)) - bbox["w"]) < 3
            and abs(int(region.get("h", 0)) - bbox["h"]) < 3
            for region in all_regions
        )
        if duplicate:
            continue
        new_order = max((int(region.get("region_order", 0)) for region in all_regions), default=0) + 1
        mapped_qid = _next_region_question_id(all_regions, question_candidates or [])
        all_regions.append(
            {
                "id": None,
                "page": page,
                "region_order": new_order,
                **bbox,
                "detected_question_id": mapped_qid,
                "mapped_question_id": mapped_qid,
                "confidence": 0.0,
                "is_confirmed": bool(mapped_qid),
            }
        )
        changed = True

    return changed


def _render_region_editor(
    db: DBManager,
    session_id: int,
    session: dict[str, Any],
    template: dict[str, Any],
    llm_settings: Any,
) -> None:
    """Image-first answer region editor with compact question mapping controls."""
    try:
        from streamlit_drawable_canvas import st_canvas  # type: ignore[import]
        has_canvas = _enable_drawable_canvas_compat()
    except ImportError:
        has_canvas = False

    question_candidates = _load_question_id_candidates(session)
    cache_key = f"regions_{session_id}"
    sel_key = f"sel_region_idx_{session_id}"
    canvas_version_key = f"region_canvas_version_{session_id}"

    if cache_key not in st.session_state:
        st.session_state[cache_key] = [dict(region) for region in db.list_answer_regions(session_id)]
    if sel_key not in st.session_state:
        st.session_state[sel_key] = None
    if canvas_version_key not in st.session_state:
        st.session_state[canvas_version_key] = 0

    regions: list[dict[str, Any]] = st.session_state[cache_key]
    top_cols = st.columns([5, 1.4])
    with top_cols[0]:
        st.info("先在样卷图中拖动/缩放作答框，再在映射表绑定题号。新建框会自动按 Q1、Q2、Q3(1)… 顺序预绑定；点击底部保存后生效。")
    with top_cols[1]:
        if st.button("清空全部题框", key=f"clear_regions_v2_{session_id}", use_container_width=True):
            for region in list(regions):
                region_id = region.get("id")
                if region_id is not None:
                    db.delete_answer_region(int(region_id))
            st.session_state[cache_key] = []
            st.session_state[sel_key] = None
            st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
            db.mark_template_confirmed(session_id, confirmed=False)
            st.rerun()

    page_tabs = st.tabs(["正面 Front", "反面 Back"])
    for tab, page in zip(page_tabs, ["front", "back"]):
        with tab:
            template_path = Path(
                template["front_template_path"] if page == "front" else template["back_template_path"]
            )
            if not template_path.exists():
                st.warning(f"模板图片不存在：{template_path}")
                continue

            image = Image.open(template_path).convert("RGB")
            orig_w, orig_h = image.size
            canvas_w = min(1180, max(720, orig_w))
            scale = canvas_w / orig_w
            canvas_h = max(360, int(orig_h * scale))

            page_indices = [i for i, region in enumerate(regions) if str(region.get("page")) == page]
            page_regions = [regions[i] for i in page_indices]

            toolbar_cols = st.columns([2.4, 3.6, 2])
            with toolbar_cols[0]:
                st.markdown(f"**{_region_page_label(page)} · 编辑作答区域**")
            with toolbar_cols[1]:
                mode_options = ["编辑/移动已有框", "新增框"]
                mode_default = 0 if page_regions else 1
                mode_label = st.radio(
                    "画布模式",
                    mode_options,
                    index=mode_default,
                    horizontal=True,
                    key=f"canvas_mode_v2_{session_id}_{page}",
                    label_visibility="collapsed",
                )
            with toolbar_cols[2]:
                if st.button("新增默认作答区", key=f"add_region_v2_{session_id}_{page}", use_container_width=True):
                    new_order = max((int(region.get("region_order", 0)) for region in regions), default=0) + 1
                    mapped_qid = _next_region_question_id(regions, question_candidates)
                    regions.append(
                        {
                            "id": None,
                            "page": page,
                            "region_order": new_order,
                            "x": max(0, int(orig_w * 0.08)),
                            "y": max(0, int(orig_h * 0.18)),
                            "w": max(160, int(orig_w * 0.72)),
                            "h": max(90, int(orig_h * 0.12)),
                            "detected_question_id": mapped_qid,
                            "mapped_question_id": mapped_qid,
                            "confidence": 0.0,
                            "is_confirmed": bool(mapped_qid),
                        }
                    )
                    st.session_state[cache_key] = regions
                    st.session_state[sel_key] = len(regions) - 1
                    st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
                    st.rerun()

            live_sync = False
            canvas_init_key = f"canvas_initialized_v4_{session_id}_{page}_{st.session_state[canvas_version_key]}"
            if has_canvas:
                live_sync = st.checkbox(
                    "鼠标拖拽编辑模式",
                    value=False,
                    key=f"canvas_live_sync_v2_{session_id}_{page}",
                    help="默认关闭以减少页面刷新。只在需要鼠标拖拽/缩放框子时打开，完成后请关闭。",
                )
                if not live_sync:
                    st.session_state[canvas_init_key] = False

            if has_canvas and live_sync:
                st.caption("鼠标拖拽编辑模式已开启：拖动/缩放会同步到本地缓存。完成后请关闭此开关，再绑定题号或保存。")
                drawing_mode = "transform" if mode_label == "编辑/移动已有框" else "rect"
                objects = _build_canvas_objects(
                    page_regions,
                    page_indices,
                    st.session_state[sel_key],
                    scale,
                )
                first_canvas_mount = not bool(st.session_state.get(canvas_init_key, False))
                initial_drawing = {"version": "4.4.0", "objects": objects} if first_canvas_mount else None
                st.session_state[canvas_init_key] = True
                canvas_result = st_canvas(
                    fill_color="rgba(255, 255, 255, 0)",
                    stroke_width=3,
                    stroke_color="#EF4444",
                    background_image=image,
                    update_streamlit=live_sync,
                    height=canvas_h,
                    width=canvas_w,
                    drawing_mode=drawing_mode,
                    initial_drawing=initial_drawing,
                    key=f"canvas_v4_{session_id}_{page}_{st.session_state[canvas_version_key]}",
                    display_toolbar=True,
                )
                if canvas_result.json_data is not None:
                    drawn_objects = canvas_result.json_data.get("objects", [])
                    if isinstance(drawn_objects, list):
                        changed = _sync_canvas_objects_to_regions(
                            drawn_objects,
                            page,
                            page_regions,
                            page_indices,
                            regions,
                            scale,
                            question_candidates,
                        )
                        if changed:
                            st.session_state[cache_key] = regions
                            st.caption("已同步画布改动到本地缓存。继续调整或点击底部“保存题框映射并确认”。")
            else:
                preview = _draw_template_regions_with_highlight(
                    image,
                    page_regions,
                    sel_idx=st.session_state[sel_key],
                    all_region_indices=page_indices,
                )
                st.image(preview, use_container_width=True)
                st.caption("稳定预览模式：不加载拖拽画布，刷新频次最低。需要鼠标拖动框时，临时开启上方“启用拖拽同步”。")

            page_indices = [i for i, region in enumerate(regions) if str(region.get("page")) == page]
            page_regions = [regions[i] for i in page_indices]

            st.markdown("**题框映射表**")
            st.caption(
                "点击“定位”会高亮对应框；坐标参数已隐藏，位置和大小请直接在画布中拖动调整。"
            )
            if question_candidates:
                _units_v2 = [
                    o for o in _region_binding_options(
                        question_candidates, _load_parent_question_ids(session)
                    ) if o and o != STUDENT_NAME_REGION_ID
                ]
                st.caption("可绑定题号/小问：" + "、".join(_units_v2))
                st.caption("整题一个大框请选不带括号的大题号（如 Q10）；分小问框请选 Q10(1)、Q10(2) 这类小问编号。")

            for local_i, global_i in enumerate(page_indices):
                region = regions[global_i]
                selected = global_i == st.session_state[sel_key]
                row_cols = st.columns([1.1, 2.8, 2.5, 0.9])
                with row_cols[0]:
                    if st.button(
                        "定位" if not selected else "已选中",
                        key=f"select_region_v2_{session_id}_{page}_{global_i}",
                        use_container_width=True,
                        type="primary" if selected else "secondary",
                    ):
                        st.session_state[sel_key] = global_i
                        st.rerun()
                with row_cols[1]:
                    st.markdown(_region_question_label(region, local_i + 1))
                with row_cols[2]:
                    _parent_ids_v2 = _load_parent_question_ids(session)
                    qid_options = _region_binding_options(
                        question_candidates, _parent_ids_v2
                    )
                    current_qid = str(region.get("mapped_question_id") or "")
                    if current_qid and current_qid not in qid_options:
                        qid_options.append(current_qid)
                    current_index = qid_options.index(current_qid) if current_qid in qid_options else 0
                    chosen = st.selectbox(
                        "绑定题号/小问",
                        qid_options,
                        index=current_index,
                        format_func=lambda v: _region_binding_label(v, _parent_ids_v2),
                        key=f"qid_region_v2_{session_id}_{page}_{global_i}",
                        label_visibility="collapsed",
                    )
                    if chosen != current_qid:
                        region["mapped_question_id"] = chosen or None
                        region["is_confirmed"] = bool(chosen)
                        st.session_state[cache_key] = regions
                with row_cols[3]:
                    if st.button("删除", key=f"delete_region_v2_{session_id}_{page}_{global_i}", use_container_width=True):
                        region_id = region.get("id")
                        if region_id is not None:
                            db.delete_answer_region(int(region_id))
                        regions.pop(global_i)
                        for new_order, item in enumerate(regions, start=1):
                            item["region_order"] = new_order
                        st.session_state[cache_key] = regions
                        st.session_state[sel_key] = None
                        st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
                        st.rerun()

            selected_idx = st.session_state[sel_key]
            if isinstance(selected_idx, int) and 0 <= selected_idx < len(regions) and str(regions[selected_idx].get("page")) == page:
                selected_region = regions[selected_idx]
                with st.container(border=True):
                    st.markdown(f"**精调选中框：{_region_question_label(selected_region, selected_idx + 1)}**")
                    coord_cols = st.columns(4)
                    coord_specs = [
                        ("x", "X", max(orig_w, orig_h) * 2),
                        ("y", "Y", max(orig_w, orig_h) * 2),
                        ("w", "宽", max(orig_w, orig_h) * 2),
                        ("h", "高", max(orig_w, orig_h) * 2),
                    ]
                    for coord_col, (field, label, max_value) in zip(coord_cols, coord_specs):
                        with coord_col:
                            value = st.number_input(
                                label,
                                min_value=0,
                                max_value=max_value,
                                value=int(selected_region.get(field, 0)),
                                step=5,
                                key=f"bbox_region_v2_{field}_{session_id}_{page}_{selected_idx}",
                            )
                            if int(value) != int(selected_region.get(field, 0)):
                                selected_region[field] = int(value)
                                selected_region["is_confirmed"] = False
                                st.session_state[cache_key] = regions

    st.divider()
    if not regions:
        st.info("暂无作答区域。请先上传样卷建立映射包，或在上方手动新增默认作答区。")
        return

    if st.button("保存题框映射并确认", key=f"save_region_mapping_v2_{session_id}", type="primary"):
        template_row = db.get_session_template(session_id)
        template_id = int(template_row["id"]) if template_row else 0
        existing_updates: list[dict[str, Any]] = []

        for region in regions:
            region["is_confirmed"] = bool(region.get("mapped_question_id"))
            region_id = region.get("id")
            if region_id is not None:
                db.update_answer_region_bbox(
                    int(region_id),
                    int(region.get("x", 0)),
                    int(region.get("y", 0)),
                    int(region.get("w", 0)),
                    int(region.get("h", 0)),
                )
                existing_updates.append(
                    {
                        "id": int(region_id),
                        "mapped_question_id": str(region.get("mapped_question_id") or "").strip() or None,
                        "is_confirmed": bool(region.get("is_confirmed")),
                    }
                )
            else:
                db.add_answer_region(session_id, template_id, region)

        if existing_updates:
            db.bulk_update_answer_region_mapping(session_id, existing_updates)

        saved_regions = db.list_answer_regions(session_id)
        all_confirmed = all(
            bool(region.get("is_confirmed")) and bool(region.get("mapped_question_id"))
            for region in saved_regions
        )
        db.mark_template_confirmed(session_id, confirmed=all_confirmed)
        regions_path = _write_regions_snapshot(db, session_id)
        _write_session_workflow_state(
            db,
            session_id,
            "regions_confirmed" if all_confirmed else "regions_saved_incomplete",
            {"regions_path": str(regions_path) if regions_path else None},
        )
        st.session_state.pop(cache_key, None)

        if all_confirmed:
            st.success("模板映射已确认，当前考试批改可以开始正式批改。")
        else:
            st.warning("已保存映射，但仍有作答区域未绑定题号。请补齐后再开始批改。")
        st.rerun()

    st.markdown('<a href="#page-top" class="global-back-top">回到顶部</a>', unsafe_allow_html=True)


def _render_region_editor_v3(
    db: DBManager,
    session_id: int,
    session: dict[str, Any],
    template: dict[str, Any],
    llm_settings: Any,
) -> None:
    """Answer-region editor with a right-side, collapsible mapping panel."""
    try:
        from streamlit_drawable_canvas import st_canvas  # type: ignore[import]
        has_canvas = _enable_drawable_canvas_compat()
    except ImportError:
        has_canvas = False

    question_candidates = _load_question_id_candidates(session)
    cache_key = f"regions_{session_id}"
    sel_key = f"sel_region_idx_{session_id}"
    canvas_version_key = f"region_canvas_version_{session_id}"

    if cache_key not in st.session_state:
        st.session_state[cache_key] = [dict(region) for region in db.list_answer_regions(session_id)]
    if sel_key not in st.session_state:
        st.session_state[sel_key] = None
    if canvas_version_key not in st.session_state:
        st.session_state[canvas_version_key] = 0

    regions: list[dict[str, Any]] = st.session_state[cache_key]

    header_cols = st.columns([5, 1.4])
    with header_cols[0]:
        st.info("新增框会自动按最小评分单元绑定：Q1、Q2、Q3(1)、Q3(2)…；右侧映射表可折叠，避免占用标定画布。")
    with header_cols[1]:
        if st.button("清空全部题框", key=f"clear_regions_v3_{session_id}", use_container_width=True):
            for region in list(regions):
                region_id = region.get("id")
                if region_id is not None:
                    db.delete_answer_region(int(region_id))
            st.session_state[cache_key] = []
            st.session_state[sel_key] = None
            st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
            db.mark_template_confirmed(session_id, confirmed=False)
            st.rerun()

    page_tabs = st.tabs(["正面 Front", "反面 Back"])
    for tab, page in zip(page_tabs, ["front", "back"]):
        with tab:
            template_path = Path(
                template["front_template_path"] if page == "front" else template["back_template_path"]
            )
            if not template_path.exists():
                st.warning(f"模板图片不存在：{template_path}")
                continue

            image = Image.open(template_path).convert("RGB")
            orig_w, orig_h = image.size
            panel_open_key = f"region_mapping_panel_open_{session_id}_{page}"
            if panel_open_key not in st.session_state:
                st.session_state[panel_open_key] = True

            page_indices = [i for i, region in enumerate(regions) if str(region.get("page")) == page]
            page_regions = [regions[i] for i in page_indices]

            if st.session_state[panel_open_key]:
                canvas_col, panel_col = st.columns([7.2, 2.8], gap="large")
            else:
                canvas_col, panel_col = st.columns([9.2, 0.8], gap="large")

            with canvas_col:
                toolbar_cols = st.columns([2.4, 3.5, 2.1])
                with toolbar_cols[0]:
                    st.markdown(f"**{_region_page_label(page)} · 标定作答区域**")
                with toolbar_cols[1]:
                    mode_options = ["编辑/移动已有框", "新增框"]
                    mode_default = 0 if page_regions else 1
                    mode_label = st.radio(
                        "画布模式",
                        mode_options,
                        index=mode_default,
                        horizontal=True,
                        key=f"canvas_mode_v3_{session_id}_{page}",
                        label_visibility="collapsed",
                    )
                with toolbar_cols[2]:
                    if st.button("新增默认作答区", key=f"add_region_v3_{session_id}_{page}", use_container_width=True):
                        _append_default_region(regions, page, orig_w, orig_h, question_candidates)
                        st.session_state[cache_key] = regions
                        st.session_state[sel_key] = len(regions) - 1
                        st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
                        st.rerun()

                page_indices = [i for i, region in enumerate(regions) if str(region.get("page")) == page]
                page_regions = [regions[i] for i in page_indices]
                canvas_w = min(1180, max(720, orig_w))
                scale = canvas_w / orig_w
                canvas_h = max(360, int(orig_h * scale))

                live_sync = False
                canvas_init_key = f"canvas_initialized_v5_{session_id}_{page}_{st.session_state[canvas_version_key]}"
                if has_canvas:
                    live_sync = st.checkbox(
                        "启用拖拽同步",
                        value=False,
                        key=f"canvas_live_sync_v3_{session_id}_{page}",
                        help="默认关闭以减少刷新；需要鼠标拖动/缩放框子时临时开启，调整完再关闭。",
                    )
                    if not live_sync:
                        st.session_state[canvas_init_key] = False

                if has_canvas and live_sync:
                    drawing_mode = "transform" if mode_label == "编辑/移动已有框" else "rect"
                    first_canvas_mount = not bool(st.session_state.get(canvas_init_key, False))
                    initial_drawing = None
                    if first_canvas_mount:
                        initial_drawing = {
                            "version": "4.4.0",
                            "objects": _build_canvas_objects(
                                page_regions,
                                page_indices,
                                st.session_state[sel_key],
                                scale,
                            ),
                        }
                    st.session_state[canvas_init_key] = True
                    canvas_result = st_canvas(
                        fill_color="rgba(255, 255, 255, 0)",
                        stroke_width=3,
                        stroke_color="#EF4444",
                        background_image=image,
                        update_streamlit=True,
                        height=canvas_h,
                        width=canvas_w,
                        drawing_mode=drawing_mode,
                        initial_drawing=initial_drawing,
                        key=f"canvas_v5_{session_id}_{page}_{st.session_state[canvas_version_key]}",
                        display_toolbar=True,
                    )
                    if canvas_result.json_data is not None:
                        drawn_objects = canvas_result.json_data.get("objects", [])
                        if isinstance(drawn_objects, list):
                            changed = _sync_canvas_objects_to_regions(
                                drawn_objects,
                                page,
                                page_regions,
                                page_indices,
                                regions,
                                scale,
                                question_candidates,
                            )
                            if changed:
                                st.session_state[cache_key] = regions
                                st.caption("画布改动已同步到本地缓存。继续调整或点击底部保存。")
                else:
                    preview = _draw_template_regions_with_highlight(
                        image,
                        page_regions,
                        sel_idx=st.session_state[sel_key],
                        all_region_indices=page_indices,
                    )
                    st.image(preview, use_container_width=True)
                    st.caption("稳定预览模式：刷新频次最低。需要拖动/缩放框时，临时开启“启用拖拽同步”。")

            with panel_col:
                if not st.session_state[panel_open_key]:
                    if st.button("展开映射表", key=f"open_region_panel_{session_id}_{page}", use_container_width=True):
                        st.session_state[panel_open_key] = True
                        st.rerun()
                    st.caption(f"{len(page_regions)} 个框")
                else:
                    with st.container(border=True):
                        panel_head = st.columns([2.8, 1])
                        with panel_head[0]:
                            st.markdown("**题框映射表**")
                            st.caption("点击定位高亮框；新框会自动绑定下一个最小评分单元。")
                        with panel_head[1]:
                            if st.button("收起", key=f"close_region_panel_{session_id}_{page}", use_container_width=True):
                                st.session_state[panel_open_key] = False
                                st.rerun()

                        if question_candidates:
                            with st.expander("可绑定评分单元", expanded=False):
                                _units_v3 = [
                                    o for o in _region_binding_options(
                                        question_candidates, _load_parent_question_ids(session)
                                    ) if o and o != STUDENT_NAME_REGION_ID
                                ]
                                st.caption("、".join(_units_v3))
                                st.caption("提示：Q10 等不带括号的为整道大题，可整题一起评；Q10(1) 为对应小问。")

                        for local_i, global_i in enumerate(page_indices):
                            region = regions[global_i]
                            selected = global_i == st.session_state[sel_key]
                            row_cols = st.columns([1.3, 2.8, 0.9])
                            with row_cols[0]:
                                if st.button(
                                    "已选" if selected else "定位",
                                    key=f"select_region_v3_{session_id}_{page}_{global_i}",
                                    use_container_width=True,
                                    type="primary" if selected else "secondary",
                                ):
                                    st.session_state[sel_key] = global_i
                                    st.rerun()
                            with row_cols[1]:
                                _parent_ids_v3 = _load_parent_question_ids(session)
                                qid_options = _region_binding_options(
                                    question_candidates, _parent_ids_v3
                                )
                                current_qid = str(region.get("mapped_question_id") or "")
                                if current_qid and current_qid not in qid_options:
                                    qid_options.append(current_qid)
                                current_index = qid_options.index(current_qid) if current_qid in qid_options else 0
                                chosen = st.selectbox(
                                    f"框 #{region.get('region_order', local_i + 1)}",
                                    qid_options,
                                    index=current_index,
                                    format_func=lambda v: _region_binding_label(v, _parent_ids_v3),
                                    key=f"qid_region_v3_{session_id}_{page}_{global_i}",
                                )
                                if chosen != current_qid:
                                    region["mapped_question_id"] = chosen or None
                                    region["detected_question_id"] = chosen or region.get("detected_question_id")
                                    region["is_confirmed"] = bool(chosen)
                                    st.session_state[cache_key] = regions
                                    st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
                            with row_cols[2]:
                                if st.button("删", key=f"delete_region_v3_{session_id}_{page}_{global_i}", use_container_width=True):
                                    region_id = region.get("id")
                                    if region_id is not None:
                                        db.delete_answer_region(int(region_id))
                                    regions.pop(global_i)
                                    for new_order, item in enumerate(regions, start=1):
                                        item["region_order"] = new_order
                                    st.session_state[cache_key] = regions
                                    st.session_state[sel_key] = None
                                    st.session_state[canvas_version_key] = int(st.session_state[canvas_version_key]) + 1
                                    st.rerun()

                        selected_idx = st.session_state[sel_key]
                        if (
                            isinstance(selected_idx, int)
                            and 0 <= selected_idx < len(regions)
                            and str(regions[selected_idx].get("page")) == page
                        ):
                            selected_region = regions[selected_idx]
                            with st.expander("高级：坐标微调", expanded=False):
                                coord_cols = st.columns(2)
                                coord_specs = [
                                    ("x", "X", max(orig_w, orig_h) * 2),
                                    ("y", "Y", max(orig_w, orig_h) * 2),
                                    ("w", "宽", max(orig_w, orig_h) * 2),
                                    ("h", "高", max(orig_w, orig_h) * 2),
                                ]
                                for coord_i, (field, label, max_value) in enumerate(coord_specs):
                                    with coord_cols[coord_i % 2]:
                                        value = st.number_input(
                                            label,
                                            min_value=0,
                                            max_value=max_value,
                                            value=int(selected_region.get(field, 0)),
                                            step=5,
                                            key=f"bbox_region_v3_{field}_{session_id}_{page}_{selected_idx}",
                                        )
                                        if int(value) != int(selected_region.get(field, 0)):
                                            selected_region[field] = int(value)
                                            selected_region["is_confirmed"] = False
                                            st.session_state[cache_key] = regions

    st.divider()
    if not regions:
        st.info("暂无作答区域。请先新增作答区，或开启拖拽同步后在画布上画框。")
        return

    if st.button("保存题框映射并确认", key=f"save_region_mapping_v3_{session_id}", type="primary"):
        template_row = db.get_session_template(session_id)
        template_id = int(template_row["id"]) if template_row else 0
        existing_updates: list[dict[str, Any]] = []

        for region in regions:
            region["is_confirmed"] = bool(region.get("mapped_question_id"))
            region_id = region.get("id")
            if region_id is not None:
                db.update_answer_region_bbox(
                    int(region_id),
                    int(region.get("x", 0)),
                    int(region.get("y", 0)),
                    int(region.get("w", 0)),
                    int(region.get("h", 0)),
                )
                existing_updates.append(
                    {
                        "id": int(region_id),
                        "mapped_question_id": str(region.get("mapped_question_id") or "").strip() or None,
                        "is_confirmed": bool(region.get("is_confirmed")),
                    }
                )
            else:
                db.add_answer_region(session_id, template_id, region)

        if existing_updates:
            db.bulk_update_answer_region_mapping(session_id, existing_updates)

        saved_regions = db.list_answer_regions(session_id)
        all_confirmed = all(
            bool(region.get("is_confirmed")) and bool(region.get("mapped_question_id"))
            for region in saved_regions
        )
        db.mark_template_confirmed(session_id, confirmed=all_confirmed)
        regions_path = _write_regions_snapshot(db, session_id)
        _write_session_workflow_state(
            db,
            session_id,
            "regions_confirmed" if all_confirmed else "regions_saved_incomplete",
            {"regions_path": str(regions_path) if regions_path else None},
        )
        st.session_state.pop(cache_key, None)

        if all_confirmed:
            st.success("模板映射已确认，当前考试批改可以开始正式批改。")
        else:
            st.warning("已保存映射，但仍有作答区域未绑定题号。请补齐后再开始批改。")
        st.rerun()

    st.markdown('<a href="#page-top" class="global-back-top">回到顶部</a>', unsafe_allow_html=True)


def _append_default_region(
    regions: list[dict[str, Any]],
    page: str,
    orig_w: int,
    orig_h: int,
    question_candidates: list[str],
) -> None:
    new_order = max((int(region.get("region_order", 0)) for region in regions), default=0) + 1
    mapped_qid = _next_region_question_id(regions, question_candidates)
    regions.append(
        {
            "id": None,
            "page": page,
            "region_order": new_order,
            "x": max(0, int(orig_w * 0.08)),
            "y": max(0, int(orig_h * 0.18)),
            "w": max(160, int(orig_w * 0.72)),
            "h": max(90, int(orig_h * 0.12)),
            "detected_question_id": mapped_qid,
            "mapped_question_id": mapped_qid,
            "confidence": 0.0,
            "is_confirmed": bool(mapped_qid),
        }
    )


def _draw_template_regions_with_highlight(
    image: Image.Image,
    page_regions: list[dict[str, Any]],
    sel_idx: int | None,
    all_region_indices: list[int],
) -> Image.Image:
    """Draw answer regions with high-contrast translucent boxes."""
    base = image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (255, 255, 255, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    draw = ImageDraw.Draw(base)
    font = _load_preview_font()

    for local_i, region in enumerate(page_regions):
        global_i = all_region_indices[local_i] if local_i < len(all_region_indices) else -1
        selected = global_i == sel_idx
        x = int(region.get("x", 0))
        y = int(region.get("y", 0))
        w = int(region.get("w", 0))
        h = int(region.get("h", 0))
        if w <= 0 or h <= 0:
            continue

        outline = (37, 99, 235, 255) if selected else (239, 68, 68, 255)
        fill = (37, 99, 235, 46) if selected else (239, 68, 68, 34)
        line_width = 7 if selected else 5
        overlay_draw.rectangle([x, y, x + w, y + h], fill=fill)
        draw.rectangle([x, y, x + w, y + h], outline=outline, width=line_width)

        label = _region_question_label(region, local_i + 1)
        text_x = x + 8
        text_y = max(0, y - 30)
        bbox = draw.textbbox((text_x, text_y), label, font=font)
        draw.rounded_rectangle(
            [bbox[0] - 6, bbox[1] - 4, bbox[2] + 6, bbox[3] + 4],
            radius=6,
            fill=(255, 255, 255, 235),
            outline=outline,
            width=2,
        )
        draw.text((text_x, text_y), label, fill=outline, font=font)

    composed = Image.alpha_composite(base, overlay)
    return composed.convert("RGB")


def _load_preview_font() -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, 16)
        except Exception:
            continue
    return ImageFont.load_default()


def _load_question_id_candidates(session: dict[str, Any]) -> list[str]:
    path = _resolve_session_file_path(session.get("rubric_path"))
    if not path.exists():
        return []

    try:
        rubric = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    result: list[str] = []
    questions = rubric.get("questions") if isinstance(rubric, dict) else []
    if not isinstance(questions, list):
        return result

    for q in questions:
        if not isinstance(q, dict):
            continue
        qid = str(q.get("question_id") or "").strip()
        parts = q.get("parts")
        part_ids: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                pid = str(part.get("part_id") or "").strip()
                if pid:
                    part_ids.append(pid)
        # When a question has split parts / scoring units, region mapping should
        # bind to the smallest unit by default. This keeps auto-binding in the
        # same order teachers mark boxes: Q1, Q2, Q3(1), Q3(2), ...
        if part_ids:
            result.extend(part_ids)
        elif qid:
            result.append(qid)

    seen: set[str] = set()
    uniq: list[str] = []
    for item in result:
        if item not in seen:
            seen.add(item)
            uniq.append(item)
    return uniq


_SAAS_DASHBOARD_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+SC:wght@400;500;600;700;800&display=swap');

:root {
  --gm-bg:#F8FAFC;
  --gm-card:#FFFFFF;
  --gm-line:#E2E8F0;
  --gm-line-soft:#EEF2F7;
  --gm-text:#0F172A;
  --gm-muted:#64748B;
  --gm-subtle:#94A3B8;
  --gm-indigo:#4F46E5;
  --gm-indigo-dark:#4338CA;
  --gm-indigo-soft:#EEF2FF;
  --gm-blue:#2563EB;
  --gm-green:#059669;
  --gm-amber:#D97706;
  --gm-red:#DC2626;
  --gm-radius:18px;
  --gm-radius-xl:24px;
  --gm-shadow-sm:0 1px 2px rgba(15,23,42,.05),0 4px 14px rgba(15,23,42,.04);
  --gm-shadow-md:0 16px 42px rgba(15,23,42,.08),0 4px 12px rgba(15,23,42,.05);
}

html, body, .stApp {
  font-family: Inter, "Noto Sans SC", "Microsoft YaHei", system-ui, sans-serif !important;
}

.material-icons,
.material-icons-outlined,
.material-icons-round,
.material-icons-sharp,
.material-symbols-outlined,
.material-symbols-rounded,
.material-symbols-sharp,
span[class*="material-icons"],
span[class*="material-symbols"] {
  font-family: "Material Symbols Rounded", "Material Symbols Outlined", "Material Icons" !important;
  font-weight: normal !important;
  font-style: normal !important;
  line-height: 1 !important;
  letter-spacing: normal !important;
  text-transform: none !important;
  white-space: nowrap !important;
  word-wrap: normal !important;
  direction: ltr !important;
  -webkit-font-feature-settings: "liga" !important;
  -webkit-font-smoothing: antialiased !important;
  font-feature-settings: "liga" !important;
}

.stApp {
  background:
    radial-gradient(circle at 18% 2%, rgba(79,70,229,.08), transparent 28%),
    linear-gradient(180deg,#FFFFFF 0%, var(--gm-bg) 26%, #F6F8FB 100%) !important;
  color:var(--gm-text) !important;
}

.block-container {
  max-width:1480px !important;
  padding:1.25rem 2rem 3rem !important;
}

section[data-testid="stSidebar"] {
  background:linear-gradient(180deg,#0F172A 0%,#111827 100%) !important;
  border-right:1px solid rgba(148,163,184,.18) !important;
}

section[data-testid="stSidebar"]::before {
  content:"GradeMind";
  display:block;
  margin:1rem 1rem .35rem;
  padding:.78rem .9rem;
  color:#F8FAFC;
  font-size:1.08rem;
  font-weight:800;
  letter-spacing:-.035em;
  border-radius:18px;
  background:linear-gradient(135deg,rgba(79,70,229,.38),rgba(37,99,235,.12));
  border:1px solid rgba(148,163,184,.2);
}

section[data-testid="stSidebar"] h1,
section[data-testid="stSidebar"] h2,
section[data-testid="stSidebar"] h3 {
  color:#E2E8F0 !important;
  font-size:.72rem !important;
  font-weight:800 !important;
  letter-spacing:.11em !important;
  text-transform:uppercase !important;
  margin-top:1.1rem !important;
}

section[data-testid="stSidebar"] label,
section[data-testid="stSidebar"] p,
section[data-testid="stSidebar"] span {
  color:#CBD5E1 !important;
}

section[data-testid="stSidebar"] .stTextInput input,
section[data-testid="stSidebar"] div[data-baseweb="select"] > div {
  background:#172033 !important;
  border:1px solid #334155 !important;
  color:#E5E7EB !important;
  border-radius:12px !important;
  box-shadow:none !important;
}

section[data-testid="stSidebar"] .stButton > button {
  background:#172033 !important;
  border:1px solid #334155 !important;
  color:#E2E8F0 !important;
  border-radius:12px !important;
  font-weight:700 !important;
  box-shadow:none !important;
}

section[data-testid="stSidebar"] .stButton > button:hover {
  background:var(--gm-indigo) !important;
  border-color:var(--gm-indigo) !important;
  color:#FFFFFF !important;
}

.gm-sidebar-summary {
  margin:.25rem 0 .65rem;
  padding:.78rem .85rem;
  border-radius:16px;
  background:linear-gradient(180deg,rgba(30,41,59,.88),rgba(15,23,42,.82));
  border:1px solid rgba(148,163,184,.2);
  box-shadow:0 10px 28px rgba(0,0,0,.16);
}

.gm-sidebar-summary-title {
  color:#94A3B8;
  font-size:.72rem;
  font-weight:700;
  letter-spacing:.08em;
  text-transform:uppercase;
}

.gm-sidebar-summary-name {
  margin-top:.24rem;
  color:#F8FAFC;
  font-size:.98rem;
  font-weight:800;
  letter-spacing:-.03em;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}

.gm-sidebar-summary-meta {
  display:flex;
  align-items:center;
  gap:.42rem;
  margin-top:.42rem;
  color:#CBD5E1;
  font-size:.76rem;
}

.gm-topbar {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:1rem;
  margin:.15rem 0 1.25rem;
}
.gm-eyebrow {
  display:inline-flex;
  align-items:center;
  gap:.45rem;
  padding:.34rem .72rem;
  border-radius:999px;
  background:var(--gm-indigo-soft);
  color:var(--gm-indigo);
  font-size:.76rem;
  font-weight:800;
  letter-spacing:.08em;
  text-transform:uppercase;
}
.gm-title {
  margin:.65rem 0 .22rem;
  color:var(--gm-text);
  font-size:2.18rem;
  line-height:1.08;
  font-weight:800;
  letter-spacing:-.058em;
}
.gm-subtitle {
  color:var(--gm-muted);
  font-size:.98rem;
}
.gm-deploy {
  display:inline-flex;
  align-items:center;
  justify-content:center;
  height:42px;
  padding:0 1.05rem;
  color:#FFF;
  background:linear-gradient(135deg,#0F172A,#1E40AF);
  border-radius:14px;
  font-weight:800;
  box-shadow:0 14px 28px rgba(15,23,42,.2);
}

div[data-testid="stVerticalBlockBorderWrapper"] {
  border:1px solid rgba(226,232,240,.98) !important;
  border-radius:24px !important;
  background:rgba(255,255,255,.94) !important;
  box-shadow:var(--gm-shadow-sm) !important;
  padding:1rem !important;
}

h1 { color:var(--gm-text) !important; font-weight:800 !important; letter-spacing:-.055em !important; }
h2, h3, [data-testid="stMarkdownContainer"] h2 {
  color:#1E293B !important;
  font-weight:760 !important;
  letter-spacing:-.03em !important;
}
p, li, label { color:var(--gm-muted) !important; }

.stButton > button {
  border-radius:13px !important;
  min-height:39px !important;
  font-weight:750 !important;
  border:1px solid var(--gm-line) !important;
  background:#FFFFFF !important;
  color:#334155 !important;
  box-shadow:var(--gm-shadow-sm) !important;
}
.stButton > button:hover {
  transform:translateY(-1px);
  border-color:#CBD5E1 !important;
  color:#0F172A !important;
  box-shadow:var(--gm-shadow-md) !important;
}
.stButton > button[kind="primary"] {
  background:linear-gradient(135deg,var(--gm-indigo),var(--gm-blue)) !important;
  color:#FFFFFF !important;
  border-color:transparent !important;
}

.stTextInput input, .stTextArea textarea, div[data-baseweb="select"] > div {
  border:1px solid var(--gm-line) !important;
  border-radius:14px !important;
  background:#FFFFFF !important;
  color:var(--gm-text) !important;
  box-shadow:none !important;
}
.stTextInput input:focus, .stTextArea textarea:focus {
  border-color:var(--gm-indigo) !important;
  box-shadow:0 0 0 4px rgba(79,70,229,.12) !important;
}

.stFileUploader > div {
  border:1.5px dashed #CBD5E1 !important;
  border-radius:20px !important;
  background:linear-gradient(180deg,rgba(248,250,252,.92),rgba(255,255,255,.98)) !important;
  padding:1.15rem !important;
}
.stFileUploader > div:hover {
  border-color:var(--gm-indigo) !important;
  background:var(--gm-indigo-soft) !important;
}
.stFileUploader button [data-testid="stIconMaterial"],
.stFileUploader button span[class*="material"] {
  font-size: 0 !important;
  width: 0 !important;
  margin: 0 !important;
  overflow: hidden !important;
}
.stFileUploader > div::before {
  content:"文档拖拽 / Upload";
  display:block;
  margin-bottom:.45rem;
  color:#64748B;
  font-size:.78rem;
  font-weight:800;
  letter-spacing:.04em;
}

.stTabs [data-baseweb="tab-list"] {
  gap:.38rem !important;
  background:#EEF2F7 !important;
  border-radius:18px !important;
  padding:.35rem !important;
  border:1px solid var(--gm-line) !important;
}
.stTabs [data-baseweb="tab"] {
  border-radius:14px !important;
  color:#64748B !important;
  font-weight:800 !important;
  padding:.55rem 1rem !important;
}
.stTabs [aria-selected="true"][data-baseweb="tab"] {
  background:#FFFFFF !important;
  color:#0F172A !important;
  box-shadow:var(--gm-shadow-sm) !important;
}

.stDataFrame, .stDataEditor {
  border-radius:18px !important;
  border:1px solid var(--gm-line) !important;
  overflow:hidden !important;
  box-shadow:var(--gm-shadow-sm) !important;
}
[data-testid="stDataFrame"] th, [data-testid="stDataEditor"] th {
  background:#F8FAFC !important;
  color:#475569 !important;
  font-size:.76rem !important;
  font-weight:800 !important;
  letter-spacing:.04em !important;
  border-bottom:1px solid var(--gm-line) !important;
}
[data-testid="stDataFrame"] td, [data-testid="stDataEditor"] td {
  border-bottom:1px solid #EEF2F7 !important;
  min-height:42px !important;
  padding:.7rem .85rem !important;
}

[data-testid="stMetric"] {
  border:1px solid var(--gm-line) !important;
  border-radius:20px !important;
  background:#FFFFFF !important;
  box-shadow:var(--gm-shadow-sm) !important;
  padding:1rem !important;
}
[data-testid="stMetricLabel"] { color:#64748B !important; font-weight:800 !important; }
[data-testid="stMetricValue"] { color:#0F172A !important; font-weight:800 !important; }

div[data-testid="stAlert"] {
  border-radius:999px !important;
  border:1px solid var(--gm-line) !important;
  padding:.55rem .85rem !important;
  box-shadow:none !important;
}
div[data-testid="stAlert"] p { font-weight:700 !important; font-size:.86rem !important; }

.badge {
  display:inline-flex;
  align-items:center;
  gap:.35rem;
  padding:.28rem .68rem;
  border-radius:999px;
  font-size:.73rem;
  font-weight:800;
}
.badge-blue{background:var(--gm-indigo-soft);color:var(--gm-indigo);}
.badge-green{background:#ECFDF5;color:var(--gm-green);}
.badge-yellow{background:#FFFBEB;color:var(--gm-amber);}
.badge-red{background:#FEF2F2;color:var(--gm-red);}
.badge-gray{background:#F1F5F9;color:#64748B;}

code, pre, .stCode {
  border-radius:16px !important;
  background:#0F172A !important;
  color:#CBD5E1 !important;
}
[data-testid="stImage"] img {
  border-radius:20px !important;
  border:1px solid var(--gm-line) !important;
  box-shadow:var(--gm-shadow-sm) !important;
}
hr { border:0 !important; border-top:1px solid var(--gm-line) !important; margin:1.35rem 0 !important; }
[data-testid="stHorizontalBlock"] { gap:1.15rem !important; align-items:flex-start !important; }
::-webkit-scrollbar { width:8px; height:8px; }
::-webkit-scrollbar-track { background:transparent; }
::-webkit-scrollbar-thumb { background:#CBD5E1; border-radius:999px; }
button[title="View fullscreen"],
[data-testid="StyledFullScreenButton"] {
  display: none !important;
}
</style>
"""


def render_header() -> None:
    st.set_page_config(
        page_title="GradeMind · AI 阅卷系统",
        layout="wide",
        initial_sidebar_state="expanded",
        page_icon="✦",
    )
    _inject_css()
    st.markdown(_SAAS_DASHBOARD_CSS, unsafe_allow_html=True)
    st.markdown('<div id="page-top"></div>', unsafe_allow_html=True)
    st.markdown(
        """
        <div class="gm-topbar">
          <div>
            <div class="gm-eyebrow">EdTech Dashboard</div>
            <div class="gm-title">双面试卷 AI 自动批改</div>
            <div class="gm-subtitle">学生数据、评分依据、模板映射、批改审阅与学情统计的统一工作台。</div>
          </div>
          <div style="display:flex;gap:.55rem;align-items:center;">
            <span class="badge badge-green">Ready</span>
            <span class="gm-deploy">Deploy</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


# ── 侧边栏数据导出 / 导入 ──────────────────────────────

def _render_sidebar_data_transfer_legacy() -> None:
    """在侧边栏底部渲染一键导出/导入全部数据的功能。"""
    import io
    import zipfile as _zf

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 📦 数据管理")

    _root = BASE_DIR  # 项目根目录

    # ── 导出 ──
    if st.sidebar.button("📤 导出全部数据", use_container_width=True, key="export_all_data_btn"):
        with st.sidebar.status("正在打包数据...", expanded=True) as status:
            try:
                buf = io.BytesIO()
                file_count = 0
                dirs_to_pack = [
                    (_root / "user_data", "user_data"),
                    (_root / "config", "config"),
                ]
                skip_dirs = {"__pycache__", ".git", ".venv"}
                skip_ext = {".pyc", ".pyo", ".tmp", ".log"}

                with _zf.ZipFile(buf, "w", _zf.ZIP_DEFLATED, compresslevel=6) as zf:
                    for src_dir, arc_prefix in dirs_to_pack:
                        if not src_dir.exists():
                            continue
                        for item in sorted(src_dir.rglob("*")):
                            if not item.is_file():
                                continue
                            if any(p in skip_dirs for p in item.relative_to(src_dir).parts):
                                continue
                            if item.suffix.lower() in skip_ext:
                                continue
                            arc_name = f"{arc_prefix}/{item.relative_to(src_dir).as_posix()}"
                            zf.write(item, arc_name)
                            file_count += 1

                buf.seek(0)
                size_mb = len(buf.getvalue()) / (1024 * 1024)
                status.update(label=f"打包完成: {file_count} 个文件, {size_mb:.1f} MB", state="complete")

                st.sidebar.download_button(
                    label=f"⬇️ 下载数据包 ({size_mb:.1f} MB)",
                    data=buf.getvalue(),
                    file_name=f"AI阅卷系统_数据备份_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="download_data_zip_btn",
                )
            except Exception as exc:
                status.update(label="打包失败", state="error")
                st.sidebar.error(f"导出失败: {exc}")

    # ── 导入 ──
    uploaded = st.sidebar.file_uploader(
        "📥 导入数据包",
        type=["zip"],
        key="import_data_zip_uploader",
        help="上传之前导出的 .zip 数据包，将恢复全部数据",
    )
    if uploaded is not None:
        if "import_confirmed" not in st.session_state:
            st.session_state.import_confirmed = False

        if not st.session_state.import_confirmed:
            st.sidebar.warning("⚠️ 导入将覆盖当前全部数据！")
            cols = st.sidebar.columns(2)
            with cols[0]:
                if st.button("✅ 确认导入", key="confirm_import_btn", type="primary", use_container_width=True):
                    st.session_state.import_confirmed = True
                    st.rerun()
            with cols[1]:
                if st.button("❌ 取消", key="cancel_import_btn", use_container_width=True):
                    st.session_state.pop("import_confirmed", None)
                    st.rerun()
        else:
            with st.sidebar.status("正在导入数据...", expanded=True) as status:
                try:
                    # 导入前先备份
                    try:
                        sys.path.insert(0, str(_root / "update_tools"))
                        from backup_core import create_backup
                        create_backup("before_import", include_api_keys=True)
                        st.sidebar.caption("✅ 已自动备份当前数据")
                    except Exception:
                        pass

                    buf = io.BytesIO(uploaded.read())
                    imported_count = 0
                    # 只允许解压到 user_data/ 和 config/
                    allowed_prefixes = ("user_data/", "user_data\\", "config/", "config\\")
                    with _zf.ZipFile(buf, "r") as zf:
                        for member in zf.namelist():
                            if member.endswith("/"):
                                continue
                            normalized = os.path.normpath(member)
                            if normalized.startswith("..") or os.path.isabs(normalized):
                                continue
                            if not member.startswith(allowed_prefixes):
                                continue
                            dest = _root / normalized
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(member) as src, open(dest, "wb") as dst:
                                dst.write(src.read())
                            imported_count += 1

                    status.update(label=f"导入完成: {imported_count} 个文件", state="complete")
                    st.sidebar.success(f"已导入 {imported_count} 个文件，请刷新页面。")
                except Exception as exc:
                    status.update(label="导入失败", state="error")
                    st.sidebar.error(f"导入失败: {exc}")
                finally:
                    st.session_state.pop("import_confirmed", None)

def _render_sidebar_data_transfer() -> None:
    import io
    import zipfile as _zf

    st.sidebar.markdown("---")
    st.sidebar.markdown("### 数据管理")

    scope_label = st.sidebar.radio(
        "导出范围",
        ["轻量数据包（推荐）", "完整数据包"],
        index=0,
        key="data_export_scope_radio",
        help="轻量包跳过批注图、历史报告、备份包和临时图片缓存；完整包会保留这些大文件。",
    )
    export_scope = "lean" if scope_label.startswith("轻量") else "full"
    entries = build_export_manifest(default_export_sources(BASE_DIR, APP_DATA_DIR), scope=export_scope)
    estimated_mb = total_size_mb(entries)
    st.sidebar.caption(f"预计导出 {len(entries)} 个文件，原始大小约 {estimated_mb:.1f} MB")
    if export_scope == "lean":
        st.sidebar.caption("轻量包适合日常迁移和备份，不包含历史批注图、报告和备份压缩包。")
    if estimated_mb > EXPORT_SIZE_WARNING_MB:
        st.sidebar.warning("预计超过 200MB，浏览器下载或导入可能失败；建议先清理回收站或改用轻量包。")

    export_button_label = "导出轻量数据包" if export_scope == "lean" else "导出完整数据包"
    if st.sidebar.button(export_button_label, use_container_width=True, key="export_all_data_btn"):
        with st.sidebar.status("正在打包数据...", expanded=True) as status:
            try:
                entries = build_export_manifest(default_export_sources(BASE_DIR, APP_DATA_DIR), scope=export_scope)
                zip_bytes = create_export_zip_bytes(entries)
                size_mb = len(zip_bytes) / (1024 * 1024)
                status.update(label=f"打包完成: {len(entries)} 个文件，{size_mb:.1f} MB", state="complete")
                if size_mb > EXPORT_SIZE_WARNING_MB:
                    st.sidebar.warning("这个压缩包超过 200MB，下载或再次导入时可能不稳定。")

                st.sidebar.download_button(
                    label=f"下载数据包 ({size_mb:.1f} MB)",
                    data=zip_bytes,
                    file_name=f"AI阅卷系统_数据备份_{export_scope}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                    mime="application/zip",
                    use_container_width=True,
                    key="download_data_zip_btn",
                )
            except Exception as exc:
                status.update(label="打包失败", state="error")
                st.sidebar.error(f"导出失败: {exc}")

    uploaded = st.sidebar.file_uploader(
        "导入数据包",
        type=["zip"],
        key="import_data_zip_uploader",
        help="导入之前导出的 .zip 数据包，会覆盖当前同名数据文件。",
    )
    if uploaded is None:
        return

    upload_size = getattr(uploaded, "size", None)
    if upload_size:
        upload_size_mb = float(upload_size) / (1024 * 1024)
        st.sidebar.caption(f"待导入文件大小: {upload_size_mb:.1f} MB")
        if upload_size_mb > EXPORT_SIZE_WARNING_MB:
            st.sidebar.warning("这个数据包超过 200MB，浏览器上传和解压可能较慢或失败。")

    if "import_confirmed" not in st.session_state:
        st.session_state.import_confirmed = False

    if not st.session_state.import_confirmed:
        st.sidebar.warning("导入会覆盖当前同名数据文件。")
        cols = st.sidebar.columns(2)
        with cols[0]:
            if st.button("确认导入", key="confirm_import_btn", type="primary", use_container_width=True):
                st.session_state.import_confirmed = True
                st.rerun()
        with cols[1]:
            if st.button("取消", key="cancel_import_btn", use_container_width=True):
                st.session_state.pop("import_confirmed", None)
                st.rerun()
        return

    with st.sidebar.status("正在导入数据...", expanded=True) as status:
        try:
            try:
                sys.path.insert(0, str(BASE_DIR / "update_tools"))
                from backup_core import create_backup

                create_backup("before_import", include_api_keys=True)
                st.sidebar.caption("已自动备份当前数据")
            except Exception:
                pass

            buf = io.BytesIO(uploaded.read())
            imported_count = 0
            allowed_roots = {"user_data", "config"}
            with _zf.ZipFile(buf, "r") as zf:
                for member in zf.namelist():
                    if member.endswith("/"):
                        continue
                    normalized = os.path.normpath(member)
                    if normalized.startswith("..") or os.path.isabs(normalized):
                        continue
                    parts = Path(normalized).parts
                    if not parts or parts[0] not in allowed_roots:
                        continue
                    if parts[0] == "user_data":
                        dest = APP_DATA_DIR.joinpath(*parts[1:])
                        allowed_root = APP_DATA_DIR
                    else:
                        dest = (BASE_DIR / "config").joinpath(*parts[1:])
                        allowed_root = BASE_DIR / "config"
                    try:
                        dest.resolve().relative_to(allowed_root.resolve())
                    except ValueError:
                        continue
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(dest, "wb") as dst:
                        dst.write(src.read())
                    imported_count += 1

            status.update(label=f"导入完成: {imported_count} 个文件", state="complete")
            st.sidebar.success(f"已导入 {imported_count} 个文件，请刷新页面。")
        except Exception as exc:
            status.update(label="导入失败", state="error")
            st.sidebar.error(f"导入失败: {exc}")
        finally:
            st.session_state.pop("import_confirmed", None)


def main() -> None:
    render_header()
    db = ensure_env_ready()
    restore_persistent_config_state(st.session_state, db)
    analytics = AnalyticsService(db)

    llm_settings = build_llm_settings_from_sidebar()
    selected_session_id = render_sidebar_session_selector(db)

    # ── 侧边栏底部：数据导出 / 导入 ──
    _render_sidebar_data_transfer()

    focus_session_id = st.session_state.get("region_focus_session_id")
    if isinstance(focus_session_id, int) and not isinstance(focus_session_id, bool):
        render_answer_region_focus_page(
            db,
            session_id=focus_session_id,
            templates_dir=TEMPLATE_DIR,
        )
        return

    st.caption("默认工作区聚焦当前考试批改；学生库和跨考试知识图谱已移至“全局资料”。")

    detail = _read_graph_detail_query()
    if detail and detail.get("view") == "kg_detail" and detail.get("knowledge_id"):
        _render_knowledge_wrong_detail(
            db,
            detail.get("student_id"),
            str(detail.get("knowledge_id")),
            detail.get("session_ids") or None,
        )
        return
    if detail and detail.get("view") == "error_detail" and detail.get("error_category"):
        _render_error_wrong_detail(
            db,
            str(detail.get("error_category")),
            detail.get("student_id"),
            detail.get("session_ids") or None,
        )
        return

    tab1, tab2, tab3, tab4, tab5 = st.tabs(
        [
            "考试工作台",
            "批改进度",
            "评分审阅",
            "报告导出",
            "全局资料",
        ]
    )

    with tab1:
        with st.container(border=True):
            render_config_and_session_tab(db, llm_settings, selected_session_id)

    with tab2:
        with st.container(border=True):
            render_grading_tab(db, selected_session_id, llm_settings)

    with tab3:
        with st.container(border=True):
            render_review_tab(db, analytics, selected_session_id)

    with tab4:
        with st.container(border=True):
            render_export_tab(selected_session_id)

    with tab5:
        data_tab1, data_tab2 = st.tabs(["学生名单", "跨考试知识图谱"])
        with data_tab1:
            with st.container(border=True):
                render_student_import_section(db)
                st.markdown('<a class="global-back-top" href="#page-top">返回顶部</a>', unsafe_allow_html=True)
        with data_tab2:
            with st.container(border=True):
                render_global_weak_points_tab(db, analytics)
                st.markdown('<a class="global-back-top" href="#page-top">返回顶部</a>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
