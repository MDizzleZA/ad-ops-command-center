import json
import sqlite3
from contextlib import contextmanager

from app.config import BASE_DIR, DB_PATH, DEFAULT_SETTINGS


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def _migrate_ad_accounts_platforms(conn):
    """Rebuild ad_accounts if its platform CHECK predates gsc/gbp.

    SQLite cannot ALTER a CHECK constraint and migrate() only runs
    CREATE TABLE IF NOT EXISTS, so existing databases keep the old
    constraint. Rebuild preserves ids so FK rows in metrics_daily,
    campaigns, creatives and sync_runs stay valid.
    """
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='ad_accounts'").fetchone()
    if not row or "'gsc'" in row['sql']:
        return
    conn.executescript("""
        PRAGMA foreign_keys=OFF;
        PRAGMA legacy_alter_table=ON;
        ALTER TABLE ad_accounts RENAME TO ad_accounts_old;
        CREATE TABLE ad_accounts (
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
        INSERT INTO ad_accounts SELECT * FROM ad_accounts_old;
        DROP TABLE ad_accounts_old;
        PRAGMA legacy_alter_table=OFF;
        PRAGMA foreign_keys=ON;
    """)


def _ensure_column(conn, table: str, column: str, decl: str):
    """ADD COLUMN if missing (schema.sql only does CREATE TABLE IF NOT EXISTS)."""
    cols = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
    if column not in cols:
        conn.execute(f'ALTER TABLE {table} ADD COLUMN {column} {decl}')


def migrate():
    schema = (BASE_DIR / 'app' / 'schema.sql').read_text(encoding='utf-8')
    with connect() as conn:
        conn.executescript(schema)
        _migrate_ad_accounts_platforms(conn)
        _ensure_column(conn, 'reference_ads', 'landing_url', 'TEXT')
        _ensure_column(conn, 'brand_profiles', 'product_images_json', 'TEXT')
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
        conn.commit()


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def rows(sql: str, params=()) -> list[dict]:
    with db() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def row(sql: str, params=()) -> dict | None:
    result = rows(sql, params)
    return result[0] if result else None


def execute(sql: str, params=()) -> int:
    """Run a write statement; returns lastrowid."""
    with db() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def executemany(sql: str, seq_params) -> int:
    with db() as conn:
        cur = conn.executemany(sql, seq_params)
        return cur.rowcount


def setting(key: str, default: str = '') -> str:
    r = row('SELECT value FROM settings WHERE key = ?', (key,))
    return r['value'] if r else default


def set_setting(key: str, value: str):
    execute('INSERT INTO settings (key, value) VALUES (?, ?) '
            'ON CONFLICT(key) DO UPDATE SET value = excluded.value', (key, value))


def jloads(text, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default
