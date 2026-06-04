from __future__ import annotations

import sqlite3
import json
from datetime import datetime
from pathlib import Path

import pandas as pd

from export_names import session_export_path_name
from path_manager import resolve_stored_file_path


class ReportGenerator:
    def __init__(self, db_path: Path, reports_dir: Path) -> None:
        self.db_path = db_path
        self.reports_dir = reports_dir

    def export(self) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.reports_dir / f"grading_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

        with sqlite3.connect(self.db_path) as conn:
            df_results = pd.read_sql_query(
                """
                SELECT
                    id,
                    student_name,
                    front_image,
                    back_image,
                    total_score,
                    student_score,
                    needs_human_review,
                    created_at
                FROM exam_results
                ORDER BY id ASC
                """,
                conn,
            )

            df_details = pd.read_sql_query(
                """
                SELECT
                    gd.exam_result_id,
                    er.student_name,
                    gd.question_id,
                    gd.score_awarded,
                    gd.deduction_reason,
                    gd.knowledge_id
                FROM grading_details gd
                JOIN exam_results er ON er.id = gd.exam_result_id
                ORDER BY gd.exam_result_id ASC, gd.id ASC
                """,
                conn,
            )

        if df_results.empty:
            raise ValueError("数据库中暂无批改结果，无法导出报表。")

        score_summary = self._build_score_summary(df_results)
        knowledge_summary = self._build_knowledge_summary(df_details)
        review_list = df_results[df_results["needs_human_review"] == 1].copy()

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            score_summary.to_excel(writer, sheet_name="成绩总表", index=False)
            df_details.to_excel(writer, sheet_name="题目明细", index=False)
            knowledge_summary.to_excel(writer, sheet_name="知识点统计", index=False)
            review_list.to_excel(writer, sheet_name="待人工复核", index=False)

        return output_path

    def export_session(self, session_id: int) -> Path:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        with sqlite3.connect(self.db_path) as conn:
            session_row = conn.execute(
                "SELECT session_name FROM grading_sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
            session_name = session_row[0] if session_row else f"考试批改_{session_id}"
            output_path = self.reports_dir / session_export_path_name(session_name, "成绩报表", "xlsx", timestamp)

            df_results = pd.read_sql_query(
                """
                SELECT
                    sr.id AS result_id,
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    sr.total_score,
                    sr.student_score,
                    sr.needs_human_review,
                    sr.graded_at
                FROM session_results sr
                JOIN students s ON s.id = sr.student_id
                WHERE sr.session_id = ?
                ORDER BY sr.id ASC
                """,
                conn,
                params=(session_id,),
            )

            df_attendance = pd.read_sql_query(
                """
                SELECT
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    sa.attendance_status,
                    sa.source_reason,
                    sa.created_at
                FROM session_attendance sa
                JOIN students s ON s.id = sa.student_id
                WHERE sa.session_id = ?
                ORDER BY s.class_name ASC, s.student_code ASC, s.name ASC
                """,
                conn,
                params=(session_id,),
            )

            df_details = pd.read_sql_query(
                """
                SELECT
                    sr.id AS result_id,
                    s.student_code,
                    s.name AS student_name,
                    s.class_name,
                    sd.question_id,
                    sd.score_awarded,
                    sd.deduction_reason,
                    sd.knowledge_id,
                    sd.knowledge_ids,
                    sd.error_category,
                    sd.error_summary
                FROM session_details sd
                JOIN session_results sr ON sr.id = sd.result_id
                JOIN students s ON s.id = sr.student_id
                WHERE sr.session_id = ?
                ORDER BY sr.id ASC, sd.id ASC
                """,
                conn,
                params=(session_id,),
            )

        if df_results.empty:
            raise ValueError("该会话暂无批改结果，无法导出报表。")

        # FILTER BOTTOM 8 PER CLASS FOR DETAILS
        valid_result_ids = []
        for class_name, group in df_results.groupby(df_results["class_name"].fillna("未分班")):
            # Sort by score descending
            group_sorted = group.sort_values(by="student_score", ascending=False)
            # Exclude bottom 8 if class size > 8
            if len(group_sorted) > 8:
                valid_group = group_sorted.iloc[:-8]
            else:
                # If 8 or fewer, exclude none or exclude all? Usually exclude none or exclude bottom half.
                # Requirement: exclude bottom 8. If less than 8, exclude all but top 1? Let's just exclude all if <=8 or maybe keep top 20%.
                # Safe fallback: exclude bottom 8 means if N > 8, keep N-8. If N <= 8, keep nothing? No, keep all to avoid empty.
                valid_group = group_sorted if len(group_sorted) <= 8 else group_sorted.iloc[:-8]
            valid_result_ids.extend(valid_group["result_id"].tolist())

        df_details_filtered = df_details[df_details["result_id"].isin(valid_result_ids)].copy()

        score_map, type_map = self._load_session_question_maps(session_id)
        knowledge_label_map = self._load_session_knowledge_label_map(session_id)
        
        # summary uses full df_results
        score_summary = self._build_compact_session_report(df_results, df_details, score_map)
        
        # details and knowledge use filtered details
        question_detail = self._build_question_score_detail_sheet(df_details_filtered, score_map, type_map)
        knowledge_summary = self._build_session_knowledge_summary_by_class(df_details_filtered, score_map, knowledge_label_map)

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            score_summary.to_excel(writer, sheet_name="成绩与小题明细", index=False)
            question_detail.to_excel(writer, sheet_name="得分情况及明细", index=False)
            knowledge_summary.to_excel(writer, sheet_name="知识点统计", index=False)
            if not df_attendance.empty:
                df_attendance.to_excel(writer, sheet_name="缺考与异常", index=False)

        return output_path

    def _build_score_summary(self, df_results: pd.DataFrame) -> pd.DataFrame:
        summary = df_results[["student_name", "total_score", "student_score", "needs_human_review", "created_at"]].copy()
        summary["得分率(%)"] = (summary["student_score"] / summary["total_score"] * 100).round(2)
        summary.rename(
            columns={
                "student_name": "学生姓名",
                "total_score": "试卷总分",
                "student_score": "学生得分",
                "needs_human_review": "需人工复核",
                "created_at": "批改时间",
            },
            inplace=True,
        )
        return summary

    def _build_session_score_summary(self, df_results: pd.DataFrame) -> pd.DataFrame:
        summary = df_results[
            ["student_code", "student_name", "class_name", "total_score", "student_score", "needs_human_review", "graded_at"]
        ].copy()
        summary["score_rate"] = (summary["student_score"] / summary["total_score"] * 100).round(2)
        summary.rename(
            columns={
                "student_code": "学号",
                "student_name": "学生姓名",
                "class_name": "班级",
                "total_score": "试卷总分",
                "student_score": "学生得分",
                "needs_human_review": "需人工复核",
                "graded_at": "批改时间",
                "score_rate": "得分率(%)",
            },
            inplace=True,
        )
        return summary

    def _build_compact_session_report(
        self,
        df_results: pd.DataFrame,
        df_details: pd.DataFrame,
        score_map: dict[str, float],
    ) -> pd.DataFrame:
        if df_details.empty:
            summary = df_results[["student_name", "class_name", "student_code", "student_score"]].copy()
            summary.columns = ["学生姓名", "班级", "学号", "总分"]
            return summary

        question_ids = _natural_question_order(df_details["question_id"].dropna().astype(str).unique().tolist())
        rows_by_result: dict[int, dict[str, object]] = {}
        # Compute rank and average per class
        class_stats = {}
        for class_name, group in df_results.groupby(df_results["class_name"].fillna("未分班")):
            group_scores = group["student_score"].dropna().tolist()
            avg = sum(group_scores) / len(group_scores) if group_scores else 0
            
            # Rank
            group = group.sort_values(by="student_score", ascending=False)
            group["班级排名"] = group["student_score"].rank(method="min", ascending=False).astype(int)
            group["班级均分"] = round(avg, 2)
            
            for _, r in group.iterrows():
                class_stats[int(r["result_id"])] = {
                    "班级排名": r["班级排名"],
                    "班级均分": r["班级均分"]
                }

        for result in df_results.to_dict(orient="records"):
            rid = int(result["result_id"])
            stats = class_stats.get(rid, {"班级排名": "-", "班级均分": "-"})
            
            rows_by_result[rid] = {
                "班级": result.get("class_name") or "未分班",
                "班级排名": stats["班级排名"],
                "班级均分": stats["班级均分"],
                "学生姓名": result.get("student_name"),
                "学号": result.get("student_code"),
                "总分": result.get("student_score"),
            }

        for detail in df_details.to_dict(orient="records"):
            result_id = int(detail.get("result_id") or 0)
            qid = str(detail.get("question_id") or "").strip()
            row = rows_by_result.get(result_id)
            if not qid or row is None:
                continue
            awarded = float(detail.get("score_awarded") or 0)
            full = score_map.get(qid)
            row[f"{qid}得分"] = f"{_format_score(awarded)}/{_format_score(full)}" if full is not None else _format_score(awarded)
            reason = str(detail.get("deduction_reason") or "").strip()
            if full is not None and awarded >= float(full) - 1e-6:
                reason = ""
            row[f"{qid}扣分原因"] = reason

        columns = ["班级", "班级排名", "班级均分", "学生姓名", "学号", "总分"]
        for qid in question_ids:
            columns.extend([f"{qid}得分", f"{qid}扣分原因"])
            
        df_out = pd.DataFrame(rows_by_result.values()).reindex(columns=columns)
        
        # Sort by Class, then Rank
        df_out.sort_values(by=["班级", "总分"], ascending=[True, False], inplace=True)
        return df_out

    def _load_session_score_map(self, session_id: int) -> dict[str, float]:
        return self._load_session_question_maps(session_id)[0]

    def _load_session_question_maps(self, session_id: int) -> tuple[dict[str, float], dict[str, str]]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT rubric_path FROM grading_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return {}, {}
        rubric_path = self._resolve_stored_file_path(row[0])
        if not rubric_path.exists():
            return {}, {}
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}, {}
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
                score_map[qid] = float(question.get("max_score") or 0)
                type_map[qid] = qtype
            parts = question.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if not isinstance(part, dict):
                        continue
                    pid = str(part.get("part_id") or "").strip()
                    if pid:
                        score_map[pid] = float(part.get("part_score") or 0)
                        type_map[pid] = qtype
        return score_map, type_map

    def _load_session_knowledge_label_map(self, session_id: int) -> dict[str, str]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT rubric_path FROM grading_sessions WHERE id = ?", (session_id,)).fetchone()
        if not row:
            return {}
        rubric_path = self._resolve_stored_file_path(row[0])
        if not rubric_path.exists():
            return {}
        try:
            rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

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
                label = _clean_knowledge_label(kid, point.get("knowledge_name") or point.get("name") or "")
                if kid and label:
                    labels[kid] = label
            primary_id = str(question.get("knowledge_id") or "").strip()
            primary_label = _clean_knowledge_label(
                primary_id,
                question.get("knowledge_name") or question.get("stem_summary") or "",
            )
            if primary_id and primary_label:
                labels.setdefault(primary_id, primary_label)
        return labels

    def _resolve_stored_file_path(self, path_value: object) -> Path:
        data_root = self.db_path.parent.parent if self.db_path.parent.name == "databases" else None
        return resolve_stored_file_path(path_value, data_root=data_root)

    def _build_question_score_detail_sheet(
        self,
        df_details: pd.DataFrame,
        score_map: dict[str, float],
        type_map: dict[str, str],
    ) -> pd.DataFrame:
        columns = [
            "题号",
            "题型",
            "满分",
            "全班得分率",
            "平均得分",
            "失分人数",
            "失分同学",
            "75%以上得分率学生",
            "25~75%得分率学生",
            "25%以下得分率学生",
        ]
        columns.append("主要错因")
        if df_details.empty:
            return pd.DataFrame(columns=columns)

        detail_records = _normalize_question_detail_records(df_details.to_dict(orient="records"), score_map)
        if not detail_records:
            return pd.DataFrame(columns=columns)
        work_df = pd.DataFrame(detail_records)

        rows: list[dict[str, object]] = []
        qids = _natural_question_order(work_df["question_id"].dropna().astype(str).unique().tolist())
        for qid in qids:
            qdf = work_df[work_df["question_id"].astype(str) == qid].copy()
            if qdf.empty:
                continue
            full_score = float(score_map.get(qid) or 0)
            if full_score <= 0:
                full_score = max(float(qdf["score_awarded"].max() or 0), 0.0)

            student_items = []
            lost_students = []
            high_rate = []
            mid_rate = []
            low_rate = []
            for item in qdf.to_dict(orient="records"):
                awarded = float(item.get("score_awarded") or 0)
                label = _student_label(item)
                rate = awarded / full_score * 100 if full_score > 0 else 100.0
                student_items.append((label, awarded, rate))
                if full_score > 0 and awarded < full_score - 1e-6:
                    lost_students.append(label)
            error_text = _summarize_error_categories(qdf.to_dict(orient="records"), full_score)

            qtype = str(type_map.get(qid) or "")
            if _is_solution_type(qtype):
                for label, _awarded, rate in student_items:
                    if rate >= 75:
                        high_rate.append(label)
                    elif rate >= 25:
                        mid_rate.append(label)
                    else:
                        low_rate.append(label)

            score_sum = float(qdf["score_awarded"].sum())
            count = len(qdf)
            full_sum = full_score * count
            class_rate = score_sum / full_sum * 100 if full_sum > 0 else 100.0
            rows.append(
                {
                    "题号": qid,
                    "题型": qtype,
                    "满分": _format_score(full_score),
                    "全班得分率": round(class_rate, 2),
                    "平均得分": round(score_sum / max(count, 1), 2),
                    "失分人数": len(lost_students),
                    "失分同学": "、".join(lost_students),
                    "75%以上得分率学生": "、".join(high_rate),
                    "25~75%得分率学生": "、".join(mid_rate),
                    "25%以下得分率学生": "、".join(low_rate),
                }
            )

        for row, qid in zip(rows, qids):
            qdf = work_df[work_df["question_id"].astype(str) == qid].copy()
            full_score = float(score_map.get(qid) or 0)
            if full_score <= 0 and not qdf.empty:
                full_score = max(float(qdf["score_awarded"].max() or 0), 0.0)
            row["主要错因"] = _summarize_error_categories(qdf.to_dict(orient="records"), full_score)

        return pd.DataFrame(rows, columns=columns).sort_values(
            by=["全班得分率", "题号"],
            ascending=[True, True],
            kind="stable",
        )

    def _build_session_knowledge_summary_by_class(
        self,
        df_details: pd.DataFrame,
        score_map: dict[str, float],
        knowledge_label_map: dict[str, str],
    ) -> pd.DataFrame:
        columns = ["班级", "知识点", "涉及题目", "累计得分", "累计满分", "得分率", "失分人数"]
        if df_details.empty:
            return pd.DataFrame(columns=columns)

        detail_records = _normalize_question_detail_records(df_details.to_dict(orient="records"), score_map)
        if not detail_records:
            return pd.DataFrame(columns=columns)

        buckets: dict[tuple[str, str], dict[str, object]] = {}
        lost_students: dict[tuple[str, str], set[str]] = {}
        for item in detail_records:
            qid = str(item.get("question_id") or "").strip()
            full_score = float(score_map.get(qid) or 0)
            if full_score <= 0:
                continue
            awarded = max(0.0, min(float(item.get("score_awarded") or 0), full_score))
            class_name = str(item.get("class_name") or "未分班").strip() or "未分班"
            student_name = _student_label(item)
            for kid in _knowledge_ids_from_detail(item):
                label = _clean_knowledge_label(kid, knowledge_label_map.get(kid, ""))
                key = (class_name, label or "未命名知识点")
                bucket = buckets.setdefault(key, {"score_sum": 0.0, "full_sum": 0.0, "questions": set()})
                bucket["score_sum"] = float(bucket["score_sum"]) + awarded
                bucket["full_sum"] = float(bucket["full_sum"]) + full_score
                questions = bucket["questions"]
                if isinstance(questions, set):
                    questions.add(qid)
                if awarded < full_score - 1e-6:
                    lost_students.setdefault(key, set()).add(student_name)

        rows: list[dict[str, object]] = []
        for (class_name, label), bucket in buckets.items():
            score_sum = float(bucket["score_sum"])
            full_sum = float(bucket["full_sum"])
            questions = bucket["questions"] if isinstance(bucket["questions"], set) else set()
            rows.append(
                {
                    "班级": class_name,
                    "知识点": label,
                    "涉及题目": "、".join(_natural_question_order([str(q) for q in questions])),
                    "累计得分": round(score_sum, 2),
                    "累计满分": round(full_sum, 2),
                    "得分率": round(score_sum / full_sum * 100, 2) if full_sum > 0 else 0,
                    "失分人数": len(lost_students.get((class_name, label), set())),
                }
            )
        return pd.DataFrame(rows, columns=columns).sort_values(by=["班级", "得分率", "知识点"], kind="stable")

    def _build_knowledge_summary(self, df_details: pd.DataFrame) -> pd.DataFrame:
        if df_details.empty:
            return pd.DataFrame(columns=["knowledge_id", "平均得分", "最低得分", "最高得分", "答题条目数"])

        agg = (
            df_details.groupby("knowledge_id")["score_awarded"]
            .agg(["mean", "min", "max", "count"])
            .reset_index()
        )
        agg.columns = ["knowledge_id", "平均得分", "最低得分", "最高得分", "答题条目数"]
        agg["平均得分"] = agg["平均得分"].round(2)
        return agg.sort_values(by=["平均得分", "knowledge_id"], ascending=[True, True])

    def _build_session_weak_points(self, df_details: pd.DataFrame) -> pd.DataFrame:
        if df_details.empty:
            return pd.DataFrame(
                columns=[
                    "student_code",
                    "student_name",
                    "knowledge_id",
                    "avg_score",
                    "deduction_count",
                    "item_count",
                    "sample_reasons",
                ]
            )

        grouped = (
            df_details.groupby(["student_code", "student_name", "knowledge_id"])
            .agg(
                avg_score=("score_awarded", "mean"),
                deduction_count=("deduction_reason", lambda s: int((s.fillna("").str.strip() != "").sum())),
                item_count=("question_id", "count"),
                sample_reasons=(
                    "deduction_reason",
                    lambda s: "；".join(sorted({x.strip() for x in s.dropna().astype(str) if x.strip()}))
                ),
            )
            .reset_index()
        )

        grouped["avg_score"] = grouped["avg_score"].round(2)
        grouped = grouped.sort_values(by=["avg_score", "deduction_count", "knowledge_id"], ascending=[True, False, True])
        grouped.rename(
            columns={
                "student_code": "学号",
                "student_name": "学生姓名",
                "knowledge_id": "知识点ID",
                "avg_score": "平均得分",
                "deduction_count": "失分条目数",
                "item_count": "答题条目数",
                "sample_reasons": "典型扣分原因",
            },
            inplace=True,
        )
        return grouped


def _natural_question_order(question_ids: list[str]) -> list[str]:
    import re

    def key(value: str) -> tuple[int, str]:
        match = re.search(r"\d+", value)
        number = int(match.group(0)) if match else 10**9
        return number, value

    return sorted(question_ids, key=key)


def _normalize_question_detail_records(
    records: list[dict[str, object]],
    score_map: dict[str, float],
) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {}
    order: list[tuple[str, str]] = []
    for index, record in enumerate(records):
        raw_qid = str(record.get("question_id") or "").strip()
        if not raw_qid:
            continue
        qid = _canonical_question_id_for_score(raw_qid, score_map)
        result_key = str(
            record.get("result_id")
            or record.get("student_code")
            or record.get("student_name")
            or index
        )
        key = (result_key, qid)
        if key not in merged:
            item = dict(record)
            item["question_id"] = qid
            item["score_awarded"] = 0.0
            item["_deduction_reasons"] = []
            item["_error_categories"] = []
            item["_error_summaries"] = []
            item["_knowledge_ids"] = []
            merged[key] = item
            order.append(key)

        item = merged[key]
        item["score_awarded"] = float(item.get("score_awarded") or 0) + float(record.get("score_awarded") or 0)
        for field, private_field in (
            ("deduction_reason", "_deduction_reasons"),
            ("error_category", "_error_categories"),
            ("error_summary", "_error_summaries"),
        ):
            value = str(record.get(field) or "").strip()
            if value:
                item[private_field].append(value)
        item["_knowledge_ids"].extend(kid for kid in _knowledge_ids_from_detail(record) if kid != "UNKNOWN")

    normalized: list[dict[str, object]] = []
    for key in order:
        item = merged[key]
        qid = str(item.get("question_id") or "")
        full_score = float(score_map.get(qid) or 0)
        if full_score > 0 and float(item.get("score_awarded") or 0) > full_score:
            item["score_awarded"] = full_score

        for private_field, public_field in (
            ("_deduction_reasons", "deduction_reason"),
            ("_error_categories", "error_category"),
            ("_error_summaries", "error_summary"),
        ):
            values = _unique_texts(item.pop(private_field, []))
            if values:
                item[public_field] = "；".join(values)

        knowledge_ids = _unique_texts(item.pop("_knowledge_ids", []))
        if knowledge_ids:
            item["knowledge_id"] = knowledge_ids[0]
            item["knowledge_ids"] = json.dumps(knowledge_ids, ensure_ascii=False)
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


def _question_parent_id(question_id: str) -> str | None:
    import re

    match = re.match(r"^(Q\d+)(?:\(|（|-)", question_id.strip())
    return match.group(1) if match else None


def _unique_texts(values: list[object]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _format_score(value) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:g}"


def _student_label(item: dict) -> str:
    name = str(item.get("student_name") or "").strip()
    code = str(item.get("student_code") or "").strip()
    if code and name:
        return f"{name}({code})"
    return name or code or "未知学生"


def _is_solution_type(qtype: str) -> bool:
    return str(qtype or "").strip().lower() in {
        "proof",
        "calculation",
        "comprehensive",
        "solution",
        "general_solution",
    }


def _student_label(item: dict) -> str:
    name = str(item.get("student_name") or "").strip()
    code = str(item.get("student_code") or "").strip()
    return name or code or "未知学生"


def _knowledge_ids_from_detail(item: dict) -> list[str]:
    raw = item.get("knowledge_ids")
    values: list[str] = []
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        if isinstance(parsed, list):
            values.extend(str(value).strip() for value in parsed if str(value).strip())
        else:
            values.extend(_split_knowledge_text(str(parsed)))
    elif isinstance(raw, list):
        values.extend(str(value).strip() for value in raw if str(value).strip())
    fallback = str(item.get("knowledge_id") or "").strip()
    if fallback:
        values.append(fallback)

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result or ["UNKNOWN"]


def _split_knowledge_text(value: str) -> list[str]:
    return [
        part.strip()
        for part in value.replace("；", ",").replace(";", ",").replace("|", ",").replace("、", ",").split(",")
        if part.strip()
    ]


def _clean_knowledge_label(knowledge_id: str, label: object) -> str:
    import re

    text = str(label or "").strip()
    kid = str(knowledge_id or "").strip()
    if kid and text.startswith(kid):
        text = text[len(kid):].strip()
        text = re.sub(r"^[\s:：\-|·、]+", "", text).strip()
    if not text or text == kid or re.fullmatch(r"[A-Za-z]+\d*_\d+", text):
        return "未命名知识点"
    return text


def _summarize_error_categories(items: list[dict], full_score: float) -> str:
    counts: dict[str, int] = {}
    for item in items:
        awarded = float(item.get("score_awarded") or 0)
        if full_score > 0 and awarded >= full_score - 1e-6:
            continue
        category = str(item.get("error_category") or "").strip()
        summary = str(item.get("error_summary") or "").strip()
        text = category or summary
        if not text:
            continue
        counts[text] = counts.get(text, 0) + 1
    return "；".join(f"{key}×{value}" for key, value in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5])
