"""LinkedIn Ads sync: daily campaign analytics via the versioned Marketing REST API.

Tokens live at app.config.LINKEDIN_TOKENS (~/.adops/linkedin-tokens.json);
client id/secret sit in Windows Credential Manager under 'linkedin:reporting'
(same store linkedin_auth.py uses). Token load/refresh is reimplemented here in
miniature — requests + keyring + json — no sys.path import of the Claude-Work
scripts. Requests retry once on 401 after a forced token refresh.

account['external_id'] is the numeric sponsored account id (e.g. '123456789'),
mapped to urn:li:sponsoredAccount:<id>. adAnalytics is queried with
pivot=CAMPAIGN, timeGranularity=DAILY; campaign-level daily rows are upserted
(spend=costInLocalCurrency, leads=oneClickLeads,
conversions=externalWebsiteConversions) plus per-date account-level aggregates.
Campaign names come from q=search on /adAccounts/{id}/adCampaigns.
"""
import json
import time
from collections import defaultdict
from datetime import datetime
from urllib.parse import quote

from app import config
from app.sync import base

CRED_TARGET = 'linkedin:reporting'
TOKEN_FILE = config.LINKEDIN_TOKENS
API = 'https://api.linkedin.com/rest'
TOKEN_URL = 'https://www.linkedin.com/oauth/v2/accessToken'
# Fallback only. The live value is the linkedin_api_version setting so sync and the
# publish adapter share one pin -- an expired version returns HTTP 426 on every call,
# which is what silently broke this sync when it was hardcoded to '202506'.
FALLBACK_API_VERSION = '202607'


def api_version() -> str:
    """Current LinkedIn-Version header value. Bump the setting quarterly.

    tools/check_publish_scopes.py probes recent YYYYMM values and reports the newest
    one that does not return 426.
    """
    from app import db
    return db.setting('linkedin_api_version', FALLBACK_API_VERSION) or FALLBACK_API_VERSION

ANALYTICS_FIELDS = ('impressions,clicks,costInLocalCurrency,externalWebsiteConversions,'
                    'oneClickLeads,dateRange,pivotValues')


# ---------- token plumbing ----------

def _load_client_credentials() -> tuple[str, str]:
    import keyring
    cred = keyring.get_credential(CRED_TARGET, None)
    if not cred:
        raise RuntimeError(
            f"LinkedIn client credentials not found in Credential Manager under '{CRED_TARGET}' "
            '— store them and run linkedin_auth.py')
    return cred.username, cred.password


def _load_tokens() -> dict:
    if not TOKEN_FILE.exists():
        raise RuntimeError(f'LinkedIn token file missing at {TOKEN_FILE} — run linkedin_auth.py')
    return json.loads(TOKEN_FILE.read_text())


def _save_tokens(tok: dict) -> None:
    tok['obtained_at'] = int(time.time())
    tok['expires_at'] = tok['obtained_at'] + tok.get('expires_in', 0)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tok, indent=2))


def _refresh_tokens(tok: dict) -> dict:
    import requests
    if 'refresh_token' not in tok:
        raise RuntimeError('LinkedIn access token expired and no refresh token stored — '
                           're-run linkedin_auth.py to re-authorise')
    client_id, client_secret = _load_client_credentials()
    resp = requests.post(TOKEN_URL, data={
        'grant_type': 'refresh_token',
        'refresh_token': tok['refresh_token'],
        'client_id': client_id,
        'client_secret': client_secret,
    }, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f'LinkedIn token refresh failed [{resp.status_code}]: '
                           f'{resp.text[:300]} — re-run linkedin_auth.py if this persists')
    new = resp.json()
    if 'refresh_token' not in new:  # preserve refresh token if not re-issued
        new['refresh_token'] = tok['refresh_token']
        new['refresh_token_expires_in'] = tok.get('refresh_token_expires_in')
    _save_tokens(new)
    return new


def _access_token(force_refresh: bool = False) -> str:
    tok = _load_tokens()
    if force_refresh or tok.get('expires_at', 0) - time.time() <= 300:
        tok = _refresh_tokens(tok)
    return tok['access_token']


def _headers(force_refresh: bool = False) -> dict:
    return {
        'LinkedIn-Version': api_version(),
        'X-Restli-Protocol-Version': '2.0.0',
        'Authorization': f'Bearer {_access_token(force_refresh)}',
    }


def _get(url: str, params: dict | None = None):
    """GET with one retry on 401 after a forced token refresh."""
    import requests
    resp = requests.get(url, headers=_headers(), params=params, timeout=60)
    if resp.status_code == 401:
        resp = requests.get(url, headers=_headers(force_refresh=True), params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f'LinkedIn API {url.split("?")[0]} -> {resp.status_code}: '
                           f'{resp.text[:400]}')
    return resp.json()


# ---------- request helpers ----------

def _date_range_param(date_from: str, date_to: str) -> str:
    start = datetime.strptime(date_from, '%Y-%m-%d').date()
    end = datetime.strptime(date_to, '%Y-%m-%d').date()
    return (f'(start:(year:{start.year},month:{start.month},day:{start.day}),'
            f'end:(year:{end.year},month:{end.month},day:{end.day}))')


def _fetch_analytics(external_id: str, date_from: str, date_to: str) -> list[dict]:
    """Daily CAMPAIGN-pivot analytics. LinkedIn rejects URL-encoded commas in
    `fields`, so the query string is built manually."""
    account_urn = quote(f'urn:li:sponsoredAccount:{external_id}', safe='')
    qs = (f'q=analytics&pivot=CAMPAIGN&timeGranularity=DAILY'
          f'&dateRange={_date_range_param(date_from, date_to)}'
          f'&accounts=List({account_urn})'
          f'&fields={ANALYTICS_FIELDS}')
    return _get(f'{API}/adAnalytics?{qs}').get('elements', [])


def _fetch_campaign_names(external_id: str) -> dict[str, dict]:
    """Map campaign id -> {name, status} via q=search under the account.
    Non-fatal: metrics still sync (with placeholder names) if this fails."""
    names: dict[str, dict] = {}
    start, seen = 0, set()
    try:
        while True:
            data = _get(f'{API}/adAccounts/{external_id}/adCampaigns',
                        params={'q': 'search', 'count': 100, 'start': start})
            elements = data.get('elements', [])
            new = 0
            for el in elements:
                cid = str(el.get('id', ''))
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                new += 1
                names[cid] = {'name': el.get('name', ''), 'status': el.get('status')}
            if not elements or new == 0 or start > 5000:
                break
            start += len(elements)
    except RuntimeError:
        return names  # partial/empty map — sync continues with placeholder names
    return names


def _element_day(element: dict) -> str | None:
    start = (element.get('dateRange') or {}).get('start') or {}
    if not start.get('year'):
        return None
    return f"{start['year']:04d}-{start['month']:02d}-{start['day']:02d}"


def _campaign_id_from_pivot(element: dict) -> str | None:
    for urn in element.get('pivotValues') or []:
        if 'sponsoredCampaign' in urn:
            return urn.rsplit(':', 1)[-1]
    return None


# ---------- sync ----------

def sync(account: dict, date_from: str, date_to: str) -> int:
    """Upsert campaign-level daily rows + per-date account aggregates."""
    external_id = str(account['external_id']).strip()
    if not external_id.isdigit():
        raise RuntimeError(
            f'LinkedIn external_id must be the numeric sponsored account id, got {external_id!r}')

    account_id = account['id']
    elements = _fetch_analytics(external_id, date_from, date_to)
    campaign_meta = _fetch_campaign_names(external_id)

    rows_written = 0
    account_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {'spend': 0.0, 'impressions': 0, 'clicks': 0, 'leads': 0, 'conversions': 0})
    campaigns_seen: set[str] = set()

    for element in elements:
        day = _element_day(element)
        campaign_id = _campaign_id_from_pivot(element)
        if not day or not campaign_id:
            continue

        spend = float(element.get('costInLocalCurrency', 0) or 0)
        impressions = int(element.get('impressions', 0) or 0)
        clicks = int(element.get('clicks', 0) or 0)
        leads = int(element.get('oneClickLeads', 0) or 0)
        conversions = int(element.get('externalWebsiteConversions', 0) or 0)

        campaigns_seen.add(campaign_id)
        base.upsert_metric(account_id, 'campaign', campaign_id, day,
                           spend=spend, impressions=impressions, clicks=clicks,
                           leads=leads, conversions=conversions)
        rows_written += 1

        totals = account_totals[day]
        totals['spend'] += spend
        totals['impressions'] += impressions
        totals['clicks'] += clicks
        totals['leads'] += leads
        totals['conversions'] += conversions

    for campaign_id in campaigns_seen:
        meta = campaign_meta.get(campaign_id) or {}
        base.upsert_campaign(account_id, campaign_id,
                             meta.get('name') or f'LinkedIn campaign {campaign_id}',
                             status=meta.get('status'))

    for day, totals in account_totals.items():
        base.upsert_metric(account_id, 'account', '', day,
                           spend=totals['spend'], impressions=totals['impressions'],
                           clicks=totals['clicks'], leads=totals['leads'],
                           conversions=totals['conversions'])
        rows_written += 1

    return rows_written
