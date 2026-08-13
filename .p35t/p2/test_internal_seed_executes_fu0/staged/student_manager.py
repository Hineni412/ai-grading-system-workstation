from __future__ import annotations

import io
from pathlib import Path

import pandas as pd

from db_manager import StudentRecord


REQUIRED_FIELDS = {
    "student_code": ["student_code", "学号", "考号", "id", "编号"],
    "name": ["name", "姓名", "学生姓名"],
    "class_name": ["class_name", "班级", "班别"],
}


class StudentManager:
    @staticmethod
    def load_students(file_name: str, file_bytes: bytes) -> list[StudentRecord]:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
        elif suffix in {".xlsx", ".xls"}:
            df = pd.read_excel(io.BytesIO(file_bytes))
        else:
            raise ValueError("仅支持 CSV / Excel 文件")

        mapped = StudentManager._standardize_columns(df)
        records: list[StudentRecord] = []

        for _, row in mapped.iterrows():
            student_code = str(row.get("student_code", "")).strip()
            name = str(row.get("name", "")).strip()
            class_name = str(row.get("class_name", "")).strip()

            if not student_code or not name or student_code.lower() == "nan" or name.lower() == "nan":
                continue

            records.append(
                StudentRecord(
                    student_code=student_code,
                    name=name,
                    class_name=class_name if class_name and class_name.lower() != "nan" else None,
                )
            )

        if not records:
            raise ValueError("未读取到有效学生数据，请检查文件字段")

        return StudentManager._deduplicate(records)

    @staticmethod
    def _standardize_columns(df: pd.DataFrame) -> pd.DataFrame:
        rename_map: dict[str, str] = {}
        lower_columns = {str(col).strip().lower(): str(col) for col in df.columns}

        for target, aliases in REQUIRED_FIELDS.items():
            found_col = None
            for alias in aliases:
                if alias.lower() in lower_columns:
                    found_col = lower_columns[alias.lower()]
                    break
            if found_col is not None:
                rename_map[found_col] = target

        standardized = df.rename(columns=rename_map)

        if "student_code" not in standardized.columns or "name" not in standardized.columns:
            raise ValueError("学生名单必须包含学号(student_code)和姓名(name)字段")

        if "class_name" not in standardized.columns:
            standardized["class_name"] = None

        return standardized[["student_code", "name", "class_name"]]

    @staticmethod
    def _deduplicate(records: list[StudentRecord]) -> list[StudentRecord]:
        unique: dict[str, StudentRecord] = {}
        for record in records:
            unique[record.student_code] = record
        return list(unique.values())
