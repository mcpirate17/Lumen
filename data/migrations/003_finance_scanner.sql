-- Lumen — Finance Scanner & Dashboard Schema
-- Stores automated scan results for the finance tab

-- Scan findings: each row is a notable signal found by the scanner
CREATE TABLE IF NOT EXISTS scan_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    scan_id TEXT NOT NULL,          -- groups findings from same scan run
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'crypto', 'etf', 'bond', 'commodity', 'currency')),
    category TEXT NOT NULL CHECK (category IN (
        'overbought', 'oversold', 'overvalued', 'undervalued',
        'momentum', 'reversal', 'volume_anomaly', 'earnings_event',
        'analyst_upgrade', 'insider_activity', 'high_risk', 'high_opportunity'
    )),
    headline TEXT NOT NULL,         -- one-line summary for dashboard
    detail TEXT,                    -- longer explanation
    risk_score REAL DEFAULT 0.5,
    opportunity_score REAL DEFAULT 0.5,
    confidence REAL DEFAULT 0.5,    -- how many indicators confirm
    indicators TEXT,                -- JSON list of confirming indicator names
    price REAL,
    seen INTEGER DEFAULT 0,         -- 1 = user has viewed this finding
    dismissed INTEGER DEFAULT 0     -- 1 = user dismissed it
);

CREATE INDEX IF NOT EXISTS idx_findings_timestamp ON scan_findings(timestamp);
CREATE INDEX IF NOT EXISTS idx_findings_symbol ON scan_findings(symbol);
CREATE INDEX IF NOT EXISTS idx_findings_category ON scan_findings(category);
CREATE INDEX IF NOT EXISTS idx_findings_unseen ON scan_findings(seen, timestamp);

-- Scan run history: tracks when scans ran and what they found
CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_id TEXT NOT NULL UNIQUE,
    timestamp TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    scan_type TEXT NOT NULL CHECK (scan_type IN ('quick', 'full', 'watchlist', 'sector')),
    symbols_scanned INTEGER DEFAULT 0,
    findings_count INTEGER DEFAULT 0,
    duration_ms INTEGER DEFAULT 0,
    status TEXT DEFAULT 'completed'
);

-- Asset universe: symbols the scanner should check regularly
CREATE TABLE IF NOT EXISTS scan_universe (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    asset_type TEXT NOT NULL CHECK (asset_type IN ('stock', 'crypto', 'etf', 'bond', 'commodity', 'currency')),
    source TEXT NOT NULL CHECK (source IN ('watchlist', 'sp500', 'sector_etf', 'crypto_top', 'manual', 'macro')),
    enabled INTEGER DEFAULT 1,
    last_scanned TEXT,
    UNIQUE(symbol, asset_type)
);

-- Pre-populate with sector ETFs, macro instruments, and key indices
INSERT OR IGNORE INTO scan_universe (symbol, asset_type, source) VALUES
    -- Sector ETFs (cover every sector of the market)
    ('XLK', 'etf', 'sector_etf'),   -- Technology
    ('XLF', 'etf', 'sector_etf'),   -- Financials
    ('XLE', 'etf', 'sector_etf'),   -- Energy
    ('XLV', 'etf', 'sector_etf'),   -- Healthcare
    ('XLI', 'etf', 'sector_etf'),   -- Industrials
    ('XLP', 'etf', 'sector_etf'),   -- Consumer Staples
    ('XLY', 'etf', 'sector_etf'),   -- Consumer Discretionary
    ('XLU', 'etf', 'sector_etf'),   -- Utilities
    ('XLB', 'etf', 'sector_etf'),   -- Materials
    ('XLRE', 'etf', 'sector_etf'),  -- Real Estate
    ('XLC', 'etf', 'sector_etf'),   -- Communication Services
    -- Broad market
    ('SPY', 'etf', 'sector_etf'),   -- S&P 500
    ('QQQ', 'etf', 'sector_etf'),   -- Nasdaq 100
    ('IWM', 'etf', 'sector_etf'),   -- Russell 2000
    ('DIA', 'etf', 'sector_etf'),   -- Dow Jones
    -- Bonds / Fixed Income
    ('TLT', 'etf', 'sector_etf'),   -- 20+ Year Treasury
    ('IEF', 'etf', 'sector_etf'),   -- 7-10 Year Treasury
    ('SHY', 'etf', 'sector_etf'),   -- 1-3 Year Treasury
    ('HYG', 'etf', 'sector_etf'),   -- High Yield Corporate
    ('LQD', 'etf', 'sector_etf'),   -- Investment Grade Corporate
    ('AGG', 'etf', 'sector_etf'),   -- Aggregate Bond
    -- Commodities
    ('GLD', 'etf', 'sector_etf'),   -- Gold
    ('SLV', 'etf', 'sector_etf'),   -- Silver
    ('USO', 'etf', 'sector_etf'),   -- Oil
    ('UNG', 'etf', 'sector_etf'),   -- Natural Gas
    ('DBA', 'etf', 'sector_etf'),   -- Agriculture
    -- Currencies
    ('UUP', 'etf', 'sector_etf'),   -- US Dollar Index
    ('FXE', 'etf', 'sector_etf'),   -- Euro
    ('FXY', 'etf', 'sector_etf'),   -- Yen
    -- Top crypto (CoinGecko IDs)
    ('bitcoin', 'crypto', 'crypto_top'),
    ('ethereum', 'crypto', 'crypto_top'),
    ('solana', 'crypto', 'crypto_top'),
    ('cardano', 'crypto', 'crypto_top'),
    ('chainlink', 'crypto', 'crypto_top'),
    ('avalanche-2', 'crypto', 'crypto_top'),
    ('polkadot', 'crypto', 'crypto_top'),
    ('dogecoin', 'crypto', 'crypto_top'),
    ('ripple', 'crypto', 'crypto_top'),
    ('litecoin', 'crypto', 'crypto_top');

INSERT OR IGNORE INTO schema_version (version) VALUES (3);
