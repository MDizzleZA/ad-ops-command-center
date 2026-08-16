from datetime import date, datetime, timedelta

from fastapi import APIRouter

from app import db

router = APIRouter(prefix='/api', tags=['dashboard'])

# Organic/analytics platforms carry traffic, not paid spend: keep them out of paid totals
ORGANIC_PLATFORMS = ('ga4', 'gsc', 'gbp')


def _dates(date_from: str | None, date_to: str | None) -> tuple[str, str, str, str]:
    """Resolve range; previous period = equal-length window ending the day before."""
    to_d = datetime.strptime(date_to, '%Y-%m-%d').date() if date_to else date.today()
    from_d = datetime.strptime(date_from, '%Y-%m-%d').date() if date_from else to_d - timedelta(days=13)
    span = (to_d - from_d).days + 1
    prev_to = from_d - timedelta(days=1)
    prev_from = prev_to - timedelta(days=span - 1)
    return from_d.isoformat(), to_d.isoformat(), prev_from.isoformat(), prev_to.isoformat()


TOTALS_SQL = """
SELECT a.platform,
       SUM(m.spend) AS spend, SUM(m.impressions) AS impressions, SUM(m.clicks) AS clicks,
       SUM(m.leads) AS leads, SUM(m.conversions) AS conversions, SUM(m.revenue) AS revenue
FROM metrics_daily m JOIN ad_accounts a ON a.id = m.account_id
WHERE m.level = 'account' AND m.date BETWEEN ? AND ? {client_filter}
GROUP BY a.platform
"""


def _platform_totals(date_from: str, date_to: str, client_id: int | None) -> dict:
    sql = TOTALS_SQL.format(client_filter='AND a.client_id = ?' if client_id else '')
    params = [date_from, date_to] + ([client_id] if client_id else [])
    return {r['platform']: r for r in db.rows(sql, params)}


def _derive(t: dict) -> dict:
    spend, clicks, imps = t.get('spend') or 0, t.get('clicks') or 0, t.get('impressions') or 0
    leads, conv, rev = t.get('leads') or 0, t.get('conversions') or 0, t.get('revenue') or 0
    return {
        **{k: t.get(k) or 0 for k in ('spend', 'impressions', 'clicks', 'leads', 'conversions', 'revenue')},
        'ctr': round(clicks / imps * 100, 2) if imps else None,
        'cpc': round(spend / clicks, 2) if clicks else None,
        'cpl': round(spend / leads, 2) if leads else None,
        'cpm': round(spend / imps * 1000, 2) if imps else None,
        'roas': round(rev / spend, 2) if spend and rev else None,
    }


@router.get('/dashboard')
def dashboard(client_id: int | None = None, date_from: str | None = None, date_to: str | None = None):
    from_d, to_d, prev_from, prev_to = _dates(date_from, date_to)
    current = _platform_totals(from_d, to_d, client_id)
    previous = _platform_totals(prev_from, prev_to, client_id)

    paid_platforms = [p for p in current if p not in ORGANIC_PLATFORMS]
    platforms = {}
    for p in set(list(current.keys()) + list(previous.keys())):
        platforms[p] = {'current': _derive(current.get(p, {})), 'previous': _derive(previous.get(p, {}))}
    total_cur = _derive({k: sum((current.get(p) or {}).get(k) or 0 for p in paid_platforms)
                         for k in ('spend', 'impressions', 'clicks', 'leads', 'conversions', 'revenue')})
    total_prev = _derive({k: sum((previous.get(p) or {}).get(k) or 0 for p in paid_platforms)
                          for k in ('spend', 'impressions', 'clicks', 'leads', 'conversions', 'revenue')})

    series_sql = ("SELECT m.date, a.platform, SUM(m.spend) AS spend, SUM(m.leads) AS leads "
                  "FROM metrics_daily m JOIN ad_accounts a ON a.id=m.account_id "
                  "WHERE m.level='account' AND a.platform NOT IN ('ga4','gsc','gbp') AND m.date BETWEEN ? AND ? "
                  + ('AND a.client_id=? ' if client_id else '') + 'GROUP BY m.date, a.platform ORDER BY m.date')
    series = db.rows(series_sql, [from_d, to_d] + ([client_id] if client_id else []))

    camp_sql = ("SELECT c.name, a.platform, SUM(m.spend) AS spend, SUM(m.leads) AS leads, "
                "SUM(m.clicks) AS clicks, SUM(m.impressions) AS impressions "
                "FROM metrics_daily m JOIN ad_accounts a ON a.id=m.account_id "
                "JOIN campaigns c ON c.account_id=m.account_id AND c.external_id=m.entity_external_id "
                "WHERE m.level='campaign' AND m.date BETWEEN ? AND ? "
                + ('AND a.client_id=? ' if client_id else '')
                + 'GROUP BY c.id ORDER BY spend DESC LIMIT 10')
    top_campaigns = [dict(r, **{'cpl': round(r['spend'] / r['leads'], 2) if r['leads'] else None})
                     for r in db.rows(camp_sql, [from_d, to_d] + ([client_id] if client_id else []))]

    kpi = None
    if client_id:
        client = db.row('SELECT kpi_json FROM clients WHERE id=?', (client_id,))
        kpi = db.jloads(client['kpi_json']) if client else None

    return {'range': {'from': from_d, 'to': to_d, 'prev_from': prev_from, 'prev_to': prev_to},
            'total': {'current': total_cur, 'previous': total_prev},
            'platforms': platforms, 'series': series, 'top_campaigns': top_campaigns, 'kpi': kpi}


@router.get('/dashboard/aggregate')
def dashboard_aggregate(date_from: str | None = None, date_to: str | None = None):
    from_d, to_d, prev_from, prev_to = _dates(date_from, date_to)
    sql = ("SELECT cl.id AS client_id, cl.name, cl.status, "
           "SUM(m.spend) AS spend, SUM(m.leads) AS leads, SUM(m.clicks) AS clicks, "
           "SUM(m.impressions) AS impressions, SUM(m.conversions) AS conversions "
           "FROM clients cl LEFT JOIN ad_accounts a ON a.client_id=cl.id AND a.platform NOT IN ('ga4','gsc','gbp') "
           "LEFT JOIN metrics_daily m ON m.account_id=a.id AND m.level='account' AND m.date BETWEEN ? AND ? "
           "GROUP BY cl.id ORDER BY spend DESC")
    cards = []
    prev = {r['client_id']: r for r in db.rows(sql, (prev_from, prev_to))}
    for r in db.rows(sql, (from_d, to_d)):
        cards.append({'client_id': r['client_id'], 'name': r['name'], 'status': r['status'],
                      'current': _derive(r), 'previous': _derive(prev.get(r['client_id'], {}))})
    return {'range': {'from': from_d, 'to': to_d}, 'clients': cards}
