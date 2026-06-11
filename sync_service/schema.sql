CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    kline_n INTEGER,
    action TEXT,
    dir5 TEXT,
    dir4 TEXT,
    regime TEXT,
    filt_passed INTEGER,
    reason TEXT,
    details TEXT,
    created_at TEXT,
    UNIQUE(source, kline_n, created_at)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    pnl REAL,
    won INTEGER,
    regime TEXT,
    direction TEXT,
    size REAL,
    entry_bar INTEGER,
    settle_bar INTEGER,
    details TEXT,
    created_at TEXT,
    UNIQUE(source, entry_bar, settle_bar)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    balance REAL,
    trades_count INTEGER,
    wr REAL,
    cooldown_left INTEGER,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS btc_klines (
    timestamp TEXT PRIMARY KEY,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL
);
