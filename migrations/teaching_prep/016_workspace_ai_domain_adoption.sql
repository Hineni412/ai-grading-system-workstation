ALTER TABLE semester_mapping_proposals
ADD COLUMN workspace_adoption_id TEXT;

ALTER TABLE semester_mapping_proposals
ADD COLUMN workspace_adopted_at TEXT;

ALTER TABLE lesson_draft_versions
ADD COLUMN workspace_adoption_id TEXT;

ALTER TABLE lesson_draft_versions
ADD COLUMN workspace_adopted_at TEXT;

ALTER TABLE exercise_suggestion_runs
ADD COLUMN workspace_adoption_id TEXT;

ALTER TABLE exercise_suggestion_runs
ADD COLUMN workspace_adopted_at TEXT;

ALTER TABLE slide_plan_versions
ADD COLUMN workspace_adoption_id TEXT;

ALTER TABLE slide_plan_versions
ADD COLUMN workspace_adopted_at TEXT;

CREATE UNIQUE INDEX uq_semester_mapping_workspace_adoption
ON semester_mapping_proposals(workspace_adoption_id)
WHERE workspace_adoption_id IS NOT NULL;

CREATE UNIQUE INDEX uq_lesson_draft_workspace_adoption
ON lesson_draft_versions(workspace_adoption_id)
WHERE workspace_adoption_id IS NOT NULL;

CREATE UNIQUE INDEX uq_exercise_run_workspace_adoption
ON exercise_suggestion_runs(workspace_adoption_id)
WHERE workspace_adoption_id IS NOT NULL;

CREATE UNIQUE INDEX uq_slide_plan_workspace_adoption
ON slide_plan_versions(workspace_adoption_id)
WHERE workspace_adoption_id IS NOT NULL;
