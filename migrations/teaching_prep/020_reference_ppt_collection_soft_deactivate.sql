ALTER TABLE reference_ppt_collections
ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1
    CHECK(is_active IN (0, 1));
