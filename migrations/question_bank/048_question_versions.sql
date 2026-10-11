-- Per-question versions: bump question_versions per affected question id,
-- bump data_versions('materials_global') for shared/non-question inputs.
-- Generated from QUESTION_BANK_VERSION_* constants in
-- integration/data_generation.py (single source; tests verify parity).

CREATE TABLE IF NOT EXISTS question_versions (
    question_id INTEGER PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0
);

INSERT OR IGNORE INTO data_versions (domain, version) VALUES ('materials_global', 0);

CREATE TRIGGER IF NOT EXISTS qv_questions_ins AFTER INSERT ON questions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.id, 1 WHERE NEW.id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_questions_upd AFTER UPDATE ON questions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.id, 1 WHERE OLD.id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.id, 1 WHERE NEW.id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_questions_del AFTER DELETE ON questions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.id, 1 WHERE OLD.id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_tags_ins AFTER INSERT ON question_tags FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_tags_upd AFTER UPDATE ON question_tags FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_tags_del AFTER DELETE ON question_tags FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_solution_evidence_versions_ins AFTER INSERT ON question_solution_evidence_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_solution_evidence_versions_upd AFTER UPDATE ON question_solution_evidence_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_solution_evidence_versions_del AFTER DELETE ON question_solution_evidence_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_evidence_point_knowledge_links_ins AFTER INSERT ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_evidence_point_knowledge_links_upd AFTER UPDATE ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_evidence_point_knowledge_links_del AFTER DELETE ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_heads_ins AFTER INSERT ON training_criterion_heads FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_heads_upd AFTER UPDATE ON training_criterion_heads FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_heads_del AFTER DELETE ON training_criterion_heads FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_versions_ins AFTER INSERT ON training_criterion_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_versions_upd AFTER UPDATE ON training_criterion_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_versions_del AFTER DELETE ON training_criterion_versions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_events_ins AFTER INSERT ON training_criterion_events FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_events_upd AFTER UPDATE ON training_criterion_events FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_events_del AFTER DELETE ON training_criterion_events FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_items_ins AFTER INSERT ON question_analysis_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_items_upd AFTER UPDATE ON question_analysis_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_items_del AFTER DELETE ON question_analysis_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_index_ins AFTER INSERT ON question_content_index FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_index_upd AFTER UPDATE ON question_content_index FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_index_del AFTER DELETE ON question_content_index FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_revisions_ins AFTER INSERT ON question_content_revisions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_revisions_upd AFTER UPDATE ON question_content_revisions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_content_revisions_del AFTER DELETE ON question_content_revisions FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_previews_ins AFTER INSERT ON question_previews FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_previews_upd AFTER UPDATE ON question_previews FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_previews_del AFTER DELETE ON question_previews FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_error_patterns_ins AFTER INSERT ON question_error_patterns FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_error_patterns_upd AFTER UPDATE ON question_error_patterns FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_error_patterns_del AFTER DELETE ON question_error_patterns FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_part_difficulty_features_ins AFTER INSERT ON question_part_difficulty_features FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_part_difficulty_features_upd AFTER UPDATE ON question_part_difficulty_features FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_part_difficulty_features_del AFTER DELETE ON question_part_difficulty_features FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_scope_summary_ins AFTER INSERT ON question_scope_summary FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_scope_summary_upd AFTER UPDATE ON question_scope_summary FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_scope_summary_del AFTER DELETE ON question_scope_summary FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_paper_question_occurrences_ins AFTER INSERT ON paper_question_occurrences FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_paper_question_occurrences_upd AFTER UPDATE ON paper_question_occurrences FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_paper_question_occurrences_del AFTER DELETE ON paper_question_occurrences FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_taxonomy_review_applications_ins AFTER INSERT ON taxonomy_review_applications FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_taxonomy_review_applications_upd AFTER UPDATE ON taxonomy_review_applications FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_taxonomy_review_applications_del AFTER DELETE ON taxonomy_review_applications FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_items_ins AFTER INSERT ON training_criterion_backfill_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_items_upd AFTER UPDATE ON training_criterion_backfill_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_items_del AFTER DELETE ON training_criterion_backfill_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_duplicate_links_ins AFTER INSERT ON question_duplicate_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.duplicate_of_question_id, 1 WHERE NEW.duplicate_of_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_duplicate_links_upd AFTER UPDATE ON question_duplicate_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.duplicate_of_question_id, 1 WHERE OLD.duplicate_of_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.question_id, 1 WHERE NEW.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.duplicate_of_question_id, 1 WHERE NEW.duplicate_of_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_duplicate_links_del AFTER DELETE ON question_duplicate_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.question_id, 1 WHERE OLD.question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.duplicate_of_question_id, 1 WHERE OLD.duplicate_of_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_grading_question_links_ins AFTER INSERT ON grading_question_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.bank_question_id, 1 WHERE NEW.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_grading_question_links_upd AFTER UPDATE ON grading_question_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.bank_question_id, 1 WHERE OLD.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.bank_question_id, 1 WHERE NEW.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_grading_question_links_del AFTER DELETE ON grading_question_links FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.bank_question_id, 1 WHERE OLD.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_items_ins AFTER INSERT ON question_document_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.bank_question_id, 1 WHERE NEW.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_items_upd AFTER UPDATE ON question_document_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.bank_question_id, 1 WHERE OLD.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.bank_question_id, 1 WHERE NEW.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_items_del AFTER DELETE ON question_document_items FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.bank_question_id, 1 WHERE OLD.bank_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_authoring_works_ins AFTER INSERT ON authoring_works FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.source_question_id, 1 WHERE NEW.source_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_authoring_works_upd AFTER UPDATE ON authoring_works FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.source_question_id, 1 WHERE OLD.source_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT NEW.source_question_id, 1 WHERE NEW.source_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_authoring_works_del AFTER DELETE ON authoring_works FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT OLD.source_question_id, 1 WHERE OLD.source_question_id IS NOT NULL AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_requests_ins AFTER INSERT ON question_analysis_requests FOR EACH ROW WHEN json_valid(NEW.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_analysis_requests_ins AFTER INSERT ON question_analysis_requests FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_requests_upd AFTER UPDATE ON question_analysis_requests FOR EACH ROW WHEN json_valid(NEW.question_ids_json) AND json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_analysis_requests_upd AFTER UPDATE ON question_analysis_requests FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) OR NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_question_analysis_requests_del AFTER DELETE ON question_analysis_requests FOR EACH ROW WHEN json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_analysis_requests_del AFTER DELETE ON question_analysis_requests FOR EACH ROW WHEN NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_runs_ins AFTER INSERT ON training_criterion_backfill_runs FOR EACH ROW WHEN json_valid(NEW.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_training_criterion_backfill_runs_ins AFTER INSERT ON training_criterion_backfill_runs FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_runs_upd AFTER UPDATE ON training_criterion_backfill_runs FOR EACH ROW WHEN json_valid(NEW.question_ids_json) AND json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_training_criterion_backfill_runs_upd AFTER UPDATE ON training_criterion_backfill_runs FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) OR NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_training_criterion_backfill_runs_del AFTER DELETE ON training_criterion_backfill_runs FOR EACH ROW WHEN json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_training_criterion_backfill_runs_del AFTER DELETE ON training_criterion_backfill_runs FOR EACH ROW WHEN NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_publications_ins AFTER INSERT ON question_document_publications FOR EACH ROW WHEN json_valid(NEW.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_document_publications_ins AFTER INSERT ON question_document_publications FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_publications_upd AFTER UPDATE ON question_document_publications FOR EACH ROW WHEN json_valid(NEW.question_ids_json) AND json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(NEW.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_document_publications_upd AFTER UPDATE ON question_document_publications FOR EACH ROW WHEN NOT json_valid(NEW.question_ids_json) OR NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_question_document_publications_del AFTER DELETE ON question_document_publications FOR EACH ROW WHEN json_valid(OLD.question_ids_json) BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT CAST(value AS INTEGER), 1 FROM json_each(OLD.question_ids_json) WHERE true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qvg_question_document_publications_del AFTER DELETE ON question_document_publications FOR EACH ROW WHEN NOT json_valid(OLD.question_ids_json) BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS qv_papers_ins AFTER INSERT ON papers FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT id, 1 FROM questions WHERE paper_id = NEW.id AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_papers_upd AFTER UPDATE ON papers FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT id, 1 FROM questions WHERE paper_id = OLD.id AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
    INSERT INTO question_versions (question_id, version)
        SELECT id, 1 FROM questions WHERE paper_id = NEW.id AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS qv_papers_del AFTER DELETE ON papers FOR EACH ROW BEGIN
    INSERT INTO question_versions (question_id, version)
        SELECT id, 1 FROM questions WHERE paper_id = OLD.id AND true
        ON CONFLICT(question_id) DO UPDATE SET version = version + 1;
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_assets_ins AFTER INSERT ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_assets_upd AFTER UPDATE ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_assets_del AFTER DELETE ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_reviews_ins AFTER INSERT ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_reviews_upd AFTER UPDATE ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_reviews_del AFTER DELETE ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_work_versions_ins AFTER INSERT ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_work_versions_upd AFTER UPDATE ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_authoring_work_versions_del AFTER DELETE ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mapping_events_ins AFTER INSERT ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mapping_events_upd AFTER UPDATE ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mapping_events_del AFTER DELETE ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mappings_ins AFTER INSERT ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mappings_upd AFTER UPDATE ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_fine_term_core_mappings_del AFTER DELETE ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_fine_term_dispositions_ins AFTER INSERT ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_fine_term_dispositions_upd AFTER UPDATE ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_fine_term_dispositions_del AFTER DELETE ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_node_profiles_ins AFTER INSERT ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_node_profiles_upd AFTER UPDATE ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_node_profiles_del AFTER DELETE ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_events_ins AFTER INSERT ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_events_upd AFTER UPDATE ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_events_del AFTER DELETE ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_mappings_ins AFTER INSERT ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_mappings_upd AFTER UPDATE ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_mappings_del AFTER DELETE ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_relations_ins AFTER INSERT ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_relations_upd AFTER UPDATE ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_release_relations_del AFTER DELETE ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_releases_ins AFTER INSERT ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_releases_upd AFTER UPDATE ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_graph_releases_del AFTER DELETE ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_identity_replacements_ins AFTER INSERT ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_identity_replacements_upd AFTER UPDATE ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_identity_replacements_del AFTER DELETE ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_amendment_events_ins AFTER INSERT ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_amendment_events_upd AFTER UPDATE ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_amendment_events_del AFTER DELETE ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_audit_events_ins AFTER INSERT ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_audit_events_upd AFTER UPDATE ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_audit_events_del AFTER DELETE ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_evidence_ins AFTER INSERT ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_evidence_upd AFTER UPDATE ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_evidence_del AFTER DELETE ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_items_ins AFTER INSERT ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_items_upd AFTER UPDATE ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_items_del AFTER DELETE ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_requests_ins AFTER INSERT ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_requests_upd AFTER UPDATE ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_requests_del AFTER DELETE ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_runs_ins AFTER INSERT ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_runs_upd AFTER UPDATE ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relation_suggestion_runs_del AFTER DELETE ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relations_ins AFTER INSERT ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relations_upd AFTER UPDATE ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_relations_del AFTER DELETE ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_aliases_ins AFTER INSERT ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_aliases_upd AFTER UPDATE ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_aliases_del AFTER DELETE ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identities_ins AFTER INSERT ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identities_upd AFTER UPDATE ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identities_del AFTER DELETE ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identity_mappings_ins AFTER INSERT ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identity_mappings_upd AFTER UPDATE ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_knowledge_tag_identity_mappings_del AFTER DELETE ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_analysis_operations_ins AFTER INSERT ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_analysis_operations_upd AFTER UPDATE ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_analysis_operations_del AFTER DELETE ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_document_publication_outbox_ins AFTER INSERT ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_document_publication_outbox_upd AFTER UPDATE ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;

CREATE TRIGGER IF NOT EXISTS dvg_question_document_publication_outbox_del AFTER DELETE ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials_global';
END;
