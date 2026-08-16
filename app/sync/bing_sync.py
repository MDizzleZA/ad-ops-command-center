"""Microsoft Advertising (Bing Ads) sync via the shared AdOps connector.

Reuses BingAdsConnector from ~/.adops/connectors/bing_ads.py (imported
by adding app.config.CONNECTORS_DIR to sys.path). Credentials come from the
BING_* env vars already loaded by app.config from ~/.adops/.env.

Granularity: the connector builds its Reporting API requests with
Aggregation='Daily' and a TimePeriod column, so the raw report rows ARE daily —
only its public helpers (get_account_insights/get_campaign_insights) collapse
them to period totals. This module therefore reuses the connector's request
builders and _download_report directly to keep per-day rows, and upserts one
metrics row per date at both account and campaign level. No weekly chunking is
needed.

Conversions from the Reporting API are stored as both leads and conversions
(lead-gen account, same convention as google_sync).
"""
import json
import os
import sys
from datetime import datetime

from app import config
from app.sync import base

REQUIRED_ENV = ('BING_DEVELOPER_TOKEN', 'BING_GOOGLE_CLIENT_ID', 'BING_GOOGLE_CLIENT_SECRET',
                'BING_GOOGLE_REFRESH_TOKEN', 'BING_CUSTOMER_ID', 'BING_ACCOUNT_ID')

_TIME_PERIOD_FORMATS = ('%m/%d/%Y', '%Y-%m-%d', '%d/%m/%Y')


def _connector(account: dict):
    missing = [v for v in REQUIRED_ENV if not os.environ.get(v)]
    if missing:
        raise RuntimeError(
            f"Bing Ads credentials missing from environment: {', '.join(missing)} — "
            'check ~/.adops/.env (loaded by app.config)')

    connectors_dir = str(config.CONNECTORS_DIR)
    if connectors_dir not in sys.path:
        sys.path.insert(0, connectors_dir)
    try:
        from bing_ads import BingAdsConnector
    except ImportError as exc:
        raise RuntimeError(
            f'Cannot import BingAdsConnector from {connectors_dir} — '
            f'is the bingads SDK installed? (pip install bingads) [{exc}]') from exc

    ext = (account.get('external_id') or '').strip()
    account_id = int(ext) if ext.isdigit() else None  # non-numeric (e.g. account number) -> env
    try:
        return BingAdsConnector(account_id=account_id)
    except Exception as exc:
        raise RuntimeError(
            f'Bing Ads auth failed (Google OAuth refresh token may be revoked/expired): {exc}'
        ) from exc


def _parse_day(raw: str) -> str:
    for fmt in _TIME_PERIOD_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date().isoformat()
        except (ValueError, AttributeError):
            continue
    raise RuntimeError(f'Unrecognised Bing report TimePeriod value: {raw!r}')


def _safe_float(val) -> float:
    try:
        return float(str(val).replace(',', ''))
    except (ValueError, TypeError):
        return 0.0


def _safe_int(val) -> int:
    return int(_safe_float(val))


def _metric_kwargs(report_row: dict) -> dict:
    conversions = _safe_int(report_row.get('Conversions', '0'))
    return dict(
        spend=_safe_float(report_row.get('Spend', '0')),
        impressions=_safe_int(report_row.get('Impressions', '0')),
        clicks=_safe_int(report_row.get('Clicks', '0')),
        leads=conversions,
        conversions=conversions,
        extra_json=json.dumps({'granularity': 'daily'}),
    )


def sync(account: dict, date_from: str, date_to: str) -> int:
    """Pull daily account + campaign report rows and upsert one metrics row per date."""
    connector = _connector(account)
    account_id = account['id']
    rows_written = 0

    try:
        account_rows = connector._download_report(
            connector._build_account_request(date_from, date_to))
        campaign_rows = connector._download_report(
            connector._build_campaign_request(date_from, date_to))
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Bing Ads report download failed for {date_from}..{date_to}: {exc}'
                           ) from exc

    for report_row in account_rows:
        day = _parse_day(report_row.get('TimePeriod', ''))
        base.upsert_metric(account_id, 'account', '', day, **_metric_kwargs(report_row))
        rows_written += 1

    campaigns_seen: dict[str, str] = {}
    for report_row in campaign_rows:
        campaign_id = str(report_row.get('CampaignId', '') or '').strip()
        if not campaign_id:
            continue
        day = _parse_day(report_row.get('TimePeriod', ''))
        campaigns_seen[campaign_id] = report_row.get('CampaignName', '') or campaign_id
        base.upsert_metric(account_id, 'campaign', campaign_id, day, **_metric_kwargs(report_row))
        rows_written += 1

    for campaign_id, name in campaigns_seen.items():
        base.upsert_campaign(account_id, campaign_id, name)

    return rows_written
