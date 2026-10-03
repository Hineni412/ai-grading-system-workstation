"""Render anonymous comparison HTML; question bodies remain available only in place."""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def build(directory):
    data = (directory / "comparison.json").read_text(encoding="utf-8")
    report = json.loads(data)
    assert report["input"]["same_student_scores"] and report["input"]["same_recent_history"]
    assert report["input"]["same_read_versions"]
    assert report["model_requests"] == report["database_writes"] == report["raw_student_exports"] == 0
    for scope in report["scopes"]:
        assert all(scope["native_parity"])
        for paper in scope["papers"]:
            old, new = [set(item["id"] for item in paper[key]["items"]) for key in ("before", "after")]
            assert set(paper["retained_questions"]) == old & new
            assert set(paper["removed_questions"]) == old - new
            assert set(paper["added_questions"]) == new - old
            assert len(paper["after"]["covered"]) + len(paper["after"]["gaps"]) == paper["current_need_count"]
    forbidden = {"student_id", "student_name", "student_code", "class_id", "score_rate", "score_awarded",
                 "full_score", "source_question_refs", "observable_evidence", "answer_anchor", "stem_text", "solution"}
    def verify(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value), "Raw source field reached report"
            for item in value.values(): verify(item)
        elif isinstance(value, list):
            for item in value: verify(item)
    verify(report)
    template = Path(__file__).with_name("training_endpoint_report_template.html").read_text(encoding="utf-8")
    escaped = data.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    html = template.replace("__DATA__", escaped)
    assert "__DATA__" not in html and "REPORT_GROUP_METHOD" not in html
    destination = directory / "training-comparison.html"
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        for offset in range(0, len(html), 5000): stream.write(html[offset:offset+5000])
    print(json.dumps({"report": destination.name, "bytes": destination.stat().st_size,
                      "paired_papers": sum(len(scope["papers"]) for scope in report["scopes"]),
                      "raw_fields_absent": True}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    build(parser.parse_args().directory)
