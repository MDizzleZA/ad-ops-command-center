"""Competitor landing page extraction: fetch the page behind an ad, distil strategy.

Runs automatically for a capped number of new landing URLs per competitor
during ad scans, and on demand from the UI. Analysis (offer, hooks, CTA
structure, form asks, social proof) is stored in landing_pages.
"""
import json
import re

import requests

from app import db
from app.services.persona_miner import _html_to_text

ANALYSIS_SCHEMA = {
    'type': 'object',
    'properties': {
        'offer': {'type': 'string'},
        'hook_headline': {'type': 'string'},
        'key_claims': {'type': 'array', 'items': {'type': 'string'}},
        'cta_structure': {'type': 'array', 'items': {'type': 'string'}},
        'form_fields': {'type': 'array', 'items': {'type': 'string'}},
        'social_proof': {'type': 'array', 'items': {'type': 'string'}},
        'compliance_notes': {'type': 'string'},
        'takeaways': {'type': 'array', 'items': {'type': 'string'}},
    },
    'required': ['offer', 'hook_headline', 'key_claims', 'cta_structure', 'takeaways'],
}


def _clean_url(url: str) -> str | None:
    if not url or not url.lower().startswith(('http://', 'https://')):
        return None
    # strip tracking params for dedupe (keep the path + non-utm query)
    url = re.sub(r'([?&])(utm_[a-z]+|fbclid|gclid|msclkid|li_fat_id)=[^&#]*', r'\1', url)
    return re.sub(r'[?&]+$', '', url)[:500]


def analyze_url(url: str, client_id: int = None, competitor_id: int = None,
                reference_ad_id: int = None, force: bool = False) -> dict:
    """Fetch + analyse a landing page. Reuses a prior analysis of the same URL unless force."""
    from app.services import gemini
    url = _clean_url(url)
    if not url:
        raise ValueError('a valid http(s) landing URL is required')
    existing = db.row('SELECT * FROM landing_pages WHERE url=? AND status=\'done\' '
                      'ORDER BY id DESC LIMIT 1', (url,))
    if existing and not force:
        existing['analysis'] = db.jloads(existing.pop('analysis_json', None), {})
        existing['cached'] = True
        return existing

    row_id = db.execute(
        'INSERT INTO landing_pages (client_id, competitor_id, reference_ad_id, url, status) '
        'VALUES (?,?,?,?,?)', (client_id, competitor_id, reference_ad_id, url, 'fetching'))
    try:
        resp = requests.get(url, timeout=30, allow_redirects=True,
                            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        resp.raise_for_status()
        text = _html_to_text(resp.text)[:30000]
        if len(text) < 300:
            raise RuntimeError('page returned too little text (likely JS-rendered or blocked)')
        prompt = f"""You are a CRO/paid-media strategist reverse-engineering a competitor landing page.
Analyse the page text below and extract the strategy:
- offer: the core offer/value proposition in one sentence
- hook_headline: the main headline as written (or closest equivalent)
- key_claims: the specific claims/benefits used to persuade
- cta_structure: every call-to-action and where it sits in the flow
- form_fields: what the lead form asks for (if any)
- social_proof: testimonials, numbers, awards, trust badges mentioned
- compliance_notes: any regulatory framing (FSP numbers, disclaimers, T&Cs) - or what's missing
- takeaways: 3-5 concrete things we could test or do better on our own pages

PAGE URL: {url}
PAGE TEXT:
{text}"""
        analysis = gemini.gen_text(prompt, schema=ANALYSIS_SCHEMA)
        db.execute("UPDATE landing_pages SET status='done', analysis_json=?, raw_chars=? WHERE id=?",
                   (json.dumps(analysis), len(text), row_id))
        result = db.row('SELECT * FROM landing_pages WHERE id=?', (row_id,))
        result['analysis'] = analysis
        result['cached'] = False
        return result
    except Exception as exc:
        db.execute("UPDATE landing_pages SET status='error', error=? WHERE id=?",
                   (f'{exc.__class__.__name__}: {exc}'[:400], row_id))
        raise


def extract_for_competitor(client_id: int, competitor_id: int) -> int:
    """Analyse up to N distinct fresh landing URLs from a competitor's scanned ads."""
    cap = int(db.setting('landing_max_per_competitor', '2'))
    ads = db.rows('SELECT id, landing_url FROM reference_ads WHERE competitor_id=? '
                  'AND landing_url IS NOT NULL ORDER BY id DESC LIMIT 20', (competitor_id,))
    seen, done = set(), 0
    for ad in ads:
        if done >= cap:
            break
        url = _clean_url(ad['landing_url'])
        if not url or url in seen:
            continue
        seen.add(url)
        already = db.row("SELECT 1 AS x FROM landing_pages WHERE url=? AND status='done' LIMIT 1", (url,))
        if already:
            continue
        try:
            analyze_url(url, client_id=client_id, competitor_id=competitor_id, reference_ad_id=ad['id'])
            done += 1
        except Exception:
            continue  # per-URL failures logged on the landing_pages row
    return done


def list_analyses(client_id: int = None, competitor_id: int = None) -> list[dict]:
    where, params = ['1=1'], []
    if competitor_id:
        where.append('l.competitor_id=?')
        params.append(competitor_id)
    elif client_id:
        where.append('(l.client_id=? OR c.client_id=?)')
        params += [client_id, client_id]
    rows = db.rows(f"SELECT l.*, c.name AS competitor_name FROM landing_pages l "
                   f"LEFT JOIN competitors c ON c.id = l.competitor_id "
                   f"WHERE {' AND '.join(where)} ORDER BY l.id DESC LIMIT 50", params)
    for r in rows:
        r['analysis'] = db.jloads(r.pop('analysis_json', None), {})
    return rows
