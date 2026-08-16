"""Shared upsert helpers and sync-run bookkeeping for all platform syncs.

Every platform module in app/sync/ exposes:
    sync(account: dict, date_from: str, date_to: str) -> int   # rows written

where `account` is a row from ad_accounts (dict with id, client_id, platform,
external_id, alias, currency, config_json). Modules upsert into metrics_daily
(and creatives where the platform exposes ad-level creative metadata) via the
helpers below. Dates are YYYY-MM-DD.
"""
from datetime import date, datetime, timedelta

from app import db

DEFAULT_WINDOW_DAYS = 14
BACKFILL_DAYS = 90


def window_for(account_id: int) -> tuple[str, str]:
    """Last 14 days normally; 90-day backfill when the account has no data yet."""
    has_data = db.row('SELECT 1 AS x FROM metrics_daily WHERE account_id=? LIMIT 1', (account_id,))
    days = DEFAULT_WINDOW_DAYS if has_data else BACKFILL_DAYS
    today = date.today()
    return (today - timedelta(days=days)).isoformat(), today.isoformat()


def upsert_metric(account_id: int, level: str, entity_id: str, day: str, **m) -> None:
    db.execute(
        'INSERT INTO metrics_daily (account_id, level, entity_external_id, date, spend, impressions, clicks, '
        'leads, conversions, revenue, video_views, reach, frequency, extra_json) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(account_id, level, entity_external_id, date) DO UPDATE SET '
        'spend=excluded.spend, impressions=excluded.impressions, clicks=excluded.clicks, leads=excluded.leads, '
        'conversions=excluded.conversions, revenue=excluded.revenue, video_views=excluded.video_views, '
        'reach=excluded.reach, frequency=excluded.frequency, extra_json=excluded.extra_json',
        (account_id, level, entity_id or '', day,
         m.get('spend', 0) or 0, m.get('impressions', 0) or 0, m.get('clicks', 0) or 0,
         m.get('leads', 0) or 0, m.get('conversions', 0) or 0, m.get('revenue', 0) or 0,
         m.get('video_views', 0) or 0, m.get('reach', 0) or 0, m.get('frequency'),
         m.get('extra_json')))


def upsert_campaign(account_id: int, external_id: str, name: str, objective: str = None,
                    status: str = None) -> None:
    db.execute(
        'INSERT INTO campaigns (account_id, external_id, name, objective, status) VALUES (?,?,?,?,?) '
        'ON CONFLICT(account_id, external_id) DO UPDATE SET name=excluded.name, '
        'objective=COALESCE(excluded.objective, campaigns.objective), '
        'status=COALESCE(excluded.status, campaigns.status)',
        (account_id, external_id, name, objective, status))


def upsert_creative(account_id: int, ad_external_id: str, **c) -> None:
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    db.execute(
        'INSERT INTO creatives (account_id, ad_external_id, adset_external_id, campaign_external_id, name, '
        'format, headline, body, cta, landing_url, thumbnail_url, thumbnail_path, status, first_seen, last_seen) '
        'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(account_id, ad_external_id) DO UPDATE SET '
        'adset_external_id=COALESCE(excluded.adset_external_id, creatives.adset_external_id), '
        'campaign_external_id=COALESCE(excluded.campaign_external_id, creatives.campaign_external_id), '
        'name=COALESCE(excluded.name, creatives.name), format=COALESCE(excluded.format, creatives.format), '
        'headline=COALESCE(excluded.headline, creatives.headline), body=COALESCE(excluded.body, creatives.body), '
        'cta=COALESCE(excluded.cta, creatives.cta), landing_url=COALESCE(excluded.landing_url, creatives.landing_url), '
        'thumbnail_url=COALESCE(excluded.thumbnail_url, creatives.thumbnail_url), '
        'thumbnail_path=COALESCE(excluded.thumbnail_path, creatives.thumbnail_path), '
        'status=COALESCE(excluded.status, creatives.status), last_seen=excluded.last_seen',
        (account_id, ad_external_id, c.get('adset_external_id'), c.get('campaign_external_id'),
         c.get('name'), c.get('format'), c.get('headline'), c.get('body'), c.get('cta'),
         c.get('landing_url'), c.get('thumbnail_url'), c.get('thumbnail_path'), c.get('status'), now, now))


def start_run(platform: str, account_id: int | None, date_from: str, date_to: str) -> int:
    return db.execute('INSERT INTO sync_runs (platform, account_id, date_from, date_to) VALUES (?,?,?,?)',
                      (platform, account_id, date_from, date_to))


def finish_run(run_id: int, rows_written: int = 0, error: str = None) -> None:
    db.execute("UPDATE sync_runs SET status=?, rows_written=?, error=?, finished_at=datetime('now') WHERE id=?",
               ('error' if error else 'done', rows_written, error, run_id))


def accounts_for(platform: str, client_id: int | None = None) -> list[dict]:
    sql = 'SELECT * FROM ad_accounts WHERE platform=? AND sync_enabled=1'
    params: list = [platform]
    if client_id:
        sql += ' AND client_id=?'
        params.append(client_id)
    return db.rows(sql, params)
