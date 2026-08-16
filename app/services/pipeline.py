"""Daily psychology-based creative pipeline (Eugene Schwartz awareness stages).

Generates a batch of ads per client - one per awareness stage - as structured
copy (headline / primary text / description / CTA) plus AI images in the three
paid-social aspect ratios (1:1, 4:5, 9:16). Product assets from the brand
profile are composited into generations via Gemini reference images; the FSP
disclaimer bar and logo stay deterministic (overlay.py). Thumbs up/down
feedback is fed back into subsequent prompts as a learning loop.
"""
import json
from datetime import date
from pathlib import Path

from PIL import Image

from app import db
from app.config import MEDIA_DIR
from app.services import compliance, gemini, overlay

AWARENESS_STAGES = {
    'unaware': 'The reader does not yet know they have the problem. Lead with a striking fact, '
               'story or emotion that surfaces the hidden problem. Never mention the product.',
    'problem_aware': 'The reader feels the pain but does not know solutions exist. Agitate the '
                     'specific pain, then hint that a way out exists. No product names yet.',
    'solution_aware': 'The reader knows solutions like this exist but not this brand. Position the '
                      'category benefit and differentiate the approach; introduce the brand late.',
    'product_aware': 'The reader knows the brand but is not convinced. Lead with proof, specifics, '
                     'differentiators and objection handling.',
    'most_aware': 'The reader is ready - they need the right offer and a low-friction next step. '
                  'Direct offer, urgency where compliant, crystal-clear CTA.',
}

RATIOS = {
    '1x1': (1080, 1080),
    '4x5': (1080, 1350),
    '9x16': (1080, 1920),
}

BATCH_SCHEMA = {
    'type': 'object',
    'properties': {
        'ads': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'awareness_stage': {'type': 'string', 'enum': list(AWARENESS_STAGES)},
                    'angle': {'type': 'string'},
                    'headline': {'type': 'string'},
                    'primary_text': {'type': 'string'},
                    'description': {'type': 'string'},
                    'cta': {'type': 'string'},
                    'visual_direction': {'type': 'string'},
                    'image_prompt': {'type': 'string'},
                },
                'required': ['awareness_stage', 'angle', 'headline', 'primary_text', 'description',
                             'cta', 'visual_direction', 'image_prompt'],
            },
        },
    },
    'required': ['ads'],
}

# in-flight batch generations, keyed by client_id (module-level; single process)
_generating: set[int] = set()


def is_generating(client_id: int) -> bool:
    return client_id in _generating


def _brand_block(client_id: int) -> str:
    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (client_id,))
    client = db.row('SELECT * FROM clients WHERE id=?', (client_id,))
    if not brand:
        return f"## BRAND\nClient: {client['name']} ({client['industry'] or 'unknown industry'})"
    colors = ', '.join(f"{c['name']} {c['hex']}" for c in db.jloads(brand['colors_json'], []))
    return (f"## BRAND ({client['name']})\nIndustry: {client['industry']}\n"
            f"Tagline: {brand['tagline']}\nTone of voice: {brand['tone_of_voice']}\n"
            f"Colours: {colors}\nStyle rules: {brand['style_rules']}\n"
            f"Mandatory disclaimer context: {brand['disclaimer_text']}")


def _personas_block(client_id: int) -> str:
    personas = db.rows('SELECT * FROM personas WHERE client_id=?', (client_id,))
    if not personas:
        return ''
    parts = ['## PERSONAS (write for these buyers)']
    for p in personas:
        pains = '; '.join(db.jloads(p['pain_points_json'], []))
        triggers = '; '.join(db.jloads(p['triggers_json'], []))
        parts.append(f"- {p['headline'] or p['name']}\n  Pains: {pains}\n  Triggers: {triggers}")
    return '\n'.join(parts)


def _feedback_block(client_id: int) -> str:
    """Learning loop: recent thumbs-up ads to emulate, thumbs-down to avoid."""
    liked = db.rows('SELECT headline, primary_text, awareness_stage FROM daily_ads '
                    'WHERE client_id=? AND feedback=1 ORDER BY id DESC LIMIT 5', (client_id,))
    disliked = db.rows('SELECT headline, primary_text, feedback_note FROM daily_ads '
                       'WHERE client_id=? AND feedback=-1 ORDER BY id DESC LIMIT 5', (client_id,))
    if not liked and not disliked:
        return ''
    parts = ['## FEEDBACK FROM THE STRATEGIST (learning loop)']
    if liked:
        parts.append('Ads rated GOOD - emulate this style and sharpness:')
        parts += [f"  + [{a['awareness_stage']}] {a['headline']} | {(a['primary_text'] or '')[:160]}"
                  for a in liked]
    if disliked:
        parts.append('Ads rated BAD - avoid these patterns:')
        parts += [f"  - {a['headline']} | {(a['primary_text'] or '')[:120]}"
                  + (f" (note: {a['feedback_note']})" if a['feedback_note'] else '')
                  for a in disliked]
    return '\n'.join(parts)


def generate_batch(client_id: int, count: int = None, auto_images: bool = False) -> list[int]:
    """Generate today's batch of ads for a client. Returns the new daily_ads ids."""
    if client_id in _generating:
        raise RuntimeError('a batch is already generating for this client')
    _generating.add(client_id)
    try:
        return _generate_batch_inner(client_id, count, auto_images)
    finally:
        _generating.discard(client_id)


def _generate_batch_inner(client_id: int, count: int = None, auto_images: bool = False) -> list[int]:
    count = count or int(db.setting('pipeline_ads_per_day', '5'))
    stages = list(AWARENESS_STAGES)[:count] if count <= 5 else list(AWARENESS_STAGES)
    stage_lines = '\n'.join(f'- {s}: {AWARENESS_STAGES[s]}' for s in stages)
    blocks = [
        'You are a direct-response copywriter trained on Eugene Schwartz\'s "Breakthrough Advertising". '
        'Write one paid-social ad per awareness stage listed below, for the brand that follows. '
        'South African English. Platform-ready for Meta/LinkedIn feeds.',
        f'## AWARENESS STAGES ({len(stages)} ads, one each)\n{stage_lines}',
        _brand_block(client_id),
        _personas_block(client_id),
        _feedback_block(client_id),
        'Per ad: angle = 3-6 word name for the psychological angle; headline under 40 characters; '
        'primary_text 40-120 words with line breaks, hook in the first sentence, ending with the '
        'mandatory disclaimer verbatim on its own line (every asset must carry it); description under '
        '25 words; cta = platform CTA button text; visual_direction a designer could execute; '
        'image_prompt = a full text-to-image prompt for the visual (scene, subject, mood, colours, '
        'composition - NO text/logos/disclaimers in the image). No superlatives or claims you cannot '
        'substantiate in the ad itself.',
        compliance.prompt_block(client_id),
    ]
    result = gemini.gen_text('\n\n'.join(b for b in blocks if b), schema=BATCH_SCHEMA)
    today = date.today().isoformat()
    ids = []
    for ad in result.get('ads', [])[:count]:
        ad_id = db.execute(
            'INSERT INTO daily_ads (client_id, batch_date, awareness_stage, angle, headline, '
            'primary_text, description, cta, visual_direction, image_prompt) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (client_id, today, ad.get('awareness_stage') or 'unaware', ad.get('angle'),
             ad.get('headline'), ad.get('primary_text'), ad.get('description'), ad.get('cta'),
             ad.get('visual_direction'), ad.get('image_prompt')))
        ids.append(ad_id)
        try:
            text = '\n'.join(str(ad.get(k) or '') for k in ('headline', 'primary_text', 'description', 'cta'))
            audit = compliance.audit(text, client_id)
            db.execute('UPDATE daily_ads SET compliance_json=? WHERE id=?', (json.dumps(audit), ad_id))
        except Exception:
            pass  # audit failure shouldn't kill the batch; UI shows "unchecked"
        if auto_images:
            try:
                generate_ratio_image(ad_id, '1x1')
            except Exception:
                pass
    return ids


def _fit_to_size(path: str, size: tuple) -> None:
    """Centre-crop to the target aspect then resize to exact dimensions."""
    img = Image.open(path).convert('RGB')
    target_w, target_h = size
    target_aspect = target_w / target_h
    w, h = img.size
    aspect = w / h
    if abs(aspect - target_aspect) > 0.01:
        if aspect > target_aspect:  # too wide
            new_w = int(h * target_aspect)
            x = (w - new_w) // 2
            img = img.crop((x, 0, x + new_w, h))
        else:  # too tall
            new_h = int(w / target_aspect)
            y = (h - new_h) // 2
            img = img.crop((0, y, 0 + w, y + new_h))
    if img.size != size:
        img = img.resize(size, Image.LANCZOS)
    img.save(path, 'PNG')


def generate_ratio_image(ad_id: int, ratio: str) -> str:
    """Generate (or regenerate) the ad's image for one aspect ratio. Returns media path."""
    if ratio not in RATIOS:
        raise ValueError(f'ratio must be one of {list(RATIOS)}')
    ad = db.row('SELECT * FROM daily_ads WHERE id=?', (ad_id,))
    if not ad:
        raise ValueError('ad not found')
    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (ad['client_id'],)) or {}
    colors = ', '.join(f"{c['name']} ({c['hex']})" for c in db.jloads(brand.get('colors_json'), []))
    size = RATIOS[ratio]
    orientation = {'1x1': 'square 1:1', '4x5': 'vertical 4:5 portrait', '9x16': 'vertical 9:16 story'}[ratio]

    product_paths = [p for p in db.jloads(brand.get('product_images_json'), []) if Path(p).exists()]
    product_note = ('Feature the product from the supplied reference image(s) naturally in the scene '
                    '- keep its shape, label and colours accurate.' if product_paths else '')
    prompt = f"""Create a premium {orientation} social media ad image ({size[0]}x{size[1]}).
{ad['image_prompt'] or ad['visual_direction'] or 'clean, professional brand visual'}
{product_note}
Brand colours: {colors or 'neutral premium palette'}
Style: {brand.get('style_rules') or 'professional, aspirational, photographic'}
Leave the bottom 12% visually quiet (a disclaimer bar is composited there).
Do NOT render any text, logos, or disclaimers - these are added separately."""

    out_path = str(MEDIA_DIR / 'generated' / f'daily-{ad_id}-{ratio}.png')
    gemini.gen_image(prompt, ref_image_paths=product_paths, out_path=out_path)
    _fit_to_size(out_path, size)
    if brand.get('disclaimer_text') or brand.get('logo_dark_path') or brand.get('logo_path'):
        overlay.apply_overlay(out_path, disclaimer=brand.get('disclaimer_text'),
                              logo_path=brand.get('logo_dark_path') or brand.get('logo_path'),
                              target_size=None)
    db.execute(f'UPDATE daily_ads SET image_{ratio}_path=? WHERE id=?', (out_path, ad_id))
    return out_path


def run_daily_batches():
    """Scheduler entry: generate the daily batch for every enabled client."""
    raw = db.setting('pipeline_enabled_clients', '')
    for token in raw.replace(';', ',').split(','):
        token = token.strip()
        if not token.isdigit():
            continue
        client_id = int(token)
        today = date.today().isoformat()
        exists = db.row('SELECT 1 AS x FROM daily_ads WHERE client_id=? AND batch_date=? LIMIT 1',
                        (client_id, today))
        if exists:
            continue
        try:
            generate_batch(client_id, auto_images=True)
        except Exception:
            pass  # per-client failures shouldn't stop the loop
