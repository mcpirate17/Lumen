-- Lumen — Emotion & Behavioral Metrics Schema
-- Adds TinyBERT emotion detection + NRCLex + per-message style metrics

-- Add emotion columns to mood table
ALTER TABLE mood ADD COLUMN emotion_label TEXT;
ALTER TABLE mood ADD COLUMN emotion_confidence REAL;
ALTER TABLE mood ADD COLUMN nrc_joy REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_anger REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_fear REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_sadness REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_surprise REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_trust REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_anticipation REAL DEFAULT 0.0;
ALTER TABLE mood ADD COLUMN nrc_disgust REAL DEFAULT 0.0;

-- Per-message behavioral style metrics
CREATE TABLE IF NOT EXISTS behavioral_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    chat_id INTEGER REFERENCES chats(id),
    word_count INTEGER,
    sentence_count INTEGER,
    avg_sentence_length REAL,
    question_marks INTEGER DEFAULT 0,
    exclamation_marks INTEGER DEFAULT 0,
    pronoun_ratio_i REAL DEFAULT 0.0,
    pronoun_ratio_we REAL DEFAULT 0.0,
    pronoun_ratio_you REAL DEFAULT 0.0,
    formality_score REAL DEFAULT 0.5,
    emoji_count INTEGER DEFAULT 0,
    caps_ratio REAL DEFAULT 0.0,
    engagement_score REAL DEFAULT 0.5
);

CREATE INDEX IF NOT EXISTS idx_behavioral_timestamp ON behavioral_metrics(timestamp);
CREATE INDEX IF NOT EXISTS idx_behavioral_chat ON behavioral_metrics(chat_id);

-- Rolling baselines for drift detection
CREATE TABLE IF NOT EXISTS behavioral_baselines (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_name TEXT NOT NULL UNIQUE,
    rolling_mean REAL NOT NULL DEFAULT 0.0,
    rolling_std REAL NOT NULL DEFAULT 0.0,
    sample_count INTEGER NOT NULL DEFAULT 0,
    last_updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (2);
