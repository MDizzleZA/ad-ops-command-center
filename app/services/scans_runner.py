"""Competitor scan orchestration: Apify runs per competitor, diffing, summary + AI insights.

The insights summary follows the competitive-ads-extractor analysis framework:
themes, pain points highlighted, creative patterns, copy formulas, recommendations.
"""
import json
from collections import Counter

from app import db
from app.services import apify, gemini

INSIGHTS_MAX_ADS = 40


def _is_new(competitor_id: int, reference_ad_id: int, scan_id: int) -> bool:
    prior = db.row(
        'SELECT 1 AS x FROM scan_ads WHERE competitor_id=? AND reference_ad_id=? AND scan_id != ? LIMIT 1',
        (competitor_id, reference_ad_id, scan_id))
    return prior is None


def run_scan(client_id: int, kind: str = 'ads') -> int:
    """Blocking scan across the client's active competitors. Returns scan id."""
    scan_id = db.execute('INSERT INTO scans (client_id, kind) VALUES (?,?)', (client_id, kind))
    try:
        _run_scan_inner(scan_id, client_id, kind)
        db.execute("UPDATE scans SET status='done', finished_at=datetime('now') WHERE id=?", (scan_id,))
    except Exception as exc:
        db.execute("UPDATE scans SET status='error', error=?, finished_at=datetime('now') WHERE id=?",
                   (f'{exc.__class__.__name__}: {exc}', scan_id))
    return scan_id


def _run_scan_inner(scan_id: int, client_id: int, kind: str):
    competitors = db.rows('SELECT * FROM competitors WHERE client_id=? AND active=1', (client_id,))
    if not competitors:
        raise RuntimeError('No active competitors configured for this client')
    country = db.setting('default_country', 'ZA')
    total = new = 0
    per_competitor = []

    for comp in competitors:
        if kind == 'organic':
            new_posts = _scan_organic(scan_id, comp)
            per_competitor.append({'competitor_id': comp['id'], 'name': comp['name'],
                                   'total': new_posts, 'new': new_posts, 'formats': {}, 'timeline': []})
            total += new_posts
            continue

        actor = db.setting('apify_actor_ad_library')
        run_input = apify.ad_library_search_input(
            query=comp['name'], page_url=comp.get('fb_page_url'), country=country, active_only=True)
        run_row_id = apify.start_run(actor, run_input, purpose='scan_ads')
        db.execute('UPDATE scans SET apify_run_id=? WHERE id=?', (str(run_row_id), scan_id))
        run = apify.wait_for_run(run_row_id)
        if run['status'] != 'done':
            per_competitor.append({'competitor_id': comp['id'], 'name': comp['name'],
                                   'total': 0, 'new': 0, 'formats': {}, 'timeline': [],
                                   'error': run.get('error') or 'actor run failed'})
            continue
        items = apify.dataset_items(run['dataset_id'])
        db.execute('UPDATE apify_runs SET items=? WHERE id=?', (len(items), run_row_id))
        comp_total = comp_new = 0
        formats = Counter()
        months = Counter()
        for item in items:
            norm = apify.normalize_ad_item(item)
            # Keyword search can return other pages: keep only ads from this competitor's page
            page = (norm.get('page_name') or '').lower()
            if page and comp['name'].split()[0].lower() not in page:
                continue
            ref_id, _created = apify.upsert_reference_ad(norm, source='scan', client_id=client_id,
                                                         competitor_id=comp['id'])
            is_new = _is_new(comp['id'], ref_id, scan_id)
            db.execute('INSERT OR IGNORE INTO scan_ads (scan_id, reference_ad_id, competitor_id, is_new) '
                       'VALUES (?,?,?,?)', (scan_id, ref_id, comp['id'], 1 if is_new else 0))
            comp_total += 1
            comp_new += 1 if is_new else 0
            formats[norm.get('format') or 'unknown'] += 1
            if norm.get('started_running'):
                months[norm['started_running'][:7]] += 1
        total += comp_total
        new += comp_new
        landing_done = 0
        if comp_new:
            try:
                from app.services import landing
                landing_done = landing.extract_for_competitor(client_id, comp['id'])
            except Exception:
                pass  # landing analysis is best-effort; failures live on landing_pages rows
        per_competitor.append({
            'competitor_id': comp['id'], 'name': comp['name'], 'total': comp_total, 'new': comp_new,
            'landing_pages_analyzed': landing_done,
            'formats': dict(formats),
            'timeline': [{'month': m, 'count': c} for m, c in sorted(months.items())],
        })

    insights = None
    if kind == 'ads' and total:
        try:
            insights = _generate_insights(scan_id, client_id)
        except Exception as exc:
            insights = f'(insights generation failed: {exc})'
    db.execute('UPDATE scans SET total_ads=?, new_ads=?, summary_json=? WHERE id=?',
               (total, new, json.dumps({'per_competitor': per_competitor, 'insights': insights}), scan_id))


def _scan_organic(scan_id: int, comp: dict) -> int:
    if not comp.get('fb_page_url'):
        return 0
    actor = db.setting('apify_actor_posts')
    run_input = {'startUrls': [{'url': comp['fb_page_url']}], 'resultsLimit': 20,
                 'onlyPostsNewerThan': '7 days'}
    run_row_id = apify.start_run(actor, run_input, purpose='scan_organic')
    run = apify.wait_for_run(run_row_id)
    if run['status'] != 'done':
        return 0
    items = apify.dataset_items(run['dataset_id'])
    db.execute('UPDATE apify_runs SET items=? WHERE id=?', (len(items), run_row_id))
    count = 0
    for item in items:
        url = item.get('url') or item.get('postUrl') or item.get('topLevelUrl')
        if not url:
            continue
        db.execute(
            'INSERT INTO organic_posts (competitor_id, scan_id, post_url, posted_at, text, likes, '
            'comments, shares, media_url, raw_json) VALUES (?,?,?,?,?,?,?,?,?,?) '
            'ON CONFLICT(post_url) DO UPDATE SET likes=excluded.likes, comments=excluded.comments, '
            'shares=excluded.shares, scan_id=excluded.scan_id',
            (comp['id'], scan_id, url, str(item.get('time') or item.get('date') or '')[:19] or None,
             (item.get('text') or '')[:2000], item.get('likes') or 0,
             item.get('comments') or 0, item.get('shares') or 0,
             item.get('media', [{}])[0].get('thumbnail') if isinstance(item.get('media'), list) else None,
             json.dumps(item)[:8000]))
        count += 1
    return count


def _generate_insights(scan_id: int, client_id: int) -> str:
    ads = db.rows(
        'SELECT r.page_name, r.format, r.headline, r.body, r.cta, r.started_running, s.is_new '
        'FROM scan_ads s JOIN reference_ads r ON r.id = s.reference_ad_id WHERE s.scan_id=? LIMIT ?',
        (scan_id, INSIGHTS_MAX_ADS))
    client = db.row('SELECT name, industry FROM clients WHERE id=?', (client_id,))
    corpus = '\n---\n'.join(
        f"[{a['page_name']} | {a['format']} | started {a['started_running'] or '?'}"
        f"{' | NEW' if a['is_new'] else ''}]\nHeadline: {a['headline'] or '-'}\n"
        f"Body: {(a['body'] or '-')[:500]}\nCTA: {a['cta'] or '-'}" for a in ads)
    prompt = f"""You are a senior paid-social strategist analysing competitor ads for {client['name']}
({client['industry']}). Below are {len(ads)} active competitor ads from the Meta Ad Library.

Produce a concise competitive analysis in this exact structure (plain text, short sections):
1. OVERVIEW - total ads per competitor, dominant formats, newly launched ads worth noting
2. PROBLEMS THEY'RE HIGHLIGHTING - the pain points/angles competitors lead with, with example copy
3. CREATIVE PATTERNS - recurring visual/format approaches and why they likely work
4. COPY FORMULAS - headline structures and CTA patterns that repeat
5. GAPS & RECOMMENDATIONS - underserved angles {client['name']} could own, and 3 concrete test ideas
   (remember: South African financial-services FAIS rules apply - no guaranteed-return angles)

ADS:
{corpus}"""
    return gemini.gen_text(prompt)
