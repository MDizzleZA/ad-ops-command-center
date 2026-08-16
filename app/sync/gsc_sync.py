"""Google Search Console sync: daily organic search performance.

Auth uses the shared service account json at app.config.GSC_SERVICE_ACCOUNT
(your-service-account@your-project.iam.gserviceaccount.com) — the account must be
added as a user (Full or Restricted) on each Search Console property.
account['external_id'] is the exact GSC property, either 'sc-domain:example.com'
or 'https://example.com/' — the form must match the property type in Search
Console; the wrong form returns empty rows, not an error.

Three searchanalytics.query calls per sync window:
  1. dimensions=[date]        -> daily clicks/impressions/ctr/position totals.
  2. dimensions=[date, query] -> top queries per day (by clicks).
  3. dimensions=[date, page]  -> top pages per day (by clicks).

Only account-level rows are written: clicks/impressions go to their columns;
ctr, position and the top-10 query/page lists live in extra_json. GSC data
finalises ~2-3 days late, so the most recent days under-report until the
rolling sync window overwrites them.
"""
import json
from collections import defaultdict

from app import config
from app.sync import base

SCOPES = ['https://www.googleapis.com/auth/webmasters.readonly']
TOP_N = 10
ROW_LIMIT = 25000


def _service():
    try:
        from google.oauth2 import service_account
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError('google-api-python-client not installed — '
                           'run: pip install google-api-python-client') from exc
    if not config.GSC_SERVICE_ACCOUNT.exists():
        raise RuntimeError(f'GSC service account key not found at {config.GSC_SERVICE_ACCOUNT}')
    try:
        creds = service_account.Credentials.from_service_account_file(
            str(config.GSC_SERVICE_ACCOUNT), scopes=SCOPES)
        return build('searchconsole', 'v1', credentials=creds, cache_discovery=False)
    except Exception as exc:
        raise RuntimeError(f'Failed to load GSC service account credentials: {exc}') from exc


def _query(service, site_url: str, body: dict) -> list[dict]:
    try:
        from googleapiclient.errors import HttpError
    except ImportError:  # pragma: no cover - import guarded in _service()
        HttpError = Exception
    try:
        response = service.searchanalytics().query(siteUrl=site_url, body=body).execute()
        return response.get('rows', [])
    except HttpError as exc:
        if exc.resp.status in (401, 403):
            raise RuntimeError(
                f'GSC access denied for {site_url!r} — add '
                f'your-service-account@your-project.iam.gserviceaccount.com as a user '
                f'(Full or Restricted) under Settings > Users and permissions '
                f'in Search Console: {exc}') from exc
        raise RuntimeError(f'GSC query failed for {site_url!r}: {exc}') from exc


def _top_by_day(rows: list[dict], key_name: str) -> dict[str, list[dict]]:
    """rows have keys [date, query|page] -> top TOP_N per day by clicks."""
    per_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        day, key = r['keys'][0], r['keys'][1]
        per_day[day].append({
            key_name: key,
            'clicks': int(r.get('clicks', 0)),
            'impressions': int(r.get('impressions', 0)),
            'position': round(float(r.get('position', 0)), 1),
        })
    return {
        day: sorted(entries, key=lambda e: (e['clicks'], e['impressions']), reverse=True)[:TOP_N]
        for day, entries in per_day.items()
    }


def sync(account: dict, date_from: str, date_to: str) -> int:
    """Write one account-level metrics row per date in the window."""
    service = _service()
    site_url = str(account['external_id']).strip()
    if not (site_url.startswith('sc-domain:') or site_url.startswith('http')):
        raise RuntimeError(
            f"GSC external_id must be 'sc-domain:example.com' or a full URL, got {site_url!r}")

    def body(dimensions: list[str], row_limit: int) -> dict:
        return {'startDate': date_from, 'endDate': date_to,
                'dimensions': dimensions, 'rowLimit': row_limit}

    daily_rows = _query(service, site_url, body(['date'], 1000))
    query_rows = _query(service, site_url, body(['date', 'query'], ROW_LIMIT))
    page_rows = _query(service, site_url, body(['date', 'page'], ROW_LIMIT))

    top_queries = _top_by_day(query_rows, 'q')
    top_pages = _top_by_day(page_rows, 'page')

    rows_written = 0
    for r in daily_rows:
        day = r['keys'][0]
        base.upsert_metric(
            account['id'], 'account', '', day,
            spend=0,
            clicks=int(r.get('clicks', 0)),
            impressions=int(r.get('impressions', 0)),
            extra_json=json.dumps({
                'ctr': round(float(r.get('ctr', 0)), 4),
                'position': round(float(r.get('position', 0)), 1),
                'top_queries': top_queries.get(day, []),
                'top_pages': top_pages.get(day, []),
            }))
        rows_written += 1

    return rows_written
