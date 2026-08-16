import hashlib
import json
import threading

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app import db
from app.config import MEDIA_DIR
from app.services import landing, persona_miner, pipeline
from app.services.media import media_url

router = APIRouter(prefix='/api', tags=['pipeline'])


def _ad_out(ad: dict) -> dict:
    for ratio in ('1x1', '4x5', '9x16'):
        ad[f'image_{ratio}'] = media_url(ad.pop(f'image_{ratio}_path', None))
    comp = db.jloads(ad.pop('compliance_json', None))
    ad['compliance_status'] = comp.get('status') if comp else None
    ad['compliance'] = comp
    return ad


@router.get('/pipeline/ads')
def list_ads(client_id: int, batch_date: str | None = None, limit: int = 60):
    where, params = ['client_id=?'], [client_id]
    if batch_date:
        where.append('batch_date=?')
        params.append(batch_date)
    ads = db.rows(f"SELECT * FROM daily_ads WHERE {' AND '.join(where)} "
                  'ORDER BY id DESC LIMIT ?', params + [limit])
    return {'generating': pipeline.is_generating(client_id), 'ads': [_ad_out(a) for a in ads]}


@router.post('/pipeline/generate')
def generate(payload: dict):
    client_id = payload.get('client_id')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    if pipeline.is_generating(client_id):
        raise HTTPException(409, 'a batch is already generating for this client')
    count = payload.get('count')
    auto_images = bool(payload.get('auto_images'))
    thread = threading.Thread(target=_generate_bg, args=(client_id, count, auto_images), daemon=True)
    thread.start()
    return {'started': True}


def _generate_bg(client_id: int, count, auto_images: bool):
    try:
        pipeline.generate_batch(client_id, count=count, auto_images=auto_images)
    except Exception:
        pass  # per-batch errors surface via the (empty) list + logs


@router.post('/pipeline/ads/{ad_id}/feedback')
def feedback(ad_id: int, payload: dict):
    vote = payload.get('vote')
    if vote not in (1, -1, 0):
        raise HTTPException(400, 'vote must be 1, -1 or 0')
    ad = db.row('SELECT id FROM daily_ads WHERE id=?', (ad_id,))
    if not ad:
        raise HTTPException(404, 'ad not found')
    db.execute('UPDATE daily_ads SET feedback=?, feedback_note=? WHERE id=?',
               (vote, (payload.get('note') or '')[:300] or None, ad_id))
    return {'ok': True}


@router.post('/pipeline/ads/{ad_id}/image')
def gen_image(ad_id: int, payload: dict):
    ratio = payload.get('ratio', '1x1')
    try:
        path = pipeline.generate_ratio_image(ad_id, ratio)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {'ratio': ratio, 'image': media_url(path)}


@router.post('/pipeline/ads/{ad_id}/to-brief')
def to_brief(ad_id: int):
    ad = db.row('SELECT * FROM daily_ads WHERE id=?', (ad_id,))
    if not ad:
        raise HTTPException(404, 'ad not found')
    brief_data = {
        'hook': ad['angle'], 'headline': ad['headline'], 'primary_text': ad['primary_text'],
        'cta': ad['cta'], 'visual_direction': ad['visual_direction'],
        'format_spec': '1080x1080 / 1080x1350 / 1080x1920',
        'compliance_notes': f"Daily pipeline ad ({ad['awareness_stage']})",
    }
    brief_id = db.execute(
        'INSERT INTO briefs (client_id, title, axis, axis_value, brief_json, compliance_json, status) '
        'VALUES (?,?,?,?,?,?,?)',
        (ad['client_id'], f"Pipeline: {(ad['headline'] or ad['angle'] or 'daily ad')[:60]}",
         'hook', ad['awareness_stage'], json.dumps(brief_data), ad['compliance_json'], 'draft'))
    return {'brief_id': brief_id}


# ================= Product images (for compositing) =================

@router.get('/pipeline/product-images')
def product_images(client_id: int):
    brand = db.row('SELECT product_images_json FROM brand_profiles WHERE client_id=?', (client_id,))
    paths = db.jloads((brand or {}).get('product_images_json'), [])
    return [{'path': p, 'url': media_url(p)} for p in paths]


@router.post('/pipeline/product-images')
async def upload_product_image(file: UploadFile = File(...), client_id: int = Form(...)):
    suffix = (file.filename or 'product.png').rsplit('.', 1)[-1].lower()
    if suffix not in ('png', 'jpg', 'jpeg', 'webp'):
        raise HTTPException(400, 'image files only')
    brand = db.row('SELECT id, product_images_json FROM brand_profiles WHERE client_id=?', (client_id,))
    if not brand:
        raise HTTPException(404, 'client has no brand profile yet - seed or create one first')
    content = await file.read()
    name = f"product-{client_id}-{hashlib.sha1(content).hexdigest()[:10]}.{suffix}"
    path = MEDIA_DIR / 'uploads' / name
    path.write_bytes(content)
    paths = db.jloads(brand.get('product_images_json'), [])
    if str(path) not in paths:
        paths.append(str(path))
    db.execute('UPDATE brand_profiles SET product_images_json=? WHERE client_id=?',
               (json.dumps(paths), client_id))
    return {'path': str(path), 'url': media_url(str(path))}


@router.delete('/pipeline/product-images')
def delete_product_image(client_id: int, path: str):
    brand = db.row('SELECT product_images_json FROM brand_profiles WHERE client_id=?', (client_id,))
    paths = db.jloads((brand or {}).get('product_images_json'), [])
    if path in paths:
        paths.remove(path)
        db.execute('UPDATE brand_profiles SET product_images_json=? WHERE client_id=?',
                   (json.dumps(paths), client_id))
    return {'ok': True}


# ================= Personas =================

@router.get('/personas')
def personas(client_id: int):
    rows = db.rows('SELECT * FROM personas WHERE client_id=? ORDER BY id DESC', (client_id,))
    for p in rows:
        for col in ('demographics_json', 'pain_points_json', 'triggers_json', 'objections_json'):
            p[col.replace('_json', '')] = db.jloads(p.pop(col, None), [])
    return {'mining': persona_miner.is_mining(client_id), 'personas': rows}


@router.post('/personas/mine')
def mine_personas(payload: dict):
    client_id = payload.get('client_id')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    if persona_miner.is_mining(client_id):
        raise HTTPException(409, 'a mining run is already in progress for this client')
    keywords = [k.strip() for k in (payload.get('keywords') or '').split(',') if k.strip()]
    review_urls = [u.strip() for u in (payload.get('review_urls') or '').split() if u.strip()]
    thread = threading.Thread(
        target=_mine_bg, args=(client_id, keywords, review_urls, int(payload.get('count') or 3)),
        daemon=True)
    thread.start()
    return {'started': True}


def _mine_bg(client_id: int, keywords, review_urls, count: int):
    try:
        persona_miner.mine(client_id, keywords=keywords, review_urls=review_urls, persona_count=count)
    except Exception:
        pass  # failures visible via apify_runs / empty result


@router.delete('/personas/{persona_id}')
def delete_persona(persona_id: int):
    db.execute('DELETE FROM personas WHERE id=?', (persona_id,))
    return {'ok': True}


# ================= Landing pages =================

@router.get('/landing')
def landing_list(client_id: int | None = None, competitor_id: int | None = None):
    return landing.list_analyses(client_id=client_id, competitor_id=competitor_id)


@router.post('/landing/analyze')
def landing_analyze(payload: dict):
    url = payload.get('url')
    ref_id = payload.get('reference_ad_id')
    competitor_id = payload.get('competitor_id')
    if ref_id and not url:
        ref = db.row('SELECT landing_url, competitor_id FROM reference_ads WHERE id=?', (ref_id,))
        if not ref or not ref.get('landing_url'):
            raise HTTPException(404, 'reference ad has no landing URL')
        url = ref['landing_url']
        competitor_id = competitor_id or ref.get('competitor_id')
    if not url:
        raise HTTPException(400, 'url or reference_ad_id required')
    try:
        return landing.analyze_url(url, client_id=payload.get('client_id'),
                                   competitor_id=competitor_id, reference_ad_id=ref_id,
                                   force=bool(payload.get('force')))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(400, f'landing analysis failed: {exc}')
