from fastapi import APIRouter, HTTPException

from app import db
from app.services import fatigue
from app.services.media import media_url

router = APIRouter(prefix='/api', tags=['creatives'])


@router.get('/creatives')
def list_creatives(client_id: int | None = None, platform: str | None = None,
                   format: str | None = None, date_from: str | None = None,
                   date_to: str | None = None, sort: str = 'spend'):
    where, params = ['1=1'], []
    if client_id:
        where.append('a.client_id=?')
        params.append(client_id)
    if platform:
        where.append('a.platform=?')
        params.append(platform)
    if format:
        where.append('c.format=?')
        params.append(format)
    date_clause, date_params = '', []
    if date_from and date_to:
        date_clause = 'AND m.date BETWEEN ? AND ?'
        date_params = [date_from, date_to]

    sort_col = {'spend': 'spend DESC', 'cpl': 'cpl ASC', 'ctr': 'ctr DESC'}.get(sort, 'spend DESC')
    sql = f"""
    SELECT c.id, c.account_id, c.ad_external_id, c.name, c.format, c.headline, c.body, c.cta,
           c.landing_url, c.thumbnail_path, c.status, a.platform, a.client_id, cl.name AS client_name,
           COALESCE(SUM(m.spend),0) AS spend, COALESCE(SUM(m.impressions),0) AS impressions,
           COALESCE(SUM(m.clicks),0) AS clicks, COALESCE(SUM(m.leads),0) AS leads,
           AVG(m.frequency) AS frequency,
           CASE WHEN SUM(m.impressions)>0 THEN ROUND(SUM(m.clicks)*100.0/SUM(m.impressions),2) END AS ctr,
           CASE WHEN SUM(m.leads)>0 THEN ROUND(SUM(m.spend)/SUM(m.leads),2) END AS cpl,
           CASE WHEN SUM(m.impressions)>0 THEN ROUND(SUM(m.spend)*1000.0/SUM(m.impressions),2) END AS cpm
    FROM creatives c
    JOIN ad_accounts a ON a.id=c.account_id
    JOIN clients cl ON cl.id=a.client_id
    LEFT JOIN metrics_daily m ON m.account_id=c.account_id AND m.level='ad'
         AND m.entity_external_id=c.ad_external_id {date_clause}
    WHERE {' AND '.join(where)}
    GROUP BY c.id ORDER BY {sort_col} NULLS LAST LIMIT 200"""
    creatives = db.rows(sql, date_params + params)

    account_ids = list({c['account_id'] for c in creatives})
    cpl_target = None
    if client_id:
        client = db.row('SELECT kpi_json FROM clients WHERE id=?', (client_id,))
        kpi = db.jloads(client['kpi_json']) if client else None
        cpl_target = (kpi or {}).get('cpl_target', {}).get('blended')
    scores = fatigue.score_creatives(account_ids, cpl_target) if account_ids else {}

    for c in creatives:
        c['thumbnail'] = media_url(c.pop('thumbnail_path', None))
        score = scores.get((c['account_id'], c['ad_external_id']))
        c['badge'] = score['badge'] if score else None
        c['badge_reason'] = score['reason'] if score else None
    return {'creatives': creatives}


@router.get('/creatives/{creative_id}/timeseries')
def creative_timeseries(creative_id: int, date_from: str | None = None, date_to: str | None = None):
    creative = db.row('SELECT * FROM creatives WHERE id=?', (creative_id,))
    if not creative:
        raise HTTPException(404, 'creative not found')
    where, params = '', [creative['account_id'], creative['ad_external_id']]
    if date_from and date_to:
        where = 'AND date BETWEEN ? AND ?'
        params += [date_from, date_to]
    series = db.rows(
        f"SELECT date, spend, impressions, clicks, leads, "
        f"CASE WHEN impressions>0 THEN ROUND(clicks*100.0/impressions,2) END AS ctr "
        f"FROM metrics_daily WHERE account_id=? AND level='ad' AND entity_external_id=? {where} "
        f"ORDER BY date", params)
    return {'series': series}


@router.post('/creatives/{creative_id}/to-reference')
def creative_to_reference(creative_id: int, payload: dict):
    """Turn an own creative into a reference ad, optionally opening a clone job."""
    target = payload.get('target', 'brief')
    creative = db.row(
        'SELECT c.*, a.client_id FROM creatives c JOIN ad_accounts a ON a.id=c.account_id WHERE c.id=?',
        (creative_id,))
    if not creative:
        raise HTTPException(404, 'creative not found')
    ref_id = db.execute(
        'INSERT INTO reference_ads (source, client_id, platform, page_name, format, headline, body, cta, '
        'local_media_path, tags) VALUES (?,?,?,?,?,?,?,?,?,?)',
        ('creative', creative['client_id'], 'meta', creative['name'], creative['format'],
         creative['headline'], creative['body'], creative['cta'], creative['thumbnail_path'],
         'own-creative'))
    result = {'reference_ad_id': ref_id}
    if target == 'cloner':
        job_id = db.execute(
            'INSERT INTO clone_jobs (client_id, reference_ad_id, source_image_path) VALUES (?,?,?)',
            (creative['client_id'], ref_id, creative['thumbnail_path']))
        result['clone_job_id'] = job_id
    return result
