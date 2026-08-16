import json

from fastapi import APIRouter, HTTPException

from app import db
from app.config import MEDIA_DIR
from app.services import compliance, gemini, overlay
from app.services.media import media_url

router = APIRouter(prefix='/api', tags=['briefs'])

VARIANTS_SCHEMA = {
    'type': 'object',
    'properties': {
        'variants': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'axis_value': {'type': 'string'},
                    'hook': {'type': 'string'},
                    'primary_text': {'type': 'string'},
                    'headline': {'type': 'string'},
                    'cta': {'type': 'string'},
                    'visual_direction': {'type': 'string'},
                    'format_spec': {'type': 'string'},
                    'compliance_notes': {'type': 'string'},
                },
                'required': ['axis_value', 'hook', 'primary_text', 'headline', 'cta',
                             'visual_direction', 'format_spec', 'compliance_notes'],
            },
        },
    },
    'required': ['variants'],
}

AXIS_INSTRUCTIONS = {
    'hook': 'Generate 5 variants, each with a DIFFERENT opening hook/angle (curiosity, data point, '
            'contrarian, question, plain-spoken). Keep the body message consistent with the reference; '
            'axis_value = a 2-4 word name for the hook angle.',
    'persona': 'Generate one variant PER PERSONA listed below - rewrite the whole ad (hook, body, CTA) '
               'to speak to that persona\'s specific pains, triggers and objections; '
               'axis_value = the persona name.',
    'pain_point': 'Generate one variant PER PAIN POINT listed below - reframe the ad around that single '
                  'customer problem; axis_value = a short name for the pain point.',
    'visual_format': 'Generate 4 variants adapting the concept to: static image, carousel, short video '
                     '(15-30s script with scene beats in primary_text), and UGC-style creator video. '
                     'axis_value = the format name; format_spec = exact dimensions/duration/slide count.',
    'asset_type': 'Generate one variant per placement asset: feed image 1080x1080, story/reel 1080x1920, '
                  'LinkedIn single image, and lead-form intro copy. axis_value = the asset name; '
                  'format_spec = exact specs.',
}


def _brand_block(client_id: int) -> str:
    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (client_id,))
    client = db.row('SELECT * FROM clients WHERE id=?', (client_id,))
    if not brand:
        return f"## BRAND\nClient: {client['name']} ({client['industry'] or 'unknown industry'})"
    colors = ', '.join(f"{c['name']} {c['hex']}" for c in db.jloads(brand['colors_json'], []))
    return f"""## BRAND ({client['name']})
Industry: {client['industry']}
Tagline: {brand['tagline']}
Tone of voice: {brand['tone_of_voice']}
Colours: {colors}
Style rules: {brand['style_rules']}
Mandatory disclaimer (must appear in every variant's primary_text or compliance_notes): {brand['disclaimer_text']}"""


def _personas_block(client_id: int) -> str:
    personas = db.rows('SELECT * FROM personas WHERE client_id=?', (client_id,))
    if not personas:
        return ''
    parts = ['## PERSONAS']
    for p in personas:
        pains = '; '.join(db.jloads(p['pain_points_json'], []))
        triggers = '; '.join(db.jloads(p['triggers_json'], []))
        objections = '; '.join(db.jloads(p['objections_json'], []))
        parts.append(f"- {p['headline']}\n  Pains: {pains}\n  Triggers: {triggers}\n  Objections: {objections}")
    return '\n'.join(parts)


def _reference_block(reference_ad_id: int | None, source_text: str | None) -> str:
    if reference_ad_id:
        ref = db.row('SELECT * FROM reference_ads WHERE id=?', (reference_ad_id,))
        if not ref:
            raise HTTPException(404, 'reference ad not found')
        return (f"## REFERENCE AD (from {ref['page_name'] or ref['source']})\n"
                f"Headline: {ref['headline'] or '-'}\nBody: {ref['body'] or '-'}\nCTA: {ref['cta'] or '-'}")
    if source_text:
        return f'## REFERENCE CONCEPT (pasted by strategist)\n{source_text[:3000]}'
    raise HTTPException(400, 'reference_ad_id or source_text required')


@router.post('/briefs/iterate')
def iterate(payload: dict):
    client_id = payload.get('client_id')
    axis = payload.get('axis')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    if axis not in AXIS_INSTRUCTIONS:
        raise HTTPException(400, f'axis must be one of {list(AXIS_INSTRUCTIONS)}')

    blocks = [
        'You are a senior creative strategist at a South African performance-marketing agency, '
        'producing structured ad briefs ready to hand to a designer/editor.',
        _reference_block(payload.get('reference_ad_id'), payload.get('source_text')),
        _brand_block(client_id),
    ]
    if axis in ('persona', 'pain_point'):
        blocks.append(_personas_block(client_id))
    blocks.append(f'## TASK\n{AXIS_INSTRUCTIONS[axis]}')
    blocks.append('Every variant needs: a scroll-stopping hook, platform-ready primary_text (with any '
                  'mandatory disclaimers), a headline under 40 characters, a CTA, concrete visual_direction '
                  'a designer can execute (composition, imagery, on-image text), format_spec, and '
                  'compliance_notes explaining how the variant satisfies the rules.')
    blocks.append(compliance.prompt_block(client_id))

    result = gemini.gen_text('\n\n'.join(blocks), schema=VARIANTS_SCHEMA)
    return {'variants': result.get('variants', [])}


@router.get('/briefs')
def list_briefs(client_id: int | None = None):
    where, params = ('WHERE client_id=?', [client_id]) if client_id else ('', [])
    briefs = db.rows(f'SELECT id, title, axis, axis_value, status, created_at, reference_ad_id, '
                     f'compliance_json FROM briefs {where} ORDER BY id DESC LIMIT 100', params)
    for b in briefs:
        comp = db.jloads(b.pop('compliance_json', None))
        b['compliance_status'] = comp.get('status') if comp else None
    return briefs


@router.get('/briefs/{brief_id}')
def get_brief(brief_id: int):
    brief = db.row('SELECT * FROM briefs WHERE id=?', (brief_id,))
    if not brief:
        raise HTTPException(404, 'brief not found')
    brief['brief'] = db.jloads(brief.pop('brief_json', None), {})
    brief['compliance'] = db.jloads(brief.pop('compliance_json', None))
    if brief.get('reference_ad_id'):
        ref = db.row('SELECT * FROM reference_ads WHERE id=?', (brief['reference_ad_id'],))
        if ref:
            ref['media'] = media_url(ref.get('local_media_path')) or ref.get('media_url')
        brief['reference_ad'] = ref
    assets = db.rows('SELECT * FROM generated_assets WHERE brief_id=? ORDER BY id DESC', (brief_id,))
    for a in assets:
        a['file'] = media_url(a.pop('file_path', None))
    brief['assets'] = assets
    return brief


@router.post('/briefs')
def save_brief(payload: dict):
    client_id = payload.get('client_id')
    brief_data = payload.get('brief') or {}
    if not client_id or not brief_data:
        raise HTTPException(400, 'client_id and brief required')
    brief_id = db.execute(
        'INSERT INTO briefs (client_id, reference_ad_id, title, axis, axis_value, brief_json, status) '
        'VALUES (?,?,?,?,?,?,?)',
        (client_id, payload.get('reference_ad_id'),
         payload.get('title') or brief_data.get('headline') or 'Untitled brief',
         payload.get('axis'), payload.get('axis_value'), json.dumps(brief_data), 'draft'))
    result = compliance.audit_and_store(brief_id)
    return {'id': brief_id, 'compliance': result}


@router.put('/briefs/{brief_id}')
def update_brief(brief_id: int, payload: dict):
    brief = db.row('SELECT * FROM briefs WHERE id=?', (brief_id,))
    if not brief:
        raise HTTPException(404, 'brief not found')
    status = payload.get('status')
    if status not in ('draft', 'approved', 'rejected'):
        raise HTTPException(400, 'status must be draft, approved or rejected')
    if status == 'approved':
        comp = db.jloads(brief['compliance_json'])
        if not comp:
            raise HTTPException(409, 'run a compliance check before approving')
        if comp.get('status') == 'block':
            raise HTTPException(409, 'compliance BLOCK - fix the violations and re-check before approving')
    db.execute('UPDATE briefs SET status=? WHERE id=?', (status, brief_id))
    return {'ok': True, 'status': status}


@router.post('/briefs/{brief_id}/compliance-check')
def compliance_check(brief_id: int):
    return compliance.audit_and_store(brief_id)


@router.post('/briefs/{brief_id}/generate-image')
def generate_image(brief_id: int):
    brief = db.row('SELECT * FROM briefs WHERE id=?', (brief_id,))
    if not brief:
        raise HTTPException(404, 'brief not found')
    data = db.jloads(brief['brief_json'], {})
    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (brief['client_id'],))
    colors = ', '.join(f"{c['name']} ({c['hex']})" for c in db.jloads((brand or {}).get('colors_json'), []))
    prompt = f"""Create a premium 1:1 square social media ad image (1080x1080).
Visual direction: {data.get('visual_direction') or 'clean, professional financial services aesthetic'}
On-image headline text: "{data.get('headline') or data.get('hook') or ''}"
Brand colours to use: {colors or 'deep purple #32174D, warm cream #EFE7BD'}
Style: {(brand or {}).get('style_rules') or 'professional, aspirational, photographic'}
Leave the bottom 12% of the image visually quiet (a disclaimer bar will be composited there).
Do NOT render any small legal text, logos, or disclaimers - these are added separately."""
    out_path = str(MEDIA_DIR / 'generated' / f'brief-{brief_id}-{abs(hash(prompt)) % 99999}.png')
    gemini.gen_image(prompt, out_path=out_path)
    if brand and (brand.get('disclaimer_text') or brand.get('logo_dark_path')):
        overlay.apply_overlay(out_path, disclaimer=brand.get('disclaimer_text'),
                              logo_path=brand.get('logo_dark_path') or brand.get('logo_path'))
    asset_id = db.execute(
        'INSERT INTO generated_assets (client_id, brief_id, kind, file_path, prompt, model) '
        'VALUES (?,?,?,?,?,?)',
        (brief['client_id'], brief_id, 'image', out_path, prompt,
         db.setting('gemini_image_model')))
    return {'asset_id': asset_id, 'file': media_url(out_path)}
