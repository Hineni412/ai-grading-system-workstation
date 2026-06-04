import streamlit as st
import subprocess
import sys
import json
import pandas as pd
from pathlib import Path
from db_manager import DBManager
from path_manager import get_path_manager

def run_script_safely(cmd_list, timeout=60):
    try:
        result = subprocess.run(cmd_list, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            st.error(f"脚本执行失败 (Exit code {result.returncode})\n\n**STDOUT**:\n{result.stdout}\n\n**STDERR**:\n{result.stderr}")
            return False, result.stdout, result.stderr
        else:
            return True, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        st.error(f"脚本执行超时 (>{timeout}s)")
        return False, "", ""
    except Exception as e:
        st.error(f"调用外部脚本时发生异常: {e}")
        return False, "", ""

def render_objective_admission_wizard_tab(db: DBManager, selected_session_id: int | None):
    st.header("新试卷客观题准入向导")
    st.info("此向导帮助您安全、规范地完成客观题的准入评估与人工核查流程。所有评估与准入步骤均不影响正式成绩。")

    if not selected_session_id:
        st.warning("请在左侧侧边栏的下拉菜单中选择一个要评估的考试 Session。")
        return

    selected_session = str(selected_session_id)
    pm = get_path_manager()
    registry_file = pm.config_dir / "objective_registry" / f"{selected_session}.json"

    st.markdown(f"### Step 1: 当前 Session 概况 (ID: {selected_session})")
    has_registry = registry_file.exists()
    
    if has_registry:
        st.success("✅ 该 Session 已初始化准入注册表")
        try:
            with open(registry_file, 'r', encoding='utf-8') as f:
                reg_data = json.load(f)
            total_qs = len(reg_data)
            approved = sum(1 for q in reg_data.values() if q.get("status") in ["approved_trial", "approved_production"])
            blocked = sum(1 for q in reg_data.values() if "blocked" in q.get("status", ""))
            pending = sum(1 for q in reg_data.values() if q.get("status") in ["pending", "shadow_only", "dry_run_candidate"])
            
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("客观题总数", total_qs)
            c2.metric("Approved (可试用)", approved)
            c3.metric("Blocked (拦截)", blocked)
            c4.metric("Pending/Shadow (待定)", pending)
        except Exception as e:
            st.error(f"读取注册表出错: {e}")
    else:
        st.warning("⚠️ 该 Session 暂无客观题准入注册表，请进行初始化。")

    st.markdown("---")
    st.markdown("### Step 2: 初始化客观题 Registry")
    st.caption("扫描试卷配置，为新客观题建立初始档案。新题默认 pending，只能 shadow，不能直接 replacement；已有配置如 Q1/Q2 不会被覆盖。")
    if st.button("初始化本试卷客观题 Registry", type="primary"):
        with st.spinner("正在初始化..."):
            success, out, err = run_script_safely([sys.executable, "initialize_objective_registry.py", "--session-id", selected_session])
            if success:
                st.success("初始化完成！")
                st.code(out)
                st.rerun()

    st.markdown("---")
    st.markdown("### Step 3: 运行 Session 级客观题评估")
    st.caption("随机抽取学生样本运行 shadow 评估，或全局扫描裁图风险。")
    col1, col2 = st.columns(2)
    sample_size = col1.number_input("评估样本量", min_value=1, max_value=1000, value=20)
    all_students = col2.checkbox("评估全量学生 (All Students)")
    
    if st.button("运行客观题准入评估", type="primary"):
        with st.spinner("正在运行系统级评估，可能需要几分钟，请耐心等待..."):
            cmd = [sys.executable, "evaluate_objective_questions_for_session.py", "--session-id", selected_session, "--export-report"]
            if all_students:
                cmd.append("--all-students")
            else:
                cmd.extend(["--sample-size", str(sample_size)])
            success, out, err = run_script_safely(cmd, timeout=300)
            if success:
                st.success("评估完成！")
                st.code(out)
                st.rerun()

    st.markdown("---")
    st.markdown("### Step 4: 查看逐题准入状态")
    
    if has_registry:
        try:
            with open(registry_file, 'r', encoding='utf-8') as f:
                reg_data = json.load(f)
            
            rows = []
            for qid, qinfo in reg_data.items():
                status = qinfo.get("status", "unknown")
                rec = qinfo.get("recommended_for_allowlist", False)
                
                rows.append({
                    "题号": qid,
                    "状态": status,
                    "风险等级": qinfo.get("risk_level", "unknown"),
                    "推荐准入": "是" if rec else "否",
                    "允许模式": ", ".join(qinfo.get("allowed_modes", [])),
                    "拦截原因": qinfo.get("block_reason", ""),
                    "需人工审核率": f"{qinfo.get('need_review_rate', 0)*100:.1f}%",
                    "自动评分率": f"{qinfo.get('auto_scored_rate', 0)*100:.1f}%",
                    "裁图异常率": f"{qinfo.get('crop_error_rate', 0)*100:.1f}%",
                    "一致率": f"{qinfo.get('consistency_rate', 0)*100:.1f}%"
                })
            df = pd.DataFrame(rows)
            
            def color_status(val):
                color = ''
                if val in ["approved_trial", "approved_production"]: color = 'background-color: lightgreen'
                elif val == "dry_run_candidate": color = 'background-color: lightblue'
                elif val in ["shadow_only", "pending"]: color = 'background-color: lightyellow'
                elif "blocked" in val: color = 'background-color: lightcoral'
                elif val == "manual_review_required": color = 'background-color: orange'
                return color
            
            st.dataframe(df.style.applymap(color_status, subset=['状态']), use_container_width=True)
            
        except Exception as e:
            st.error(f"渲染逐题状态失败: {e}")

    st.markdown("---")
    st.markdown("### Step 5 & 6: 风险题处理与裁图校准")
    st.caption("针对 Blocked 或 Pending 题目，您可以生成裁图预览进行肉眼核对。如因截断或包含其他文字导致拦截，可覆写识别框 (recognition_box) 参数。")
    
    c_target, _ = st.columns([1, 1])
    target_q = c_target.text_input("输入要处理的题号 (例如: Q2)")
    
    if target_q:
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            if st.button(f"生成 {target_q} 裁图预览", use_container_width=True):
                with st.spinner("生成中..."):
                    success, out, err = run_script_safely([sys.executable, "preview_objective_crop_calibration.py", "--session-id", selected_session, "--question-id", target_q, "--export"])
                    if success:
                        st.success(f"已生成！请在 `logs/objective_crop_calibration/{selected_session}/{target_q}/` 中查看。")
                        
        with btn_col2:
            if st.button(f"生成 {target_q} 人工核查包", use_container_width=True):
                with st.spinner("打包中..."):
                    success, out, err = run_script_safely([sys.executable, "prepare_objective_human_audit_pack.py", "--session-id", selected_session, "--question-id", target_q, "--export"])
                    if success:
                        st.success(f"核查包生成完毕！请在 `logs/objective_human_audit_pack/{selected_session}/{target_q}/` 中查看。")
                        
        st.markdown("#### 覆写裁图框 (Recognition Box Override)")
        st.warning("提醒：配置仅影响客观题后台识别抠图，不影响原图和正式成绩。")
        ccol1, ccol2, ccol3, ccol4 = st.columns(4)
        calib_x = ccol1.number_input("x", min_value=0, step=1, key="wizard_calib_x")
        calib_y = ccol2.number_input("y", min_value=0, step=1, key="wizard_calib_y")
        calib_w = ccol3.number_input("w", min_value=0, step=1, key="wizard_calib_w")
        calib_h = ccol4.number_input("h", min_value=0, step=1, key="wizard_calib_h")
        
        if st.button(f"保存 {target_q} Override 坐标"):
            try:
                from objective_crop_calibration import save_recognition_override
                save_recognition_override(target_q, calib_x, calib_y, calib_w, calib_h)
                st.success(f"已保存 {target_q} 的校准参数并写入 objective_recognition_overrides.json！您现在可以执行 Step 7。")
            except Exception as e:
                st.error(f"保存失败: {e}")

    st.markdown("---")
    st.markdown("### Step 7: 重新评估指定题")
    st.caption("调整参数或代码后，可单独重新评估该题。")
    if target_q:
        if st.button(f"重新运行单题准入向导 ({target_q})", type="primary"):
            with st.spinner("执行中..."):
                success, out, err = run_script_safely([sys.executable, "run_objective_admission_wizard.py", "--session-id", selected_session, "--question-id", target_q])
                if success:
                    st.success("重新评估完成！")
                    st.code(out)
                    st.rerun()
    else:
        st.info("请在上方输入要处理的题号。")

    st.markdown("---")
    st.markdown("### Step 8: 生成最终准入报告")
    st.caption("所有校验完毕后，可生成该 Session 完整的可用准入清单及风险报告。")
    if st.button("生成最终 Registry Report"):
        with st.spinner("正在生成..."):
            success, out, err = run_script_safely([sys.executable, "generate_objective_question_registry_report.py", "--session-id", selected_session])
            if success:
                st.success("最终准入报告已生成！")
                st.code(out)
                st.info("请在日志目录中查阅 `objective_question_registry_report.md`。")
