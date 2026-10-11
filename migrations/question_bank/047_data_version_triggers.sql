-- 读缓存数据版本：按数据类别维护版本号（data_versions）。

-- materials＝题库正文与知识/判读资料；training_records＝训练证据与掌握度；

-- training_ops＝训练草稿、卷面实例、扫描与评估过程。作业进度与派生

-- 缓存表（frequency/fingerprints/receipts）不触发版本。

CREATE TABLE IF NOT EXISTS data_versions (
    domain  TEXT PRIMARY KEY,
    version INTEGER NOT NULL DEFAULT 0
);



INSERT OR IGNORE INTO data_versions (domain, version) VALUES
    ('materials', 0), ('training_records', 0), ('training_ops', 0);



CREATE TRIGGER IF NOT EXISTS dv_authoring_assets_ins AFTER INSERT ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_assets_upd AFTER UPDATE ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_assets_del AFTER DELETE ON authoring_assets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_reviews_ins AFTER INSERT ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_reviews_upd AFTER UPDATE ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_reviews_del AFTER DELETE ON authoring_reviews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_work_versions_ins AFTER INSERT ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_work_versions_upd AFTER UPDATE ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_work_versions_del AFTER DELETE ON authoring_work_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_works_ins AFTER INSERT ON authoring_works FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_works_upd AFTER UPDATE ON authoring_works FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_authoring_works_del AFTER DELETE ON authoring_works FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_evidence_point_knowledge_links_ins AFTER INSERT ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_evidence_point_knowledge_links_upd AFTER UPDATE ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_evidence_point_knowledge_links_del AFTER DELETE ON evidence_point_knowledge_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mapping_events_ins AFTER INSERT ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mapping_events_upd AFTER UPDATE ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mapping_events_del AFTER DELETE ON fine_term_core_mapping_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mappings_ins AFTER INSERT ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mappings_upd AFTER UPDATE ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_fine_term_core_mappings_del AFTER DELETE ON fine_term_core_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_question_links_ins AFTER INSERT ON grading_question_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_question_links_upd AFTER UPDATE ON grading_question_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_grading_question_links_del AFTER DELETE ON grading_question_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_fine_term_dispositions_ins AFTER INSERT ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_fine_term_dispositions_upd AFTER UPDATE ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_fine_term_dispositions_del AFTER DELETE ON knowledge_graph_fine_term_dispositions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_node_profiles_ins AFTER INSERT ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_node_profiles_upd AFTER UPDATE ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_node_profiles_del AFTER DELETE ON knowledge_graph_node_profiles FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_events_ins AFTER INSERT ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_events_upd AFTER UPDATE ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_events_del AFTER DELETE ON knowledge_graph_release_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_mappings_ins AFTER INSERT ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_mappings_upd AFTER UPDATE ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_mappings_del AFTER DELETE ON knowledge_graph_release_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_relations_ins AFTER INSERT ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_relations_upd AFTER UPDATE ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_release_relations_del AFTER DELETE ON knowledge_graph_release_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_releases_ins AFTER INSERT ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_releases_upd AFTER UPDATE ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_graph_releases_del AFTER DELETE ON knowledge_graph_releases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_identity_replacements_ins AFTER INSERT ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_identity_replacements_upd AFTER UPDATE ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_identity_replacements_del AFTER DELETE ON knowledge_identity_replacements FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_amendment_events_ins AFTER INSERT ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_amendment_events_upd AFTER UPDATE ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_amendment_events_del AFTER DELETE ON knowledge_relation_amendment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_audit_events_ins AFTER INSERT ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_audit_events_upd AFTER UPDATE ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_audit_events_del AFTER DELETE ON knowledge_relation_audit_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_evidence_ins AFTER INSERT ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_evidence_upd AFTER UPDATE ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_evidence_del AFTER DELETE ON knowledge_relation_evidence FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_items_ins AFTER INSERT ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_items_upd AFTER UPDATE ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_items_del AFTER DELETE ON knowledge_relation_suggestion_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_requests_ins AFTER INSERT ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_requests_upd AFTER UPDATE ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_requests_del AFTER DELETE ON knowledge_relation_suggestion_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_runs_ins AFTER INSERT ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_runs_upd AFTER UPDATE ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relation_suggestion_runs_del AFTER DELETE ON knowledge_relation_suggestion_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relations_ins AFTER INSERT ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relations_upd AFTER UPDATE ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_relations_del AFTER DELETE ON knowledge_relations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_aliases_ins AFTER INSERT ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_aliases_upd AFTER UPDATE ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_aliases_del AFTER DELETE ON knowledge_tag_aliases FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identities_ins AFTER INSERT ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identities_upd AFTER UPDATE ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identities_del AFTER DELETE ON knowledge_tag_identities FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identity_mappings_ins AFTER INSERT ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identity_mappings_upd AFTER UPDATE ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_knowledge_tag_identity_mappings_del AFTER DELETE ON knowledge_tag_identity_mappings FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluation_items_ins AFTER INSERT ON mastery_v2_evaluation_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluation_items_upd AFTER UPDATE ON mastery_v2_evaluation_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluation_items_del AFTER DELETE ON mastery_v2_evaluation_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluations_ins AFTER INSERT ON mastery_v2_evaluations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluations_upd AFTER UPDATE ON mastery_v2_evaluations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_evaluations_del AFTER DELETE ON mastery_v2_evaluations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_parameter_versions_ins AFTER INSERT ON mastery_v2_parameter_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_parameter_versions_upd AFTER UPDATE ON mastery_v2_parameter_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_parameter_versions_del AFTER DELETE ON mastery_v2_parameter_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_events_ins AFTER INSERT ON mastery_v2_rollout_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_events_upd AFTER UPDATE ON mastery_v2_rollout_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_events_del AFTER DELETE ON mastery_v2_rollout_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_state_ins AFTER INSERT ON mastery_v2_rollout_state FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_state_upd AFTER UPDATE ON mastery_v2_rollout_state FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_rollout_state_del AFTER DELETE ON mastery_v2_rollout_state FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_spot_checks_ins AFTER INSERT ON mastery_v2_spot_checks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_spot_checks_upd AFTER UPDATE ON mastery_v2_spot_checks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_mastery_v2_spot_checks_del AFTER DELETE ON mastery_v2_spot_checks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_paper_question_occurrences_ins AFTER INSERT ON paper_question_occurrences FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_paper_question_occurrences_upd AFTER UPDATE ON paper_question_occurrences FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_paper_question_occurrences_del AFTER DELETE ON paper_question_occurrences FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_papers_ins AFTER INSERT ON papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_papers_upd AFTER UPDATE ON papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_papers_del AFTER DELETE ON papers FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batch_items_ins AFTER INSERT ON personalized_paper_batch_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batch_items_upd AFTER UPDATE ON personalized_paper_batch_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batch_items_del AFTER DELETE ON personalized_paper_batch_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batches_ins AFTER INSERT ON personalized_paper_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batches_upd AFTER UPDATE ON personalized_paper_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_batches_del AFTER DELETE ON personalized_paper_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_events_ins AFTER INSERT ON personalized_paper_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_events_upd AFTER UPDATE ON personalized_paper_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_events_del AFTER DELETE ON personalized_paper_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_instances_ins AFTER INSERT ON personalized_paper_instances FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_instances_upd AFTER UPDATE ON personalized_paper_instances FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_instances_del AFTER DELETE ON personalized_paper_instances FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_items_ins AFTER INSERT ON personalized_paper_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_items_upd AFTER UPDATE ON personalized_paper_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_items_del AFTER DELETE ON personalized_paper_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_pages_ins AFTER INSERT ON personalized_paper_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_pages_upd AFTER UPDATE ON personalized_paper_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_paper_pages_del AFTER DELETE ON personalized_paper_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_drafts_ins AFTER INSERT ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_drafts_upd AFTER UPDATE ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_drafts_del AFTER DELETE ON personalized_recommendation_drafts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_events_ins AFTER INSERT ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_events_upd AFTER UPDATE ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_personalized_recommendation_events_del AFTER DELETE ON personalized_recommendation_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_items_ins AFTER INSERT ON question_analysis_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_items_upd AFTER UPDATE ON question_analysis_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_items_del AFTER DELETE ON question_analysis_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_operations_ins AFTER INSERT ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_operations_upd AFTER UPDATE ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_operations_del AFTER DELETE ON question_analysis_operations FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_requests_ins AFTER INSERT ON question_analysis_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_requests_upd AFTER UPDATE ON question_analysis_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_analysis_requests_del AFTER DELETE ON question_analysis_requests FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_index_ins AFTER INSERT ON question_content_index FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_index_upd AFTER UPDATE ON question_content_index FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_index_del AFTER DELETE ON question_content_index FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_revisions_ins AFTER INSERT ON question_content_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_revisions_upd AFTER UPDATE ON question_content_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_content_revisions_del AFTER DELETE ON question_content_revisions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_items_ins AFTER INSERT ON question_document_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_items_upd AFTER UPDATE ON question_document_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_items_del AFTER DELETE ON question_document_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publication_outbox_ins AFTER INSERT ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publication_outbox_upd AFTER UPDATE ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publication_outbox_del AFTER DELETE ON question_document_publication_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publications_ins AFTER INSERT ON question_document_publications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publications_upd AFTER UPDATE ON question_document_publications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_document_publications_del AFTER DELETE ON question_document_publications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_duplicate_links_ins AFTER INSERT ON question_duplicate_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_duplicate_links_upd AFTER UPDATE ON question_duplicate_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_duplicate_links_del AFTER DELETE ON question_duplicate_links FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_error_patterns_ins AFTER INSERT ON question_error_patterns FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_error_patterns_upd AFTER UPDATE ON question_error_patterns FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_error_patterns_del AFTER DELETE ON question_error_patterns FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_part_difficulty_features_ins AFTER INSERT ON question_part_difficulty_features FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_part_difficulty_features_upd AFTER UPDATE ON question_part_difficulty_features FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_part_difficulty_features_del AFTER DELETE ON question_part_difficulty_features FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_previews_ins AFTER INSERT ON question_previews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_previews_upd AFTER UPDATE ON question_previews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_previews_del AFTER DELETE ON question_previews FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_scope_summary_ins AFTER INSERT ON question_scope_summary FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_scope_summary_upd AFTER UPDATE ON question_scope_summary FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_scope_summary_del AFTER DELETE ON question_scope_summary FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_solution_evidence_versions_ins AFTER INSERT ON question_solution_evidence_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_solution_evidence_versions_upd AFTER UPDATE ON question_solution_evidence_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_solution_evidence_versions_del AFTER DELETE ON question_solution_evidence_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_tags_ins AFTER INSERT ON question_tags FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_tags_upd AFTER UPDATE ON question_tags FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_question_tags_del AFTER DELETE ON question_tags FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_questions_ins AFTER INSERT ON questions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_questions_upd AFTER UPDATE ON questions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_questions_del AFTER DELETE ON questions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_taxonomy_review_applications_ins AFTER INSERT ON taxonomy_review_applications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_taxonomy_review_applications_upd AFTER UPDATE ON taxonomy_review_applications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_taxonomy_review_applications_del AFTER DELETE ON taxonomy_review_applications FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_attempts_ins AFTER INSERT ON training_assessment_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_attempts_upd AFTER UPDATE ON training_assessment_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_attempts_del AFTER DELETE ON training_assessment_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_events_ins AFTER INSERT ON training_assessment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_events_upd AFTER UPDATE ON training_assessment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_events_del AFTER DELETE ON training_assessment_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_runs_ins AFTER INSERT ON training_assessment_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_runs_upd AFTER UPDATE ON training_assessment_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_assessment_runs_del AFTER DELETE ON training_assessment_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_attempts_ins AFTER INSERT ON training_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_attempts_upd AFTER UPDATE ON training_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_attempts_del AFTER DELETE ON training_attempts FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_items_ins AFTER INSERT ON training_criterion_backfill_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_items_upd AFTER UPDATE ON training_criterion_backfill_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_items_del AFTER DELETE ON training_criterion_backfill_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_runs_ins AFTER INSERT ON training_criterion_backfill_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_runs_upd AFTER UPDATE ON training_criterion_backfill_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_backfill_runs_del AFTER DELETE ON training_criterion_backfill_runs FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_events_ins AFTER INSERT ON training_criterion_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_events_upd AFTER UPDATE ON training_criterion_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_events_del AFTER DELETE ON training_criterion_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_heads_ins AFTER INSERT ON training_criterion_heads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_heads_upd AFTER UPDATE ON training_criterion_heads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_heads_del AFTER DELETE ON training_criterion_heads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_versions_ins AFTER INSERT ON training_criterion_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_versions_upd AFTER UPDATE ON training_criterion_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_criterion_versions_del AFTER DELETE ON training_criterion_versions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'materials';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_outbox_ins AFTER INSERT ON training_evidence_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_outbox_upd AFTER UPDATE ON training_evidence_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_outbox_del AFTER DELETE ON training_evidence_outbox FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_records_ins AFTER INSERT ON training_evidence_records FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_records_upd AFTER UPDATE ON training_evidence_records FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_records_del AFTER DELETE ON training_evidence_records FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_sync_events_ins AFTER INSERT ON training_evidence_sync_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_sync_events_upd AFTER UPDATE ON training_evidence_sync_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_evidence_sync_events_del AFTER DELETE ON training_evidence_sync_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_exports_ins AFTER INSERT ON training_exports FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_exports_upd AFTER UPDATE ON training_exports FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_exports_del AFTER DELETE ON training_exports FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_feedback_snapshots_ins AFTER INSERT ON training_feedback_snapshots FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_feedback_snapshots_upd AFTER UPDATE ON training_feedback_snapshots FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_feedback_snapshots_del AFTER DELETE ON training_feedback_snapshots FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_locks_ins AFTER INSERT ON training_point_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_locks_upd AFTER UPDATE ON training_point_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_locks_del AFTER DELETE ON training_point_locks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_results_ins AFTER INSERT ON training_point_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_results_upd AFTER UPDATE ON training_point_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_point_results_del AFTER DELETE ON training_point_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_question_results_ins AFTER INSERT ON training_question_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_question_results_upd AFTER UPDATE ON training_question_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_question_results_del AFTER DELETE ON training_question_results FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_scan_batches_ins AFTER INSERT ON training_scan_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_scan_batches_upd AFTER UPDATE ON training_scan_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_scan_batches_del AFTER DELETE ON training_scan_batches FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_set_items_ins AFTER INSERT ON training_set_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_set_items_upd AFTER UPDATE ON training_set_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_set_items_del AFTER DELETE ON training_set_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_sets_ins AFTER INSERT ON training_sets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_sets_upd AFTER UPDATE ON training_sets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_sets_del AFTER DELETE ON training_sets FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_events_ins AFTER INSERT ON training_submission_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_events_upd AFTER UPDATE ON training_submission_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_events_del AFTER DELETE ON training_submission_events FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_pages_ins AFTER INSERT ON training_submission_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_pages_upd AFTER UPDATE ON training_submission_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_pages_del AFTER DELETE ON training_submission_pages FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_uploads_ins AFTER INSERT ON training_submission_uploads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_uploads_upd AFTER UPDATE ON training_submission_uploads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submission_uploads_del AFTER DELETE ON training_submission_uploads FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submissions_ins AFTER INSERT ON training_submissions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submissions_upd AFTER UPDATE ON training_submissions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_submissions_del AFTER DELETE ON training_submissions FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_ops';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_task_items_ins AFTER INSERT ON training_task_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_task_items_upd AFTER UPDATE ON training_task_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_task_items_del AFTER DELETE ON training_task_items FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_tasks_ins AFTER INSERT ON training_tasks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_tasks_upd AFTER UPDATE ON training_tasks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_tasks_del AFTER DELETE ON training_tasks FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_variants_ins AFTER INSERT ON training_variants FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_variants_upd AFTER UPDATE ON training_variants FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_training_variants_del AFTER DELETE ON training_variants FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_variant_students_ins AFTER INSERT ON variant_students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_variant_students_upd AFTER UPDATE ON variant_students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;

CREATE TRIGGER IF NOT EXISTS dv_variant_students_del AFTER DELETE ON variant_students FOR EACH ROW BEGIN
    UPDATE data_versions SET version = version + 1 WHERE domain = 'training_records';
END;
