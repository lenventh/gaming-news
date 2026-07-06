CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,       -- 'rss', 'reddit', 'bilibili', 'zhihu', 'web'
    published_at TIMESTAMP,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    category TEXT,                    -- assigned by classifier
    raw_data TEXT,                    -- JSON blob for source-specific data
    content_hash TEXT UNIQUE,         -- MD5 of title+url for dedup
    material_links TEXT               -- JSON array of image/video URLs
);

CREATE TABLE IF NOT EXISTS weekly_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_label TEXT NOT NULL UNIQUE,  -- e.g. '2026-W28'
    generated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    markdown_content TEXT NOT NULL,
    item_count INTEGER,
    stats TEXT                        -- JSON: counts per category
);
