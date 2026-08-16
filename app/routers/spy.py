import threading

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app import db
from app.config import MEDIA_DIR
from app.services import apify
from app.services.media import media_url

router = APIRouter(prefix='/api', tags=['spy'])


def _ad_out(ad: dict) -> dict:
    ad['media'] = media_url(ad.get('local_media_path')) or ad.get('media_url')
    return ad


@router.get('/reference-ads')
def list_reference_ads(client_id: int | None = None, source: str | None = None,
                       competitor_id: int | None = None, search: str | None = None):
    where, params = ['1=1'], []
    if client_id:
        where.append('(client_id=? OR client_id IS NULL)')
        params.append(client_id)
    if source:
        where.append('source=?')
        params.append(source)
    if competitor_id:
        where.append('competitor_id=?')
        params.append(competitor_id)
    if search:
        where.append('(page_name LIKE ? OR headline LIKE ? OR body LIKE ?)')
        params += [f'%{search}%'] * 3
    ads = db.rows(f"SELECT * FROM reference_ads WHERE {' AND '.join(where)} "
                  'ORDER BY saved_at DESC LIMIT 200', params)
    return [_ad_out(a) for a in ads]


@router.post('/reference-ads')
def add_reference_ad(payload: dict):
    ref_id = db.execute(
        'INSERT INTO reference_ads (source, client_id, platform, page_name, headline, body, cta, media_url) '
        'VALUES (?,?,?,?,?,?,?,?)',
        ('upload', payload.get('client_id'), payload.get('platform', 'meta'), payload.get('page_name'),
         payload.get('headline'), payload.get('body'), payload.get('cta'), payload.get('media_url')))
    return {'id': ref_id}


@router.post('/reference-ads/upload')
async def upload_reference_ad(file: UploadFile = File(...), page_name: str = Form(None),
                              headline: str = Form(None), body: str = Form(None),
                              cta: str = Form(None), client_id: int = Form(None)):
    suffix = (file.filename or 'ad.png').rsplit('.', 1)[-1].lower()
    if suffix not in ('png', 'jpg', 'jpeg', 'webp', 'gif'):
        raise HTTPException(400, 'image files only')
    content = await file.read()
    import hashlib
    name = f"upload-{hashlib.sha1(content).hexdigest()[:10]}.{suffix}"
    path = MEDIA_DIR / 'uploads' / name
    path.write_bytes(content)
    ref_id = db.execute(
        'INSERT INTO reference_ads (source, client_id, platform, page_name, headline, body, cta, '
        'local_media_path) VALUES (?,?,?,?,?,?,?,?)',
        ('upload', client_id, 'meta', page_name, headline, body, cta, str(path)))
    return {'id': ref_id}


# platform -> (actor setting key, input builder, item normalizer)
_SPY_PLATFORMS = {
    'meta': ('apify_actor_ad_library', apify.ad_library_search_input, apify.normalize_ad_item),
    'google': ('apify_actor_google_ads', apify.google_ads_search_input, apify.normalize_google_ad_item),
    'linkedin': ('apify_actor_linkedin_ads', apify.linkedin_ads_search_input,
                 apify.normalize_linkedin_ad_item),
}


def _run_spy_search(run_row_id: int, client_id: int | None, platform: str):
    normalize = _SPY_PLATFORMS[platform][2]
    run = apify.wait_for_run(run_row_id)
    if run['status'] != 'done':
        return
    items = apify.dataset_items(run['dataset_id'])
    db.execute('UPDATE apify_runs SET items=? WHERE id=?', (len(items), run_row_id))
    for item in items:
        norm = normalize(item)
        ref_id, _ = apify.upsert_reference_ad(norm, source='spy', client_id=client_id,
                                              platform=platform)
        db.execute('UPDATE reference_ads SET tags=? WHERE id=? AND (tags IS NULL OR tags="")',
                   (f'spy-run-{run_row_id}', ref_id))


@router.post('/spy/search')
def spy_search(payload: dict):
    query = (payload.get('query') or '').strip()
    if not query:
        raise HTTPException(400, 'query required')
    platform = (payload.get('platform') or 'meta').lower()
    if platform not in _SPY_PLATFORMS:
        raise HTTPException(400, f'unsupported platform: {platform}')
    actor_key, build_input, _ = _SPY_PLATFORMS[platform]
    country = payload.get('country') or db.setting('default_country', 'ZA')
    actor = db.setting(actor_key)
    if not actor:
        raise HTTPException(400, f'No Apify actor configured for {platform} (Settings > {actor_key}).')
    run_input = build_input(query=query, country=country,
                            active_only=payload.get('active_only', True))
    run_row_id = apify.start_run(actor, run_input, purpose=f'spy-{platform}')
    thread = threading.Thread(target=_run_spy_search,
                              args=(run_row_id, payload.get('client_id'), platform), daemon=True)
    thread.start()
    return {'run_id': run_row_id}


@router.get('/spy/runs')
def spy_runs(limit: int = 20):
    return db.rows("SELECT * FROM apify_runs WHERE purpose LIKE 'spy%' ORDER BY id DESC LIMIT ?",
                   (limit,))


@router.get('/spy/runs/{run_row_id}')
def spy_run(run_row_id: int):
    run = db.row('SELECT * FROM apify_runs WHERE id=?', (run_row_id,))
    if not run:
        raise HTTPException(404, 'run not found')
    ads = db.rows("SELECT * FROM reference_ads WHERE tags=? ORDER BY id DESC",
                  (f'spy-run-{run_row_id}',))
    return {'run': run, 'ads': [_ad_out(a) for a in ads]}


@router.post('/spy/save')
def spy_save(payload: dict):
    ref_id = payload.get('reference_ad_id')
    target = payload.get('target', 'brief')
    client_id = payload.get('client_id')
    ref = db.row('SELECT * FROM reference_ads WHERE id=?', (ref_id,))
    if not ref:
        raise HTTPException(404, 'reference ad not found')
    if client_id and not ref['client_id']:
        db.execute('UPDATE reference_ads SET client_id=? WHERE id=?', (client_id, ref_id))
    if target == 'cloner':
        job_id = db.execute(
            'INSERT INTO clone_jobs (client_id, reference_ad_id, source_image_path) VALUES (?,?,?)',
            (client_id or ref['client_id'], ref_id, ref['local_media_path']))
        return {'clone_job_id': job_id}
    brief_id = db.execute(
        'INSERT INTO briefs (client_id, reference_ad_id, title, status) VALUES (?,?,?,?)',
        (client_id or ref['client_id'], ref_id,
         f"Ref: {(ref['headline'] or ref['page_name'] or 'saved ad')[:60]}", 'reference'))
    return {'brief_id': brief_id}
