"""Data-driven buyer persona mining: Reddit threads + review pages -> Gemini NLP.

Reddit is scraped via Apify (actor in settings); review URLs (Hellopeter,
Google reviews export pages, Trustpilot, etc.) are fetched directly and
stripped to text. Gemini distils the corpus into structured personas saved to
the personas table, where the brief console and daily pipeline already read.
"""
import json
import re
from datetime import date

import requests

from app import db
from app.services import apify

MAX_CORPUS_CHARS = 60000

PERSONA_SCHEMA = {
    'type': 'object',
    'properties': {
        'personas': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'headline': {'type': 'string'},
                    'demographics': {'type': 'array', 'items': {'type': 'string'}},
                    'pain_points': {'type': 'array', 'items': {'type': 'string'}},
                    'triggers': {'type': 'array', 'items': {'type': 'string'}},
                    'objections': {'type': 'array', 'items': {'type': 'string'}},
                    'verbatim_quotes': {'type': 'array', 'items': {'type': 'string'}},
                },
                'required': ['name', 'headline', 'demographics', 'pain_points', 'triggers', 'objections'],
            },
        },
    },
    'required': ['personas'],
}

_mining: set[int] = set()


def is_mining(client_id: int) -> bool:
    return client_id in _mining


def _html_to_text(html: str) -> str:
    html = re.sub(r'(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>', ' ', html)
    html = re.sub(r'(?s)<[^>]+>', ' ', html)
    html = re.sub(r'&(nbsp|amp|quot|#39|lt|gt);', ' ', html)
    return re.sub(r'\s+', ' ', html).strip()


def _fetch_review_page(url: str) -> str:
    try:
        resp = requests.get(url, timeout=30, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        resp.raise_for_status()
    except requests.RequestException:
        return ''
    text = _html_to_text(resp.text)
    return text if len(text) > 400 else ''  # JS-rendered SPAs yield near-empty shells


def _scrape_reddit(keywords: list[str], max_items: int = 60) -> list[str]:
    actor = db.setting('apify_actor_reddit')
    if not actor:
        return []
    run_input = {
        'searches': keywords,
        'searchPosts': True,
        'searchComments': True,
        'searchCommunities': False,
        'searchUsers': False,
        'skipUserPosts': True,
        'sort': 'relevance',
        'time': 'year',
        'maxItems': max_items,
        'includeNSFW': False,
    }
    run_row_id = apify.start_run(actor, run_input, purpose='persona')
    run = apify.wait_for_run(run_row_id, timeout_s=480)
    if run['status'] != 'done':
        return []
    items = apify.dataset_items(run['dataset_id'])
    db.execute('UPDATE apify_runs SET items=? WHERE id=?', (len(items), run_row_id))
    texts = []
    for item in items:
        title = item.get('title') or ''
        body = item.get('body') or item.get('text') or item.get('selftext') or ''
        combined = f'{title}\n{body}'.strip()
        if len(combined) > 40:
            texts.append(combined[:1500])
    return texts


def mine(client_id: int, keywords: list[str] = None, review_urls: list[str] = None,
         persona_count: int = 3) -> dict:
    """Blocking mine (run from a background thread). Returns summary dict."""
    if client_id in _mining:
        raise RuntimeError('a persona mining run is already in progress for this client')
    _mining.add(client_id)
    try:
        return _mine_inner(client_id, keywords, review_urls, persona_count)
    finally:
        _mining.discard(client_id)


def _mine_inner(client_id: int, keywords: list[str], review_urls: list[str], persona_count: int) -> dict:
    from app.services import gemini
    client = db.row('SELECT * FROM clients WHERE id=?', (client_id,))
    if not client:
        raise ValueError('client not found')
    keywords = [k for k in (keywords or []) if k.strip()] or [client['name'], client['industry'] or '']
    keywords = [k for k in keywords if k][:4]

    sources = []
    reddit_texts = _scrape_reddit(keywords)
    if reddit_texts:
        sources.append(('REDDIT DISCUSSIONS', reddit_texts))
    for url in (review_urls or [])[:5]:
        text = _fetch_review_page(url)
        if text:
            sources.append((f'REVIEW PAGE {url}', [text[:12000]]))
    if not sources:
        raise RuntimeError('no source material found - check the Reddit keywords / review URLs')

    corpus_parts, used = [], 0
    for label, texts in sources:
        corpus_parts.append(f'===== {label} =====')
        for t in texts:
            if used + len(t) > MAX_CORPUS_CHARS:
                break
            corpus_parts.append(t)
            corpus_parts.append('---')
            used += len(t)
    corpus = '\n'.join(corpus_parts)

    prompt = f"""You are a senior consumer-insights analyst building buyer personas for {client['name']}
({client['industry']}, South Africa). Below is raw consumer voice data (forum discussions and reviews).

Distil exactly {persona_count} distinct, data-grounded buyer personas. Each needs:
- name: a memorable alliterative label (e.g. "Pre-retirement Piet")
- headline: one sentence capturing who they are and what they want
- demographics: age band, life stage, income signals, location hints - only what the data supports
- pain_points: specific frustrations in the data (not generic marketing guesses)
- triggers: events/emotions that push them to act
- objections: stated reasons for hesitation or distrust
- verbatim_quotes: 2-3 short real quotes from the data that typify this persona

Ground everything in the source material. If the data only supports fewer personas, return fewer.

SOURCE DATA:
{corpus}"""
    result = gemini.gen_text(prompt, schema=PERSONA_SCHEMA)
    personas = result.get('personas', [])
    source_tag = f"miner:{date.today().isoformat()}"
    ids = []
    for p in personas:
        pid = db.execute(
            'INSERT INTO personas (client_id, name, headline, demographics_json, pain_points_json, '
            'triggers_json, objections_json, source_path) VALUES (?,?,?,?,?,?,?,?)',
            (client_id, p.get('name') or 'Unnamed persona', p.get('headline'),
             json.dumps(p.get('demographics') or []), json.dumps(p.get('pain_points') or []),
             json.dumps(p.get('triggers') or []), json.dumps(p.get('objections') or []),
             source_tag))
        ids.append(pid)
    return {'personas_created': len(ids), 'ids': ids,
            'reddit_items': len(reddit_texts), 'sources': [s[0] for s in sources]}
