"""CSV import fallback: platform export files -> metrics_daily / campaigns.

Two modes:
- metrics: rows carry a date column plus metric columns (daily performance export)
- structure: rows are campaigns (Google Ads Editor export) - names/status/budget only

`mapping` maps our field names to CSV column headers. The google_ads_editor
preset matches the Sample Client export files in the vault.
"""
import csv
import io

from app.sync import base

PRESETS = {
    'google_ads_editor': {
        'mode': 'structure',
        'mapping': {'campaign_name': 'Campaign', 'status': 'Status', 'budget': 'Budget'},
    },
    'meta_daily': {
        'mode': 'metrics',
        'mapping': {'date': 'Day', 'campaign_name': 'Campaign name', 'spend': 'Amount spent (ZAR)',
                    'impressions': 'Impressions', 'clicks': 'Link clicks', 'leads': 'Leads',
                    'reach': 'Reach'},
    },
    'google_daily': {
        'mode': 'metrics',
        'mapping': {'date': 'Day', 'campaign_name': 'Campaign', 'spend': 'Cost',
                    'impressions': 'Impr.', 'clicks': 'Clicks', 'conversions': 'Conversions'},
    },
    'linkedin_daily': {
        'mode': 'metrics',
        'mapping': {'date': 'Start Date (in UTC)', 'campaign_name': 'Campaign Name',
                    'spend': 'Total Spent', 'impressions': 'Impressions', 'clicks': 'Clicks',
                    'leads': 'Leads'},
    },
}

NUMERIC_FIELDS = ('spend', 'impressions', 'clicks', 'leads', 'conversions', 'revenue', 'reach')


def _num(value) -> float:
    if value is None:
        return 0
    cleaned = str(value).replace('R', '').replace('ZAR', '').replace(',', '').replace('%', '').strip()
    if cleaned in ('', '--', '-'):
        return 0
    try:
        return float(cleaned)
    except ValueError:
        return 0


def _normalize_date(value: str) -> str | None:
    value = (value or '').strip()[:10]
    if not value:
        return None
    if '/' in value:  # assume yyyy/mm/dd or dd/mm/yyyy
        parts = value.split('/')
        if len(parts[0]) == 4:
            return '-'.join(parts)
        return f'{parts[2]}-{parts[1]}-{parts[0]}'
    return value


def import_csv(account_id: int, content: bytes, preset: str = None, mapping: dict = None,
               mode: str = None) -> dict:
    if preset:
        cfg = PRESETS.get(preset)
        if not cfg:
            raise ValueError(f'Unknown preset: {preset}')
        mapping = cfg['mapping']
        mode = cfg['mode']
    if not mapping:
        raise ValueError('mapping or preset required')
    mode = mode or 'metrics'

    text = content.decode('utf-8-sig', errors='replace')
    reader = csv.DictReader(io.StringIO(text))
    rows_written = skipped = 0

    for row in reader:
        get = lambda field: row.get(mapping.get(field, ''), '')
        name = (get('campaign_name') or '').strip()
        if mode == 'structure':
            if not name or name.startswith('Total'):
                skipped += 1
                continue
            extid = (get('campaign_id') or name).strip()
            base.upsert_campaign(account_id, extid, name, status=(get('status') or '').strip() or None)
            rows_written += 1
            continue
        day = _normalize_date(get('date'))
        if not day:
            skipped += 1
            continue
        metrics = {f: _num(get(f)) for f in NUMERIC_FIELDS}
        entity = (get('campaign_id') or name).strip()
        level = 'campaign' if entity else 'account'
        if entity and name:
            base.upsert_campaign(account_id, entity, name)
        base.upsert_metric(account_id, level, entity, day, **metrics)
        rows_written += 1

    return {'rows_written': rows_written, 'skipped': skipped, 'mode': mode}
