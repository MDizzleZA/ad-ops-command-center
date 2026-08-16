"""Google Business Profile sync: daily location performance metrics.

Auth uses OAuth user tokens at app.config.GBP_TOKENS (created by
tools/reauth_gbp.py with the business.manage scope) — the Business Profile
APIs do not support service accounts. The signed-in Google account must be a
manager of each location. account['external_id'] is the numeric location id
(the {id} in the locations/{id} resource name).

One Business Profile Performance API request per sync window:
  GET /v1/locations/{id}:fetchMultiDailyMetricsTimeSeries with CALL_CLICKS,
  WEBSITE_CLICKS, BUSINESS_DIRECTION_REQUESTS and the four impression surfaces.

Only account-level rows are written: clicks=WEBSITE_CLICKS, impressions=sum
of the four surfaces; calls/direction requests and the per-surface impression
split live in extra_json.

IMPORTANT: the Business Profile APIs ship with quota 0 — the GCP project
needs Google-approved access (Business Profile APIs access request form).
Until approved, every call fails 403/429; those surface as clean sync_runs
errors and the rest of the platforms are unaffected.
"""
import json
from collections import defaultdict

from app import config
from app.sync import base

API = 'https://businessprofileperformance.googleapis.com/v1'
DAILY_METRICS = (
    'CALL_CLICKS',
    'WEBSITE_CLICKS',
    'BUSINESS_DIRECTION_REQUESTS',
    'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH',
    'BUSINESS_IMPRESSIONS_MOBILE_SEARCH',
    'BUSINESS_IMPRESSIONS_DESKTOP_MAPS',
    'BUSINESS_IMPRESSIONS_MOBILE_MAPS',
)
IMPRESSION_KEYS = {
    'BUSINESS_IMPRESSIONS_DESKTOP_SEARCH': 'desktop_search',
    'BUSINESS_IMPRESSIONS_MOBILE_SEARCH': 'mobile_search',
    'BUSINESS_IMPRESSIONS_DESKTOP_MAPS': 'desktop_maps',
    'BUSINESS_IMPRESSIONS_MOBILE_MAPS': 'mobile_maps',
}


def _access_token() -> str:
    if not config.GBP_TOKENS.exists():
        raise RuntimeError(f'GBP tokens not found at {config.GBP_TOKENS} — '
                           'run: python tools/reauth_gbp.py')
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
    except ImportError as exc:
        raise RuntimeError('google-auth library not installed — '
                           'run: pip install google-auth') from exc
    data = json.loads(config.GBP_TOKENS.read_text(encoding='utf-8'))
    creds = Credentials(
        token=None,
        refresh_token=data.get('refresh_token'),
        client_id=data.get('client_id'),
        client_secret=data.get('client_secret'),
        token_uri=data.get('token_uri', 'https://oauth2.googleapis.com/token'),
    )
    try:
        creds.refresh(Request())
    except Exception as exc:
        raise RuntimeError(f'GBP token refresh failed — re-run tools/reauth_gbp.py: {exc}') from exc
    return creds.token


def _date_params(prefix: str, iso_date: str) -> dict:
    year, month, day = iso_date.split('-')
    return {f'{prefix}.year': int(year), f'{prefix}.month': int(month), f'{prefix}.day': int(day)}


def _fetch_timeseries(location_id: str, date_from: str, date_to: str) -> list[dict]:
    import requests

    params = [('dailyMetrics', m) for m in DAILY_METRICS]
    for key, value in {**_date_params('dailyRange.start_date', date_from),
                       **_date_params('dailyRange.end_date', date_to)}.items():
        params.append((key, value))
    response = requests.get(
        f'{API}/locations/{location_id}:fetchMultiDailyMetricsTimeSeries',
        params=params,
        headers={'Authorization': f'Bearer {_access_token()}'},
        timeout=60,
    )
    if response.status_code in (403, 429):
        raise RuntimeError(
            f'GBP API access not approved yet for location {location_id} '
            f'(the Business Profile APIs default to quota 0). Submit the '
            f'Business Profile APIs access request form for this GCP project, '
            f'enable the Business Profile Performance API, then retry. '
            f'({response.status_code}: {response.text[:200]})')
    if response.status_code != 200:
        raise RuntimeError(f'GBP API request failed for location {location_id}: '
                           f'{response.status_code} {response.text[:300]}')
    return response.json().get('multiDailyMetricTimeSeries', [])


def sync(account: dict, date_from: str, date_to: str) -> int:
    """Write one account-level metrics row per date in the window."""
    location_id = str(account['external_id']).strip()
    if not location_id.isdigit():
        raise RuntimeError(f'GBP external_id must be a numeric location id, got {location_id!r}')

    series = _fetch_timeseries(location_id, date_from, date_to)

    days: dict[str, dict] = defaultdict(lambda: {m: 0 for m in DAILY_METRICS})
    for multi in series:
        for entry in multi.get('dailyMetricTimeSeries', []):
            metric = entry.get('dailyMetric')
            if metric not in DAILY_METRICS:
                continue
            for dated in entry.get('timeSeries', {}).get('datedValues', []):
                d = dated.get('date', {})
                if not d.get('year'):
                    continue
                day = f"{d['year']:04d}-{d.get('month', 1):02d}-{d.get('day', 1):02d}"
                days[day][metric] += int(dated.get('value', 0) or 0)

    rows_written = 0
    for day in sorted(days):
        bucket = days[day]
        impressions = {label: bucket[metric] for metric, label in IMPRESSION_KEYS.items()}
        base.upsert_metric(
            account['id'], 'account', '', day,
            spend=0,
            clicks=bucket['WEBSITE_CLICKS'],
            impressions=sum(impressions.values()),
            extra_json=json.dumps({
                'calls': bucket['CALL_CLICKS'],
                'direction_requests': bucket['BUSINESS_DIRECTION_REQUESTS'],
                'website_clicks': bucket['WEBSITE_CLICKS'],
                'impressions': impressions,
            }))
        rows_written += 1

    return rows_written
