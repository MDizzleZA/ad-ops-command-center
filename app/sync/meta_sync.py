"""Meta Ads sync: daily account/campaign/ad-level insights + creative metadata.

Credentials come from Windows Credential Manager (service 'adops-meta-ads',
same store the meta-ads skill uses), falling back to META_* env vars from
~/.adops/.env.
"""
import json
import os

from app.services.media import download_thumbnail
from app.sync import base

SERVICE = 'adops-meta-ads'
INSIGHT_FIELDS = ['spend', 'impressions', 'clicks', 'reach', 'frequency', 'actions',
                  'video_thruplay_watched_actions', 'date_start']
LEAD_ACTIONS = {'lead', 'leadgen_grouped', 'onsite_conversion.lead_grouped',
                'offsite_conversion.fb_pixel_lead'}

_api_initialized = False


def _init_api():
    global _api_initialized
    if _api_initialized:
        return
    import keyring
    from facebook_business.api import FacebookAdsApi
    app_id = keyring.get_password(SERVICE, 'app_id') or os.environ.get('META_APP_ID')
    app_secret = keyring.get_password(SERVICE, 'app_secret') or os.environ.get('META_APP_SECRET')
    token = keyring.get_password(SERVICE, 'access_token') or os.environ.get('META_ACCESS_TOKEN')
    if not token:
        raise RuntimeError('Meta credentials not found in Credential Manager (adops-meta-ads) or META_* env')
    FacebookAdsApi.init(app_id, app_secret, token)
    _api_initialized = True


def _leads_from_actions(row) -> float:
    total = 0.0
    for action in row.get('actions') or []:
        if action.get('action_type') in LEAD_ACTIONS:
            total += float(action.get('value', 0))
    return total


def _metric_kwargs(row) -> dict:
    actions = {a.get('action_type'): float(a.get('value', 0)) for a in (row.get('actions') or [])}
    video_views = 0
    for v in row.get('video_thruplay_watched_actions') or []:
        video_views += int(float(v.get('value', 0)))
    return dict(
        spend=float(row.get('spend', 0) or 0),
        impressions=int(row.get('impressions', 0) or 0),
        clicks=int(row.get('clicks', 0) or 0),
        reach=int(row.get('reach', 0) or 0),
        frequency=float(row['frequency']) if row.get('frequency') else None,
        leads=_leads_from_actions(row),
        conversions=actions.get('offsite_conversion.fb_pixel_purchase', 0) or actions.get('purchase', 0),
        video_views=video_views,
        extra_json=json.dumps({'actions': actions}) if actions else None,
    )


def _insights(entity, level: str, date_from: str, date_to: str, extra_fields=()):
    params = {
        'level': level,
        'time_range': {'since': date_from, 'until': date_to},
        'time_increment': 1,
        'limit': 500,
    }
    return entity.get_insights(fields=INSIGHT_FIELDS + list(extra_fields), params=params)


def sync(account: dict, date_from: str, date_to: str) -> int:
    _init_api()
    from facebook_business.adobjects.adaccount import AdAccount

    acct = AdAccount(account['external_id'])
    account_id = account['id']
    rows_written = 0

    for row in _insights(acct, 'account', date_from, date_to):
        base.upsert_metric(account_id, 'account', '', row['date_start'], **_metric_kwargs(row))
        rows_written += 1

    for row in _insights(acct, 'campaign', date_from, date_to, ('campaign_id', 'campaign_name')):
        base.upsert_campaign(account_id, row['campaign_id'], row.get('campaign_name'))
        base.upsert_metric(account_id, 'campaign', row['campaign_id'], row['date_start'], **_metric_kwargs(row))
        rows_written += 1

    for row in _insights(acct, 'ad', date_from, date_to,
                         ('ad_id', 'ad_name', 'adset_id', 'campaign_id')):
        base.upsert_metric(account_id, 'ad', row['ad_id'], row['date_start'], **_metric_kwargs(row))
        base.upsert_creative(account_id, row['ad_id'], name=row.get('ad_name'),
                             adset_external_id=row.get('adset_id'),
                             campaign_external_id=row.get('campaign_id'))
        rows_written += 1

    rows_written += sync_creatives(account)
    return rows_written


def sync_creatives(account: dict) -> int:
    """Fetch ad creative metadata (headline/body/thumbnail) for ads seen in insights."""
    _init_api()
    from facebook_business.adobjects.adaccount import AdAccount

    acct = AdAccount(account['external_id'])
    account_id = account['id']
    count = 0
    ads = acct.get_ads(fields=['id', 'name', 'status', 'creative{id,title,body,thumbnail_url,'
                               'object_story_spec,call_to_action_type,object_type,video_id}'],
                       params={'limit': 200})
    for ad in ads:
        creative = ad.get('creative') or {}
        story = creative.get('object_story_spec') or {}
        link_data = story.get('link_data') or {}
        video_data = story.get('video_data') or {}
        fmt = 'video' if (creative.get('video_id') or video_data) else (
            'carousel' if link_data.get('child_attachments') else 'image')
        headline = creative.get('title') or link_data.get('name') or video_data.get('title')
        body = creative.get('body') or link_data.get('message') or story.get('message')
        cta = (link_data.get('call_to_action') or video_data.get('call_to_action') or {}).get('type') \
            or creative.get('call_to_action_type')
        thumb_url = creative.get('thumbnail_url')
        thumb_path = download_thumbnail(thumb_url, f"meta-{ad['id']}") if thumb_url else None
        base.upsert_creative(account_id, ad['id'], name=ad.get('name'), status=ad.get('status'),
                             format=fmt, headline=headline, body=body, cta=cta,
                             landing_url=link_data.get('link'), thumbnail_url=thumb_url,
                             thumbnail_path=thumb_path)
        count += 1
    return count
