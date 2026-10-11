-- 读缓存数据版本：按数据类别维护版本号（data_versions）。

-- 阅卷库只有一类 results；jobs 与 workspace_ai_* 簿记不触发版本，

-- 任务进度写入不再使页面读缓存失效。

CREATE TABLE IF NOT EXISTS data_versions (
    domain  TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0
);



INSERT OR IGNORE INTO data_versions (domain, version) VALUES ('results', 0);



CREATE TRIGGER IF NOT EXISTS dv_annotated_results_ins AFTER INSERT ON annotated_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_annotated_results_upd AFTER UPDATE ON annotated_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_annotated_results_del AFTER DELETE ON annotated_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_answer_regions_ins AFTER INSERT ON answer_regions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_answer_regions_upd AFTER UPDATE ON answer_regions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_answer_regions_del AFTER DELETE ON answer_regions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_app_settings_ins AFTER INSERT ON app_settings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_app_settings_upd AFTER UPDATE ON app_settings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_app_settings_del AFTER DELETE ON app_settings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_exam_papers_ins AFTER INSERT ON exam_papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_exam_papers_upd AFTER UPDATE ON exam_papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_exam_papers_del AFTER DELETE ON exam_papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_run_items_ins AFTER INSERT ON grading_run_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_run_items_upd AFTER UPDATE ON grading_run_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_run_items_del AFTER DELETE ON grading_run_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_runs_ins AFTER INSERT ON grading_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_runs_upd AFTER UPDATE ON grading_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_runs_del AFTER DELETE ON grading_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_sessions_ins AFTER INSERT ON grading_sessions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_sessions_upd AFTER UPDATE ON grading_sessions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_sessions_del AFTER DELETE ON grading_sessions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_attendance_ins AFTER INSERT ON session_attendance FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_attendance_upd AFTER UPDATE ON session_attendance FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_attendance_del AFTER DELETE ON session_attendance FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_details_ins AFTER INSERT ON session_details FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_details_upd AFTER UPDATE ON session_details FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_details_del AFTER DELETE ON session_details FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_results_ins AFTER INSERT ON session_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_results_upd AFTER UPDATE ON session_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_results_del AFTER DELETE ON session_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_templates_ins AFTER INSERT ON session_templates FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_templates_upd AFTER UPDATE ON session_templates FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_session_templates_del AFTER DELETE ON session_templates FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_students_ins AFTER INSERT ON students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_students_upd AFTER UPDATE ON students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_students_del AFTER DELETE ON students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_teacher_score_locks_ins AFTER INSERT ON teacher_score_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_teacher_score_locks_upd AFTER UPDATE ON teacher_score_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;

CREATE TRIGGER IF NOT EXISTS dv_teacher_score_locks_del AFTER DELETE ON teacher_score_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'results';
END;
