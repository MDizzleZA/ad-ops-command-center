import hashlib
import json

import requests as http_requests
from fastapi import APIRouter, HTTPException, Request, UploadFile

from app import db
from app.config import MEDIA_DIR
from app.services import gemini, overlay
from app.services.media import media_url

router = APIRouter(prefix='/api', tags=['cloner'])

LAYOUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'canvas_ratio': {'type': 'string'},
        'blocks': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'type': {'type': 'string',
                             'enum': ['headline', 'subhead', 'body', 'image', 'logo', 'cta',
                                      'disclaimer', 'badge', 'other']},
                    'position': {'type': 'string'},
                    'content': {'type': 'string'},
                    'style_notes': {'type': 'string'},
                },
                'required': ['type', 'position', 'content', 'style_notes'],
            },
        },
        'color_scheme': {'type': 'string'},
        'composition_notes': {'type': 'string'},
    },
    'required': ['canvas_ratio', 'blocks', 'color_scheme', 'composition_notes'],
}

ANALYZE_PROMPT = """Analyse this static ad's layout and creative structure so it can be recreated
re-skinned for another brand. Identify every visual block (headline, subhead, body copy, image subject,
logo, CTA button, disclaimer, badges), its position (e.g. 'top-left third', 'bottom bar'), its content
(the actual text, or a description for images), and style notes (font weight/size feel, colour, casing).
Describe the overall colour scheme and composition (grid, hierarchy, negative space, photography style)."""


def _save_upload_bytes(content: bytes, ext: str) -> str:
    name = f'clone-src-{hashlib.sha1(content).hexdigest()[:10]}.{ext}'
    path = MEDIA_DIR / 'uploads' / name
    path.write_bytes(content)
    return str(path)


@router.post('/cloner/ingest')
async def ingest(request: Request):
    """Accepts multipart (file), or JSON {url} / {reference_ad_id}. Runs layout analysis."""
    source_path = None
    reference_ad_id = None
    content_type = request.headers.get('content-type', '')

    if 'multipart/form-data' in content_type:
        form = await request.form()
        file = form.get('file')
        if file is None or not hasattr(file, 'read'):
            raise HTTPException(400, 'file required')
        ext = (file.filename or 'ad.png').rsplit('.', 1)[-1].lower()
        if ext not in ('png', 'jpg', 'jpeg', 'webp'):
            raise HTTPException(400, 'png/jpg/webp only')
        source_path = _save_upload_bytes(await file.read(), ext)
        reference_ad_id = form.get('reference_ad_id')
    else:
        payload = await request.json()
        if payload.get('reference_ad_id'):
            reference_ad_id = payload['reference_ad_id']
            ref = db.row('SELECT * FROM reference_ads WHERE id=?', (reference_ad_id,))
            if not ref:
                raise HTTPException(404, 'reference ad not found')
            source_path = ref['local_media_path']
            if not source_path and ref['media_url']:
                from app.services.media import download_reference
                source_path = download_reference(ref['media_url'], f'clone-ref-{reference_ad_id}')
                if source_path:
                    db.execute('UPDATE reference_ads SET local_media_path=? WHERE id=?',
                               (source_path, reference_ad_id))
        elif payload.get('url'):
            resp = http_requests.get(payload['url'], timeout=30)
            resp.raise_for_status()
            ctype = resp.headers.get('content-type', '')
            ext = 'png' if 'png' in ctype else 'jpg'
            if 'text/html' in ctype:
                raise HTTPException(400, 'URL must point directly to an image (right-click the ad '
                                         'image and copy its address)')
            source_path = _save_upload_bytes(resp.content, ext)
    if not source_path:
        raise HTTPException(400, 'no source image (upload a file, image URL, or reference ad with media)')

    layout = gemini.analyze_image(source_path, ANALYZE_PROMPT, schema=LAYOUT_SCHEMA)
    job_id = db.execute(
        'INSERT INTO clone_jobs (client_id, reference_ad_id, source_image_path, layout_json, status) '
        'VALUES (?,?,?,?,?)',
        (None, reference_ad_id, source_path, json.dumps(layout), 'analyzed'))
    return {'job_id': job_id, 'layout': layout, 'source_image': media_url(source_path)}


@router.post('/cloner/{job_id}/generate')
def generate(job_id: int, payload: dict):
    job = db.row('SELECT * FROM clone_jobs WHERE id=?', (job_id,))
    if not job:
        raise HTTPException(404, 'clone job not found')
    client_id = payload.get('client_id')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    offer = (payload.get('offer_text') or '').strip()
    variants = max(1, min(int(payload.get('variants', 1)), 4))
    layout = payload.get('layout') or db.jloads(job['layout_json'], {})
    overrides = payload.get('copy_overrides') or {}

    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (client_id,))
    client = db.row('SELECT * FROM clients WHERE id=?', (client_id,))
    colors = db.jloads((brand or {}).get('colors_json'), [])
    color_desc = ', '.join(f"{c['name']} {c['hex']} ({c.get('usage', c.get('role', ''))})" for c in colors)
    fonts = ', '.join(f['name'] for f in db.jloads((brand or {}).get('fonts_json'), []))

    blocks_desc = '\n'.join(
        f"- {b['type']} at {b['position']}: \"{overrides.get(b['type'], b['content'])}\" ({b['style_notes']})"
        for b in layout.get('blocks', []))
    ref_images = [p for p in (job['source_image_path'],
                              (brand or {}).get('logo_dark_path'), (brand or {}).get('logo_path'))
                  if p and not str(p).lower().endswith('.svg')]

    db.execute("UPDATE clone_jobs SET client_id=?, offer_text=?, variant_count=?, status='generating' "
               'WHERE id=?', (client_id, offer, variants, job_id))
    assets = []
    emphasis_cycle = ['primary brand colour dominant', 'light/cream background variant',
                      'dark background variant', 'photography-led variant']
    for i in range(variants):
        prompt = f"""The FIRST attached image is a reference ad. Recreate its exact layout and creative
structure as a NEW 1080x1080 square ad for the brand "{client['name']}", completely re-skinned:

LAYOUT TO CLONE:
Canvas: {layout.get('canvas_ratio', '1:1')}
{blocks_desc}
Composition: {layout.get('composition_notes', '')}

RE-SKIN WITH THIS BRAND:
Colours: {color_desc or 'professional palette'}
Fonts (approximate the feel): {fonts or 'modern sans-serif'}
Offer/message to communicate: {offer or 'brand awareness'}
Variant emphasis: {emphasis_cycle[i % len(emphasis_cycle)]}
{('Style rules: ' + brand['style_rules']) if brand and brand.get('style_rules') else ''}

Rules: keep the reference's structure and hierarchy but NOTHING of its brand may remain (no original
logos, brand names, or colours). If a later attached image is a logo, reserve clean space top-right for
it but do NOT draw it. Keep the bottom 12% visually quiet for a disclaimer bar. Do not render small
legal text. High-end agency finish, crisp text, no watermarks."""
        out_path = str(MEDIA_DIR / 'generated' / f'clone-{job_id}-v{i + 1}.png')
        try:
            gemini.gen_image(prompt, ref_image_paths=ref_images, out_path=out_path)
        except Exception as exc:
            db.execute("UPDATE clone_jobs SET status='error', error=? WHERE id=?", (str(exc), job_id))
            raise HTTPException(502, f'image generation failed on variant {i + 1}: {exc}')
        if brand and (brand.get('disclaimer_text') or brand.get('logo_dark_path')):
            overlay.apply_overlay(out_path, disclaimer=brand.get('disclaimer_text'),
                                  logo_path=brand.get('logo_dark_path') or brand.get('logo_path'))
        asset_id = db.execute(
            'INSERT INTO generated_assets (client_id, clone_job_id, kind, file_path, prompt, model) '
            'VALUES (?,?,?,?,?,?)',
            (client_id, job_id, 'image', out_path, prompt, db.setting('gemini_image_model')))
        assets.append({'id': asset_id, 'file': media_url(out_path)})
    db.execute("UPDATE clone_jobs SET status='done' WHERE id=?", (job_id,))
    return {'assets': assets}


@router.get('/cloner/jobs')
def list_jobs(client_id: int | None = None):
    where, params = ('WHERE client_id=? OR client_id IS NULL', [client_id]) if client_id else ('', [])
    jobs = db.rows(f'SELECT * FROM clone_jobs {where} ORDER BY id DESC LIMIT 50', params)
    for j in jobs:
        j['layout'] = db.jloads(j.pop('layout_json', None))
        j['source_image'] = media_url(j.pop('source_image_path', None))
    return jobs


@router.get('/assets')
def list_assets(client_id: int | None = None):
    where, params = ('WHERE client_id=?', [client_id]) if client_id else ('', [])
    assets = db.rows(f'SELECT * FROM generated_assets {where} ORDER BY id DESC LIMIT 100', params)
    for a in assets:
        a['file'] = media_url(a.pop('file_path', None))
    return assets
