CREATE TABLE IF NOT EXISTS clients (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    industry TEXT,
    monthly_budget_zar REAL,
    kpi_json TEXT,
    vault_path TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS brand_profiles (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL UNIQUE REFERENCES clients(id),
    colors_json TEXT,
    fonts_json TEXT,
    logo_path TEXT,
    logo_dark_path TEXT,
    tagline TEXT,
    tone_of_voice TEXT,
    ad_specs_json TEXT,
    disclaimer_text TEXT,
    style_rules TEXT,
    source_path TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS personas (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    name TEXT NOT NULL,
    headline TEXT,
    demographics_json TEXT,
    pain_points_json TEXT,
    triggers_json TEXT,
    objections_json TEXT,
    source_path TEXT
);

CREATE TABLE IF NOT EXISTS compliance_rules (
    id INTEGER PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    framework TEXT NOT NULL,
    rule_type TEXT NOT NULL CHECK (rule_type IN ('prohibited','required','disclaimer')),
    rule_text TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'block' CHECK (severity IN ('block','warn'))
);

CREATE TABLE IF NOT EXISTS competitors (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    name TEXT NOT NULL,
    fb_page_url TEXT,
    fb_page_id TEXT,
    ig_handle TEXT,
    website TEXT,
    notes TEXT,
    source_path TEXT,
    active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS ad_accounts (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    platform TEXT NOT NULL CHECK (platform IN ('meta','google','bing','linkedin','ga4','gsc','gbp')),
    external_id TEXT NOT NULL,
    alias TEXT,
    currency TEXT NOT NULL DEFAULT 'ZAR',
    sync_enabled INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    UNIQUE (platform, external_id)
);

CREATE TABLE IF NOT EXISTS campaigns (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES ad_accounts(id),
    external_id TEXT NOT NULL,
    name TEXT,
    objective TEXT,
    status TEXT,
    UNIQUE (account_id, external_id)
);

CREATE TABLE IF NOT EXISTS metrics_daily (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES ad_accounts(id),
    level TEXT NOT NULL CHECK (level IN ('account','campaign','ad')),
    entity_external_id TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL,
    spend REAL NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    leads REAL NOT NULL DEFAULT 0,
    conversions REAL NOT NULL DEFAULT 0,
    revenue REAL NOT NULL DEFAULT 0,
    video_views INTEGER NOT NULL DEFAULT 0,
    reach INTEGER NOT NULL DEFAULT 0,
    frequency REAL,
    extra_json TEXT,
    UNIQUE (account_id, level, entity_external_id, date)
);
CREATE INDEX IF NOT EXISTS idx_metrics_acct_date ON metrics_daily (account_id, date);
CREATE INDEX IF NOT EXISTS idx_metrics_entity ON metrics_daily (level, entity_external_id);

CREATE TABLE IF NOT EXISTS creatives (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES ad_accounts(id),
    ad_external_id TEXT NOT NULL,
    adset_external_id TEXT,
    campaign_external_id TEXT,
    name TEXT,
    format TEXT,
    headline TEXT,
    body TEXT,
    cta TEXT,
    landing_url TEXT,
    thumbnail_url TEXT,
    thumbnail_path TEXT,
    status TEXT,
    first_seen TEXT,
    last_seen TEXT,
    UNIQUE (account_id, ad_external_id)
);
CREATE INDEX IF NOT EXISTS idx_creatives_acct ON creatives (account_id);

CREATE TABLE IF NOT EXISTS reference_ads (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('spy','scan','upload','vault_import','creative')),
    client_id INTEGER REFERENCES clients(id),
    competitor_id INTEGER REFERENCES competitors(id),
    platform TEXT NOT NULL DEFAULT 'meta',
    page_name TEXT,
    ad_library_id TEXT,
    format TEXT,
    headline TEXT,
    body TEXT,
    cta TEXT,
    media_url TEXT,
    local_media_path TEXT,
    started_running TEXT,
    is_active INTEGER,
    raw_json TEXT,
    tags TEXT,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_refads_competitor ON reference_ads (competitor_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_refads_libid ON reference_ads (ad_library_id) WHERE ad_library_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    kind TEXT NOT NULL DEFAULT 'ads' CHECK (kind IN ('ads','organic')),
    status TEXT NOT NULL DEFAULT 'running',
    apify_run_id TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    total_ads INTEGER NOT NULL DEFAULT 0,
    new_ads INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    summary_json TEXT
);

CREATE TABLE IF NOT EXISTS scan_ads (
    scan_id INTEGER NOT NULL REFERENCES scans(id),
    reference_ad_id INTEGER NOT NULL REFERENCES reference_ads(id),
    competitor_id INTEGER REFERENCES competitors(id),
    is_new INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scan_id, reference_ad_id)
);

CREATE TABLE IF NOT EXISTS organic_posts (
    id INTEGER PRIMARY KEY,
    competitor_id INTEGER NOT NULL REFERENCES competitors(id),
    scan_id INTEGER REFERENCES scans(id),
    platform TEXT NOT NULL DEFAULT 'facebook',
    post_url TEXT NOT NULL UNIQUE,
    posted_at TEXT,
    text TEXT,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shares INTEGER NOT NULL DEFAULT 0,
    media_url TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS briefs (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    parent_id INTEGER REFERENCES briefs(id),
    reference_ad_id INTEGER REFERENCES reference_ads(id),
    title TEXT,
    axis TEXT CHECK (axis IN ('hook','persona','pain_point','visual_format','asset_type')),
    axis_value TEXT,
    brief_json TEXT,
    compliance_json TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_briefs_client ON briefs (client_id, created_at);

CREATE TABLE IF NOT EXISTS clone_jobs (
    id INTEGER PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    reference_ad_id INTEGER REFERENCES reference_ads(id),
    source_image_path TEXT,
    layout_json TEXT,
    offer_text TEXT,
    variant_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ingested',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS generated_assets (
    id INTEGER PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    brief_id INTEGER REFERENCES briefs(id),
    clone_job_id INTEGER REFERENCES clone_jobs(id),
    kind TEXT NOT NULL DEFAULT 'image',
    file_path TEXT,
    prompt TEXT,
    model TEXT,
    meta_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    account_id INTEGER REFERENCES ad_accounts(id),
    date_from TEXT,
    date_to TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    rows_written INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS apify_runs (
    id INTEGER PRIMARY KEY,
    purpose TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    run_id TEXT,
    dataset_id TEXT,
    input_json TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    items INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS grader_actions (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    account_id INTEGER NOT NULL REFERENCES ad_accounts(id),
    level TEXT NOT NULL DEFAULT 'ad',
    entity_external_id TEXT NOT NULL,
    entity_name TEXT,
    action TEXT NOT NULL CHECK (action IN ('pause','scale')),
    grade TEXT,
    reason TEXT,
    metrics_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','applied','dismissed','failed')),
    error TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_grader_client ON grader_actions (client_id, status);

CREATE TABLE IF NOT EXISTS daily_ads (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    batch_date TEXT NOT NULL,
    awareness_stage TEXT NOT NULL,
    angle TEXT,
    headline TEXT,
    primary_text TEXT,
    description TEXT,
    cta TEXT,
    visual_direction TEXT,
    image_prompt TEXT,
    image_1x1_path TEXT,
    image_4x5_path TEXT,
    image_9x16_path TEXT,
    compliance_json TEXT,
    feedback INTEGER NOT NULL DEFAULT 0,
    feedback_note TEXT,
    status TEXT NOT NULL DEFAULT 'ready',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_daily_ads_client ON daily_ads (client_id, batch_date);

CREATE TABLE IF NOT EXISTS landing_pages (
    id INTEGER PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    competitor_id INTEGER REFERENCES competitors(id),
    reference_ad_id INTEGER REFERENCES reference_ads(id),
    url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done',
    error TEXT,
    analysis_json TEXT,
    raw_chars INTEGER,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_landing_url ON landing_pages (url);
CREATE INDEX IF NOT EXISTS idx_landing_competitor ON landing_pages (competitor_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ---------------------------------------------------------------------------
-- Publishing (distribution layer)
--
-- Deliberately separate from ad_accounts: that table models a paid measurement
-- account, its platform CHECK has no 'x', and one Facebook Page can carry both an
-- ad account and an organic channel. organic_posts is also not reusable here --
-- it holds scraped *competitor* posts and is keyed on competitor_id.
--
-- Safe defaults are load-bearing: publish_enabled starts at 0 and
-- requires_approval starts at 1, so a newly discovered channel can never publish
-- until it is explicitly switched on.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS social_channels (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    platform TEXT NOT NULL CHECK (platform IN ('facebook','instagram','linkedin','x','gbp')),
    external_id TEXT NOT NULL,          -- Page id / IG user id / org URN / X user id
    handle TEXT,
    name TEXT,
    token_ref TEXT,                     -- Credential Manager target NAME or file path; never a token value
    parent_external_id TEXT,            -- Instagram channels record the Page they hang off
    publish_enabled INTEGER NOT NULL DEFAULT 0,
    requires_approval INTEGER NOT NULL DEFAULT 1,
    config_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_channels_client ON social_channels (client_id, platform);

CREATE TABLE IF NOT EXISTS social_posts (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    caption TEXT,
    media_json TEXT,                    -- [{asset_id|file_path, public_url, position}]
    scheduled_at TEXT,                  -- 'YYYY-MM-DD HH:MM'; NULL means publish on approval
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
        ('draft','pending_compliance','pending_approval','scheduled','publishing',
         'published','partial','failed','cancelled')),
    compliance_status TEXT CHECK (compliance_status IN ('pass','warn','block')),
    compliance_json TEXT,
    brief_id INTEGER REFERENCES briefs(id),
    daily_ad_id INTEGER REFERENCES daily_ads(id),
    approved_at TEXT,
    approved_by TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_posts_client ON social_posts (client_id, status);
CREATE INDEX IF NOT EXISTS idx_posts_due ON social_posts (status, scheduled_at);

-- One row per channel a post fans out to, so three destinations can partially
-- succeed without the post as a whole being either published or failed.
CREATE TABLE IF NOT EXISTS social_post_targets (
    id INTEGER PRIMARY KEY,
    post_id INTEGER NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES social_channels(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
        ('pending','publishing','published','failed','skipped')),
    external_post_id TEXT,
    permalink TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    published_at TEXT,
    UNIQUE (post_id, channel_id)
);
CREATE INDEX IF NOT EXISTS idx_targets_post ON social_post_targets (post_id);
CREATE INDEX IF NOT EXISTS idx_targets_status ON social_post_targets (status);

-- Append-only. The evidence trail for what was published, when, and what the
-- platform said back -- which is what makes this safe to point at an FSP.
-- Rows are inserted, never updated or deleted.
CREATE TABLE IF NOT EXISTS publish_attempts (
    id INTEGER PRIMARY KEY,
    target_id INTEGER NOT NULL REFERENCES social_post_targets(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL,
    phase TEXT,                         -- validate | host_media | container | publish
    dry_run INTEGER NOT NULL DEFAULT 1,
    request_summary TEXT,               -- endpoint and payload shape; never a token
    response_code INTEGER,
    response_summary TEXT,
    error TEXT,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_attempts_target ON publish_attempts (target_id, attempt_no);
