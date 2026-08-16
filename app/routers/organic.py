from collections import defaultdict

from fastapi import APIRouter

from app import db
from app.routers.dashboard import _dates

router = APIRouter(prefix='/api', tags=['organic'])

ROWS_SQL = """
SELECT m.date, a.platform, m.leads, m.conversions, m.revenue, m.clicks, m.impressions, m.extra_json
FROM metrics_daily m JOIN ad_accounts a ON a.id = m.account_id
WHERE m.level = 'account' AND a.platform IN ('ga4','gsc','gbp')
  AND m.date BETWEEN ? AND ? {client_filter}
ORDER BY m.date
"""


def _fetch(date_from: str, date_to: str, client_id: int | None) -> dict[str, list[dict]]:
    sql = ROWS_SQL.format(client_filter='AND a.client_id = ?' if client_id else '')
    params = [date_from, date_to] + ([client_id] if client_id else [])
    grouped: dict[str, list[dict]] = defaultdict(list)
    for r in db.rows(sql, params):
        r['extra'] = db.jloads(r.pop('extra_json'), {}) or {}
        grouped[r['platform']].append(r)
    return grouped


def _sum(rows: list[dict], *keys: str) -> dict:
    return {k: sum(r.get(k) or 0 for r in rows) for k in keys}


def _ga4_section(rows: list[dict], prev_rows: list[dict]) -> dict:
    def totals(rs):
        t = _sum(rs, 'leads', 'conversions', 'revenue')
        for k in ('sessions', 'users', 'pageviews'):
            t[k] = sum(r['extra'].get(k) or 0 for r in rs)
        return t

    channels: dict[str, int] = defaultdict(int)
    devices: dict[str, int] = defaultdict(int)
    landing: dict[str, int] = defaultdict(int)
    series = []
    for r in rows:
        ex = r['extra']
        series.append({'date': r['date'], 'sessions': ex.get('sessions') or 0,
                       'users': ex.get('users') or 0, 'pageviews': ex.get('pageviews') or 0,
                       'leads': r['leads'] or 0, 'key_events': r['conversions'] or 0,
                       'revenue': r['revenue'] or 0})
        for name, n in (ex.get('channels') or {}).items():
            channels[name] += n
        for name, n in (ex.get('devices') or {}).items():
            devices[name] += n
        for entry in ex.get('landing_pages') or []:
            landing[entry['path']] += entry['sessions']
    top_landing = sorted(landing.items(), key=lambda kv: kv[1], reverse=True)[:10]
    return {'series': series,
            'totals': {'current': totals(rows), 'previous': totals(prev_rows)},
            'channels': dict(channels), 'devices': dict(devices),
            'top_landing_pages': [{'path': p, 'sessions': s} for p, s in top_landing]}


def _gsc_totals(rows: list[dict]) -> dict:
    t = _sum(rows, 'clicks', 'impressions')
    t['ctr'] = round(t['clicks'] / t['impressions'] * 100, 2) if t['impressions'] else None
    weighted = sum((r['extra'].get('position') or 0) * (r['impressions'] or 0) for r in rows)
    t['position'] = round(weighted / t['impressions'], 1) if t['impressions'] else None
    return t


def _gsc_merge_top(rows: list[dict], list_key: str, id_key: str) -> list[dict]:
    """Merge per-day top lists over the range: sum clicks/impressions per key,
    impression-weighted average position."""
    merged: dict[str, dict] = {}
    for r in rows:
        for entry in r['extra'].get(list_key) or []:
            key = entry.get(id_key)
            if key is None:
                continue
            slot = merged.setdefault(key, {'clicks': 0, 'impressions': 0, '_pos_weight': 0.0})
            slot['clicks'] += entry.get('clicks') or 0
            slot['impressions'] += entry.get('impressions') or 0
            slot['_pos_weight'] += (entry.get('position') or 0) * (entry.get('impressions') or 0)
    out = []
    for key, slot in merged.items():
        imps = slot['impressions']
        out.append({id_key: key, 'clicks': slot['clicks'], 'impressions': imps,
                    'ctr': round(slot['clicks'] / imps * 100, 2) if imps else None,
                    'position': round(slot['_pos_weight'] / imps, 1) if imps else None})
    return sorted(out, key=lambda e: (e['clicks'], e['impressions']), reverse=True)[:10]


def _gsc_section(rows: list[dict], prev_rows: list[dict]) -> dict:
    series = [{'date': r['date'], 'clicks': r['clicks'] or 0,
               'impressions': r['impressions'] or 0,
               'position': r['extra'].get('position')} for r in rows]
    return {'series': series,
            'totals': {'current': _gsc_totals(rows), 'previous': _gsc_totals(prev_rows)},
            'top_queries': _gsc_merge_top(rows, 'top_queries', 'q'),
            'top_pages': _gsc_merge_top(rows, 'top_pages', 'page')}


def _gbp_section(rows: list[dict], prev_rows: list[dict]) -> dict:
    def totals(rs):
        t = {'calls': 0, 'direction_requests': 0, 'website_clicks': 0, 'impressions': 0}
        for r in rs:
            ex = r['extra']
            t['calls'] += ex.get('calls') or 0
            t['direction_requests'] += ex.get('direction_requests') or 0
            t['website_clicks'] += ex.get('website_clicks') or 0
            t['impressions'] += r['impressions'] or 0
        return t

    series = [{'date': r['date'], 'calls': r['extra'].get('calls') or 0,
               'direction_requests': r['extra'].get('direction_requests') or 0,
               'website_clicks': r['extra'].get('website_clicks') or 0,
               'impressions': r['impressions'] or 0} for r in rows]
    return {'series': series,
            'totals': {'current': totals(rows), 'previous': totals(prev_rows)}}


@router.get('/organic')
def organic(client_id: int | None = None, date_from: str | None = None, date_to: str | None = None):
    from_d, to_d, prev_from, prev_to = _dates(date_from, date_to)
    current = _fetch(from_d, to_d, client_id)
    previous = _fetch(prev_from, prev_to, client_id)
    return {'range': {'from': from_d, 'to': to_d, 'prev_from': prev_from, 'prev_to': prev_to},
            'ga4': _ga4_section(current.get('ga4', []), previous.get('ga4', [])),
            'gsc': _gsc_section(current.get('gsc', []), previous.get('gsc', [])),
            'gbp': _gbp_section(current.get('gbp', []), previous.get('gbp', []))}
