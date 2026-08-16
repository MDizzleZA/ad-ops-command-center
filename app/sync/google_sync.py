"""Google Ads sync: daily account + campaign metrics via GAQL.

Credentials load from ~/google-ads.yaml (GoogleAdsClient.load_from_storage), the
same file the google-ads-editor skill uses. The seeded ad_accounts row may carry
the placeholder external_id 'google-ads-yaml'; in that case the real customer id
is resolved at runtime via CustomerService.ListAccessibleCustomers (first
non-manager account, falling back to login_customer_id from the yaml).

Lead-gen account: leads and conversions are both set to metrics.conversions.
Account-level daily rows are aggregated in Python from the campaign rows.
"""
import json
from collections import defaultdict
from pathlib import Path

from app.sync import base

GOOGLE_ADS_YAML = Path.home() / 'google-ads.yaml'

_QUERY_TEMPLATE = (
    'SELECT segments.date, campaign.id, campaign.name, campaign.status, '
    'metrics.cost_micros, metrics.impressions, metrics.clicks, metrics.conversions '
    "FROM campaign WHERE segments.date BETWEEN '{date_from}' AND '{date_to}'"
)


def _auth_error(exc: Exception) -> RuntimeError:
    """Map token/credential failures to an actionable error for sync_runs."""
    text = str(exc)
    if 'invalid_grant' in text or 'RefreshError' in type(exc).__name__ or 'expired' in text.lower():
        return RuntimeError(
            'Google Ads OAuth token expired — re-run OAuth flow for ~/google-ads.yaml '
            f'(underlying error: {text[:200]})')
    return RuntimeError(f'Google Ads API error: {text[:400]}')


def _load_client():
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError as exc:
        raise RuntimeError('google-ads library not installed — run: pip install google-ads') from exc
    if not GOOGLE_ADS_YAML.exists():
        raise RuntimeError(f'Google Ads config not found at {GOOGLE_ADS_YAML} — '
                           'set up credentials per the google-ads-editor skill (api-setup.md)')
    try:
        return GoogleAdsClient.load_from_storage(str(GOOGLE_ADS_YAML))
    except Exception as exc:
        raise RuntimeError(f'Failed to load Google Ads credentials from {GOOGLE_ADS_YAML}: {exc}') from exc


def _resolve_customer_id(client, external_id: str) -> str:
    """Return a concrete 10-digit customer id for the account row.

    A 10-digit external_id (dashes allowed) is used as-is. Otherwise (e.g. the
    'google-ads-yaml' placeholder) accessible customers are listed and the first
    non-manager account wins, falling back to the yaml's login_customer_id.
    """
    ext = (external_id or '').replace('-', '').strip()
    if ext.isdigit() and len(ext) == 10:
        return ext

    # Guard: the "first accessible customer" fallback is only safe when there is a
    # single Google account to sync. With several, it would silently pull one
    # customer's data into every account (observed 2026-07-07). Require explicit ids.
    from app import db
    google_accounts = db.rows("SELECT external_id FROM ad_accounts WHERE platform='google' AND sync_enabled=1")
    non_numeric = [a for a in google_accounts
                   if not (a['external_id'] or '').replace('-', '').strip().isdigit()]
    if len(google_accounts) > 1 and len(non_numeric) > 1:
        raise RuntimeError(
            f"Ambiguous Google customer id: external_id {external_id!r} is a placeholder and "
            f"{len(non_numeric)} google accounts lack a real 10-digit customer id. Set each "
            "account's external_id to its Google Ads customer id (Settings > accounts).")

    try:
        customer_service = client.get_service('CustomerService')
        accessible = customer_service.list_accessible_customers()
    except Exception as exc:
        raise _auth_error(exc) from exc

    ga_service = client.get_service('GoogleAdsService')
    candidate_ids = [rn.split('/')[-1] for rn in accessible.resource_names]
    for cid in candidate_ids:
        try:
            response = ga_service.search(
                customer_id=cid, query='SELECT customer.id, customer.manager FROM customer')
            for row in response:
                if not row.customer.manager:
                    return cid
        except Exception:
            continue  # inaccessible/cancelled account — try the next one

    login_cid = str(getattr(client, 'login_customer_id', '') or '').replace('-', '').strip()
    if login_cid.isdigit():
        return login_cid
    raise RuntimeError(
        'Could not resolve a Google Ads customer id: no non-manager account among '
        f'{candidate_ids or "(none accessible)"} and no login_customer_id in {GOOGLE_ADS_YAML}. '
        "Set the account's external_id to the 10-digit customer id.")


def sync(account: dict, date_from: str, date_to: str) -> int:
    """Pull daily campaign metrics via GAQL; write campaign + aggregated account rows."""
    client = _load_client()
    from google.ads.googleads.errors import GoogleAdsException

    account_id = account['id']
    customer_id = _resolve_customer_id(client, account['external_id'])
    query = _QUERY_TEMPLATE.format(date_from=date_from, date_to=date_to)

    ga_service = client.get_service('GoogleAdsService')
    rows_written = 0
    account_totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {'spend': 0.0, 'impressions': 0, 'clicks': 0, 'conversions': 0.0})
    campaigns_seen: dict[str, tuple[str, str]] = {}

    try:
        response = ga_service.search_stream(customer_id=customer_id, query=query)
        for batch in response:
            for row in batch.results:
                day = row.segments.date
                campaign_id = str(row.campaign.id)
                spend = row.metrics.cost_micros / 1e6
                conversions = float(row.metrics.conversions)

                campaigns_seen[campaign_id] = (row.campaign.name, row.campaign.status.name)
                base.upsert_metric(
                    account_id, 'campaign', campaign_id, day,
                    spend=spend, impressions=int(row.metrics.impressions),
                    clicks=int(row.metrics.clicks), leads=conversions, conversions=conversions)
                rows_written += 1

                totals = account_totals[day]
                totals['spend'] += spend
                totals['impressions'] += int(row.metrics.impressions)
                totals['clicks'] += int(row.metrics.clicks)
                totals['conversions'] += conversions
    except GoogleAdsException as exc:
        messages = '; '.join(e.message for e in exc.failure.errors) or str(exc)
        raise RuntimeError(
            f'Google Ads query failed for customer {customer_id}: {messages}') from exc
    except RuntimeError:
        raise
    except Exception as exc:
        raise _auth_error(exc) from exc

    for campaign_id, (name, status) in campaigns_seen.items():
        base.upsert_campaign(account_id, campaign_id, name, status=status)

    for day, totals in account_totals.items():
        base.upsert_metric(
            account_id, 'account', '', day,
            spend=totals['spend'], impressions=totals['impressions'],
            clicks=totals['clicks'], leads=totals['conversions'],
            conversions=totals['conversions'],
            extra_json=json.dumps({'customer_id': customer_id}))
        rows_written += 1

    return rows_written
