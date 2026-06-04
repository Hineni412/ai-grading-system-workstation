from __future__ import annotations

import pandas as pd

from db_manager import DBManager


class AnalyticsService:
    def __init__(self, db_manager: DBManager) -> None:
        self.db = db_manager

    def weak_points_dataframe(self, session_id: int, student_id: int | None = None) -> pd.DataFrame:
        rows = self.db.get_session_weak_points(session_id=session_id, student_id=student_id)
        return self._weak_points_dataframe_from_rows(rows)

    def global_weak_points_dataframe(self, student_id: int | None = None) -> pd.DataFrame:
        rows = self.db.get_active_global_weak_points(student_id=student_id)
        return self._weak_points_dataframe_from_rows(rows)

    def _weak_points_dataframe_from_rows(self, rows: list[dict]) -> pd.DataFrame:
        if not rows:
            return pd.DataFrame(
                columns=[
                    "student_code",
                    "student_name",
                    "class_name",
                    "knowledge_id",
                    "knowledge_label",
                    "avg_score",
                    "score_sum",
                    "full_score_sum",
                    "weighted_score_rate",
                    "deduction_count",
                    "item_count",
                    "exam_count",
                    "sample_reasons",
                ]
            )
        df = pd.DataFrame(rows)
        if "knowledge_label" in df.columns:
            df["knowledge_label"] = df["knowledge_label"].fillna("").astype(str).str.replace(
                r"^[A-Za-z]+\d*_\d+\s*[:：·、\-\|]?\s*",
                "",
                regex=True,
            )
        if "knowledge_id" in df.columns:
            df.drop(columns=["knowledge_id"], inplace=True)
        df.rename(
            columns={
                "student_code": "学号",
                "student_name": "学生姓名",
                "class_name": "班级",
                "knowledge_id": "知识点ID",
                "knowledge_label": "知识点",
                "avg_score": "平均得分",
                "score_sum": "累计得分",
                "full_score_sum": "累计满分",
                "weighted_score_rate": "知识点得分率",
                "deduction_count": "失分条目数",
                "item_count": "答题条目数",
                "exam_count": "涉及考试数",
                "sample_reasons": "典型扣分原因",
            },
            inplace=True,
        )
        return df

    def result_dataframe(self, session_id: int) -> pd.DataFrame:
        rows = self.db.get_session_results(session_id)
        if not rows:
            return pd.DataFrame(
                columns=["student_code", "student_name", "class_name", "student_score", "total_score", "needs_human_review"]
            )
        df = pd.DataFrame(rows)
        df["score_rate"] = (df["student_score"] / df["total_score"] * 100).round(2)
        return df
