"""Apify REST client + normalizers for Meta Ad Library and Facebook posts actors.

Actor IDs live in settings (swappable without code changes). All runs are
logged to apify_runs. Normalizers are tolerant to field-name variants across
Ad Library actors and keep the raw item in raw_json.
"""
import json
import time

import requests

from app import db
from app.config import APIFY_TOKEN
from app.services.media import download_reference

API = 'https://api.apify.com/v2'


class ApifyError(RuntimeError):
    pass


def _require_token() -> str:
    if not APIFY_TOKEN:
        raise ApifyError('APIFY_TOKEN is not set in ~/.adops/.env - add it from the Apify console '
                         '(Settings > API & Integrations).')
    return APIFY_TOKEN


def start_run(actor_id: str, run_input: dict, purpose: str) -> int:
    """Start an actor run; returns our apify_runs row id."""
    token = _require_token()
    actor_path = actor_id.replace('/', '~')
    resp = requests.post(f'{API}/acts/{actor_path}/runs', params={'token': token},
                         json=run_input, timeout=60)
    if resp.status_code >= 400:
        raise ApifyError(f'Apify start failed ({resp.status_code}): {resp.text[:300]}')
    data = resp.json()['data']
    return db.execute(
        'INSERT INTO apify_runs (purpose, actor_id, run_id, dataset_id, input_json, status) '
        'VALUES (?,?,?,?,?,?)',
        (purpose, actor_id, data['id'], data.get('defaultDatasetId'),
         json.dumps(run_input), 'running'))


def poll_run(apify_run_row_id: int) -> dict:
    """Refresh status from Apify; returns the updated apify_runs row."""
    run = db.row('SELECT * FROM apify_runs WHERE id=?', (apify_run_row_id,))
    if not run or run['status'] not in ('running',):
        return run
    token = _require_token()
    resp = requests.get(f"{API}/actor-runs/{run['run_id']}", params={'token': token}, timeout=30)
    resp.raise_for_status()
    data = resp.json()['data']
    status = data['status']  # READY|RUNNING|SUCCEEDED|FAILED|ABORTED|TIMED-OUT
    if status in ('SUCCEEDED', 'FAILED', 'ABORTED', 'TIMED-OUT'):
        cost = (data.get('usageTotalUsd') or data.get('stats', {}).get('computeUnits'))
        db.execute("UPDATE apify_runs SET status=?, cost_usd=?, finished_at=datetime('now'), error=? WHERE id=?",
                   ('done' if status == 'SUCCEEDED' else 'error', cost,
                    None if status == 'SUCCEEDED' else status, apify_run_row_id))
    return db.row('SELECT * FROM apify_runs WHERE id=?', (apify_run_row_id,))


def wait_for_run(apify_run_row_id: int, timeout_s: int = 600, interval_s: int = 8) -> dict:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        run = poll_run(apify_run_row_id)
        if run['status'] != 'running':
            return run
        time.sleep(interval_s)
    db.execute("UPDATE apify_runs SET status='error', error='local timeout', finished_at=datetime('now') "
               'WHERE id=?', (apify_run_row_id,))
    return db.row('SELECT * FROM apify_runs WHERE id=?', (apify_run_row_id,))


def dataset_items(dataset_id: str, limit: int = 500) -> list[dict]:
    token = _require_token()
    resp = requests.get(f'{API}/datasets/{dataset_id}/items',
                        params={'token': token, 'clean': 'true', 'limit': limit}, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _first(item: dict, *keys, default=None):
    for key in keys:
        value = item
        for part in key.split('.'):
            value = value.get(part) if isinstance(value, dict) else None
            if value is None:
                break
        if value not in (None, '', []):
            return value
    return default


def normalize_ad_item(item: dict) -> dict:
    """Map an Ad Library actor item to reference_ads columns (tolerant to actor variants)."""
    snapshot = item.get('snapshot') or {}
    images = _first(item, 'snapshot.images', 'images', default=[]) or []
    videos = _first(item, 'snapshot.videos', 'videos', default=[]) or []
    cards = _first(item, 'snapshot.cards', 'cards', default=[]) or []
    media_url = None
    fmt = 'image'
    if videos:
        fmt = 'video'
        v0 = videos[0] if isinstance(videos[0], dict) else {}
        media_url = _first(v0, 'video_preview_image_url', 'videoPreviewImageUrl', 'thumbnail')
    elif cards and len(cards) > 1:
        fmt = 'carousel'
        c0 = cards[0] if isinstance(cards[0], dict) else {}
        media_url = _first(c0, 'original_image_url', 'resized_image_url', 'image')
    elif images:
        i0 = images[0] if isinstance(images[0], dict) else {}
        media_url = i0 if isinstance(i0, str) else _first(
            i0, 'original_image_url', 'originalImageUrl', 'resized_image_url', 'url', 'image')
    body = _first(item, 'snapshot.body.text', 'snapshot.body', 'adText', 'text', 'body')
    if isinstance(body, dict):
        body = body.get('text')
    start_raw = _first(item, 'start_date', 'startDate', 'startDateFormatted', 'ad_delivery_start_time')
    started = None
    if start_raw:
        started = str(start_raw)[:10]
        if started.isdigit():  # unix epoch
            from datetime import datetime, timezone
            started = datetime.fromtimestamp(int(start_raw), tz=timezone.utc).strftime('%Y-%m-%d')
    return {
        'ad_library_id': str(_first(item, 'ad_archive_id', 'adArchiveID', 'adArchiveId', 'archiveId',
                                    'id', default='') or '') or None,
        'page_name': _first(item, 'page_name', 'pageName', 'snapshot.page_name'),
        'format': fmt,
        'headline': (_first(item, 'snapshot.title', 'headline', 'title') or '')[:300] or None,
        'body': (body or '')[:4000] or None,
        'cta': _first(item, 'snapshot.cta_text', 'ctaText', 'snapshot.cta_type', 'ctaType'),
        'media_url': media_url,
        'landing_url': _first(item, 'snapshot.link_url', 'link_url', 'linkUrl', 'snapshot.caption'),
        'started_running': started,
        'is_active': 1 if _first(item, 'is_active', 'isActive', default=True) else 0,
        'raw_json': json.dumps(item)[:20000],
    }


def upsert_reference_ad(norm: dict, source: str, client_id: int = None,
                        competitor_id: int = None, platform: str = 'meta') -> tuple[int, bool]:
    """Insert or update by ad_library_id; returns (reference_ad_id, created)."""
    existing = None
    if norm.get('ad_library_id'):
        existing = db.row('SELECT id FROM reference_ads WHERE ad_library_id=?', (norm['ad_library_id'],))
    local_media = None
    if norm.get('media_url') and not existing:
        local_media = download_reference(norm['media_url'],
                                         f"{source}-{norm.get('page_name') or 'ad'}")
    if existing:
        db.execute('UPDATE reference_ads SET is_active=?, started_running=COALESCE(?, started_running), '
                   'landing_url=COALESCE(landing_url, ?) WHERE id=?',
                   (norm.get('is_active'), norm.get('started_running'), norm.get('landing_url'),
                    existing['id']))
        return existing['id'], False
    ref_id = db.execute(
        'INSERT INTO reference_ads (source, client_id, competitor_id, platform, page_name, ad_library_id, '
        'format, headline, body, cta, media_url, local_media_path, landing_url, started_running, '
        'is_active, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (source, client_id, competitor_id, platform, norm.get('page_name'), norm.get('ad_library_id'),
         norm.get('format'), norm.get('headline'), norm.get('body'), norm.get('cta'),
         norm.get('media_url'), local_media, norm.get('landing_url'), norm.get('started_running'),
         norm.get('is_active'), norm.get('raw_json')))
    return ref_id, True


def ad_library_search_input(query: str = None, page_url: str = None, country: str = 'ZA',
                            active_only: bool = True, limit: int = 50) -> dict:
    """Build input for curious_coder/facebook-ads-library-scraper.

    Two modes: an Ad Library keyword-search URL, or a Facebook page URL
    (the actor scrapes the page's ads directly, filtered by the dotted
    scrapePageAds.* options)."""
    status = 'active' if active_only else 'all'
    if page_url:
        return {
            'urls': [{'url': page_url}],
            'count': limit,
            'scrapeAdDetails': False,
            'scrapePageAds.activeStatus': status,
            'scrapePageAds.countryCode': country,
            'scrapePageAds.sortBy': 'most_recent',
        }
    url = ('https://www.facebook.com/ads/library/?active_status=%s&ad_type=all&country=%s'
           '&q=%s&search_type=keyword_unordered&media_type=all'
           % (status, country, requests.utils.quote(query or '')))
    return {'urls': [{'url': url}], 'count': limit, 'scrapeAdDetails': False}


def _to_date(raw) -> str | None:
    """Normalize a date-ish value (ISO string or unix epoch) to YYYY-MM-DD."""
    if raw in (None, '', []):
        return None
    text = str(raw)[:19]
    digits = str(raw).split('.')[0]
    if digits.isdigit():
        epoch = int(digits)
        if epoch > 1e12:  # milliseconds
            epoch //= 1000
        from datetime import datetime, timezone
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime('%Y-%m-%d')
    return text[:10]


# ================= Google Ads Transparency Center =================

def google_ads_search_input(query: str, country: str = 'ZA', active_only: bool = True,
                            limit: int = 50) -> dict:
    """Build input for scrapesage/google-ads-transparency-scraper.

    A keyword resolves to matching advertisers/domains via the Transparency
    Center autocomplete, then their ad creatives are scraped. The Center has
    no live active/inactive flag, so active_only maps to a recency window
    (ads shown in the last ~90 days) rather than a hard filter."""
    run_input = {
        'queries': [query],
        'resultType': 'ads',
        'region': (country or 'ZA').upper(),
        'adFormat': 'ALL',
        'maxAdsPerSearch': limit,
        'maxAdvertisersPerQuery': 5,
        'includeDetails': False,
    }
    if active_only:
        from datetime import datetime, timedelta, timezone
        run_input['startDate'] = (datetime.now(timezone.utc) - timedelta(days=90)).strftime('%Y-%m-%d')
    return run_input


def normalize_google_ad_item(item: dict) -> dict:
    """Map a Google Ads Transparency item to reference_ads columns (tolerant)."""
    fmt_raw = (_first(item, 'format', 'adFormat', 'creativeType', 'type', default='') or '').lower()
    fmt = 'video' if 'video' in fmt_raw else 'text' if 'text' in fmt_raw else 'image'
    media_url = _first(item, 'previewImageUrl', 'imageUrl', 'image', 'thumbnailUrl', 'thumbnail',
                       'videoThumbnailUrl', 'videoPreviewImageUrl', 'creativeImageUrl')
    body = _first(item, 'body', 'text', 'description', 'adText', 'content')
    if isinstance(body, dict):
        body = body.get('text')
    ad_id = _first(item, 'creativeId', 'adId', 'creative_id', 'ad_id', 'id')
    return {
        'ad_library_id': (f'google:{ad_id}' if ad_id else None),
        'page_name': _first(item, 'advertiserName', 'advertiser', 'advertiser_name', 'domain',
                            'brand', 'displayName'),
        'format': fmt,
        'headline': (_first(item, 'title', 'headline', 'adTitle') or '')[:300] or None,
        'body': (body or '')[:4000] or None,
        'cta': _first(item, 'cta', 'ctaText', 'callToAction'),
        'media_url': media_url,
        'landing_url': _first(item, 'finalUrl', 'destinationUrl', 'landingPageUrl', 'targetUrl',
                              'displayUrl'),
        'started_running': _to_date(_first(item, 'firstShown', 'firstShownDate', 'firstShownOn',
                                           'startDate', 'lastShown')),
        'is_active': None,
        'raw_json': json.dumps(item)[:20000],
    }


# ================= LinkedIn Ad Library =================

def linkedin_ads_search_input(query: str, country: str = 'ZA', active_only: bool = True,
                              limit: int = 50) -> dict:
    """Build input for s-r/linkedin-ads-library (keyword/company search).

    limit is unused by this actor (it returns the library's result set);
    active_only maps to the last-30-days recency window."""
    run_input = {'search': query, 'sort': 'NEWEST'}
    if country and country.upper() != 'ANYWHERE':
        run_input['country'] = country.upper()
    if active_only:
        run_input['date_range'] = 'last_30_days'
    return run_input


def normalize_linkedin_ad_item(item: dict) -> dict:
    """Map a LinkedIn Ad Library item to reference_ads columns (tolerant)."""
    type_raw = (_first(item, 'type', 'adType', 'format', 'creativeType', default='') or '').lower()
    fmt = 'video' if 'video' in type_raw else 'carousel' if 'carousel' in type_raw else 'image'
    media_url = _first(item, 'imageUrl', 'image', 'thumbnailUrl', 'thumbnail', 'creativeImageUrl',
                       'previewImageUrl', 'logoUrl', 'advertiserLogo')
    body = _first(item, 'body', 'text', 'commentary', 'description', 'adText', 'content')
    if isinstance(body, dict):
        body = body.get('text')
    ad_id = _first(item, 'adId', 'id', 'ad_id', 'creativeId', 'urn')
    return {
        'ad_library_id': (f'linkedin:{ad_id}' if ad_id else None),
        'page_name': _first(item, 'advertiser', 'companyName', 'company', 'advertiserName',
                            'pageName', 'author'),
        'format': fmt,
        'headline': (_first(item, 'headline', 'title', 'adTitle') or '')[:300] or None,
        'body': (body or '')[:4000] or None,
        'cta': _first(item, 'cta', 'ctaText', 'callToAction', 'ctaType'),
        'media_url': media_url,
        'landing_url': _first(item, 'landingPage', 'clickUri', 'destinationUrl', 'ctaUrl', 'link'),
        'started_running': _to_date(_first(item, 'firstShown', 'startDate', 'ranFrom', 'startedOn',
                                           'firstSeen', 'createdAt')),
        'is_active': 1 if _first(item, 'isActive', 'is_active', default=True) else 0,
        'raw_json': json.dumps(item)[:20000],
    }
