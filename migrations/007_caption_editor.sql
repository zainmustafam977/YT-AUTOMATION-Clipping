-- E-016 — caption editor
ALTER TABLE clip_candidates ADD COLUMN caption_words_json TEXT;
ALTER TABLE clip_candidates ADD COLUMN caption_edited_at TIMESTAMP;
ALTER TABLE clip_candidates ADD COLUMN caption_edited_by VARCHAR(64);
