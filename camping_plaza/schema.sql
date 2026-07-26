-- 露营广场 - 数据库结构
-- 版本：v0.1
-- 说明：SQLite，轻量存储，游戏核心状态

-- 游戏状态表（单行记录，每次更新覆盖）
CREATE TABLE IF NOT EXISTS game_state (
    id INTEGER PRIMARY KEY DEFAULT 1,
    day INTEGER NOT NULL DEFAULT 1,
    turn INTEGER NOT NULL DEFAULT 1,
    balance INTEGER NOT NULL DEFAULT 1000,
    reputation_rate REAL NOT NULL DEFAULT 60.0,
    total_reviews INTEGER NOT NULL DEFAULT 0,
    total_rating_sum INTEGER NOT NULL DEFAULT 0,
    decisions_left INTEGER NOT NULL DEFAULT 3,
    reservation_data TEXT,  -- JSON: 预定信息
    reserved_tent_id INTEGER,
    today_income_data TEXT,  -- JSON: 今日收入明细
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 帐篷表
CREATE TABLE IF NOT EXISTS tents (
    id INTEGER PRIMARY KEY,
    capacity INTEGER NOT NULL,
    level INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'available',  -- available/occupied/cleaning/broken
    occupied_by_npc_id INTEGER,
    next_breakdown_turn INTEGER NOT NULL DEFAULT 0,
    satisfaction_bonus REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 设施表
CREATE TABLE IF NOT EXISTS facilities (
    name TEXT PRIMARY KEY,  -- dining/entertainment/greenery
    level INTEGER NOT NULL DEFAULT 0,
    dining_spend_probability REAL,
    dining_income_multiplier REAL,
    dining_satisfaction REAL,
    entertainment_satisfaction REAL,
    entertainment_income_multiplier REAL,
    greenery_satisfaction REAL,
    greenery_decay_rate REAL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- NPC客人表（当前在游戏中的）
CREATE TABLE IF NOT EXISTS npcs (
    id INTEGER PRIMARY KEY,
    group_size INTEGER NOT NULL,
    visit_type TEXT NOT NULL,  -- day/overnight
    arrival_turn INTEGER NOT NULL DEFAULT 0,
    location TEXT NOT NULL DEFAULT 'gate',
    total_satisfaction INTEGER NOT NULL DEFAULT 60,
    has_left INTEGER NOT NULL DEFAULT 0,
    review_left INTEGER NOT NULL DEFAULT 0,
    review_rating INTEGER NOT NULL DEFAULT 0,
    -- 隐藏标签
    economic_level INTEGER NOT NULL DEFAULT 1,
    spending_habit INTEGER NOT NULL DEFAULT 1,
    temperament INTEGER NOT NULL DEFAULT 1,
    -- 回访
    visit_count INTEGER NOT NULL DEFAULT 1,
    last_visit_day INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- NPC历史表（已离开但保留回访记录）
CREATE TABLE IF NOT EXISTS npc_history (
    id INTEGER PRIMARY KEY,
    group_size INTEGER NOT NULL,
    visit_count INTEGER NOT NULL DEFAULT 1,
    last_visit_day INTEGER NOT NULL DEFAULT 0,
    economic_level INTEGER NOT NULL DEFAULT 1,
    spending_habit INTEGER NOT NULL DEFAULT 1,
    temperament INTEGER NOT NULL DEFAULT 1,
    total_reviews INTEGER NOT NULL DEFAULT 0,
    avg_rating REAL,
    last_satisfaction INTEGER,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 运行时快照表（单行 JSON 快照，当前唯一权威运行存档）
-- 规范化表暂保留但不再作为运行状态恢复来源
CREATE TABLE IF NOT EXISTS runtime_snapshot (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 今日事件日志
CREATE TABLE IF NOT EXISTS event_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day INTEGER NOT NULL,
    turn INTEGER NOT NULL,
    event_type TEXT NOT NULL,  -- checkin/checkout/dining/entertainment/breakdown/repair/upgrade/review
    event_data TEXT,  -- JSON
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- 初始化默认数据
INSERT OR IGNORE INTO tents (id, capacity) VALUES
    (1, 1), (2, 2), (3, 2), (4, 3), (5, 3), (6, 5);

INSERT OR IGNORE INTO facilities (name, level) VALUES
    ('dining', 0),
    ('entertainment', 0),
    ('greenery', 1);

INSERT OR IGNORE INTO game_state (id, day, turn, balance, reputation_rate)
VALUES (1, 1, 1, 1000, 60.0);
