-- Lumen AI Assistant — Initial Schema
-- All timestamps stored as ISO 8601 UTC strings

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    model_used TEXT,
    route_reason TEXT,
    sentiment_compound REAL,
    sentiment_mood TEXT,
    domain TEXT,
    tokens_used INTEGER,
    latency_ms INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chats_timestamp ON chats(timestamp);
CREATE INDEX IF NOT EXISTS idx_chats_domain ON chats(domain);

CREATE TABLE IF NOT EXISTS profile (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL CHECK (category IN ('personality', 'preferences', 'mood', 'interests', 'style', 'schedule')),
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5 CHECK (confidence >= 0.0 AND confidence <= 1.0),
    evidence_count INTEGER DEFAULT 1,
    first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    last_updated TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE(category, key)
);

CREATE TABLE IF NOT EXISTS mood (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    vader_compound REAL NOT NULL,
    vader_pos REAL,
    vader_neg REAL,
    vader_neu REAL,
    mood_label TEXT,
    context TEXT
);

CREATE INDEX IF NOT EXISTS idx_mood_timestamp ON mood(timestamp);

CREATE TABLE IF NOT EXISTS market_data (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    source TEXT NOT NULL,
    data_type TEXT NOT NULL,
    symbol TEXT,
    payload TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_market_symbol ON market_data(symbol, timestamp);

CREATE TABLE IF NOT EXISTS claude_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    prediction TEXT NOT NULL,
    confidence REAL,
    basis TEXT,
    action TEXT,
    was_acted_on INTEGER DEFAULT 0,
    was_useful INTEGER
);

CREATE INDEX IF NOT EXISTS idx_predictions_timestamp ON predictions(timestamp);

CREATE TABLE IF NOT EXISTS watchlist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL UNIQUE,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'crypto', 'bond', 'etf')),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    alert_threshold_pct REAL DEFAULT 5.0,
    notes TEXT
);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

INSERT OR IGNORE INTO schema_version (version) VALUES (1);
