"""Algorithmic ad grading + pause/scale recommendation queue.

Grades every ad (falling back to campaign level where ad-level metrics are
absent) A-F from the last N days of synced metrics against the client's CPL
targets (clients.kpi_json) or ROAS where revenue exists. Recommendations are
queued in grader_actions for human review ("recommend + confirm"); Apply
executes against the Meta API - never automatically.
"""
import json
from datetime import date, timedelta

from app import db
from app.services import fatigue

GRADE_ORDER = ['A', 'B', 'C', 'D', 'F']


def _client_targets(client_id: int) -> dict:
    kpi = db.jloads((db.row('SELECT kpi_json FROM clients WHERE id=?', (client_id,)) or {}).get('kpi_json'), {})
    return kpi.get('cpl_target') or {}


def _entity_metrics(account_ids: list[int], level: str, date_from: str, date_to: str) -> list[dict]:
    if not account_ids:
        return []
    marks = ','.join('?' * len(account_ids))
    return db.rows(
        f"SELECT account_id, entity_external_id, SUM(spend) AS spend, SUM(impressions) AS impressions, "
        f"SUM(clicks) AS clicks, SUM(leads) AS leads, SUM(revenue) AS revenue, AVG(frequency) AS frequency "
        f"FROM metrics_daily WHERE level=? AND account_id IN ({marks}) AND date BETWEEN ? AND ? "
        f"GROUP BY account_id, entity_external_id HAVING SUM(spend) > 0",
        [level] + account_ids + [date_from, date_to])


def _grade_row(m: dict, cpl_target: list | None, min_spend: float) -> tuple[str, str]:
    """Returns (grade, reason). Grades on ROAS when revenue exists, else CPL vs target."""
    spend = m['spend'] or 0
    ctr = (m['clicks'] / m['impressions'] * 100) if m['impressions'] else None
    if m['revenue']:
        roas = m['revenue'] / spend if spend else 0
        if roas >= 4:
            return 'A', f'ROAS {roas:.1f}x'
        if roas >= 2.5:
            return 'B', f'ROAS {roas:.1f}x'
        if roas >= 1.5:
            return 'C', f'ROAS {roas:.1f}x'
        if roas >= 1:
            return 'D', f'ROAS {roas:.1f}x - barely breaking even'
        return 'F', f'ROAS {roas:.1f}x - losing money'
    if m['leads']:
        cpl = spend / m['leads']
        if cpl_target:
            lo, hi = cpl_target[0], cpl_target[1]
            if cpl <= lo:
                return 'A', f'CPL R{cpl:.0f} beats the R{lo:.0f} floor of target'
            if cpl <= hi:
                return 'B', f'CPL R{cpl:.0f} inside target band (R{lo:.0f}-R{hi:.0f})'
            if cpl <= hi * 1.5:
                return 'C', f'CPL R{cpl:.0f} up to 50% over target (R{hi:.0f})'
            if cpl <= hi * 2:
                return 'D', f'CPL R{cpl:.0f} up to 2x target (R{hi:.0f})'
            return 'F', f'CPL R{cpl:.0f} more than 2x target (R{hi:.0f})'
        # No target: grade on CTR as a weak proxy
        if ctr is not None and ctr >= 1.5:
            return 'B', f'Generating leads, CTR {ctr:.2f}% (no CPL target set)'
        return 'C', f'Generating leads at CPL R{spend / m["leads"]:.0f} (no CPL target set)'
    # Spending with zero leads
    threshold = (cpl_target[1] if cpl_target else min_spend * 3)
    if spend >= threshold * 1.5:
        return 'F', f'R{spend:.0f} spent with no leads (>{threshold * 1.5:.0f})'
    if spend >= threshold:
        return 'D', f'R{spend:.0f} spent with no leads yet'
    if ctr is not None and ctr >= 1.0:
        return 'C', f'Low spend so far, CTR {ctr:.2f}% healthy'
    return 'C', 'Low spend - too early to judge'


def grade_client(client_id: int) -> list[dict]:
    """Grade all ads (and campaigns without ad-level data) for a client's paid accounts."""
    min_spend = float(db.setting('grader_min_spend', '300'))
    lookback = int(db.setting('grader_lookback_days', '14'))
    today = date.today()
    date_from = (today - timedelta(days=lookback - 1)).isoformat()
    date_to = today.isoformat()

    accounts = db.rows("SELECT * FROM ad_accounts WHERE client_id=? AND platform IN "
                       "('meta','google','bing','linkedin')", (client_id,))
    targets = _client_targets(client_id)
    results = []
    fatigue_scores = {}
    try:
        fatigue_scores = fatigue.score_creatives([a['id'] for a in accounts],
                                                 cpl_target=targets.get('blended'))
    except Exception:
        pass

    for account in accounts:
        cpl_target = targets.get(account['platform']) or targets.get('blended')
        rows = _entity_metrics([account['id']], 'ad', date_from, date_to)
        level = 'ad'
        if not rows:  # some platforms only sync campaign level
            rows = _entity_metrics([account['id']], 'campaign', date_from, date_to)
            level = 'campaign'
        for m in rows:
            grade, reason = _grade_row(m, cpl_target, min_spend)
            fat = fatigue_scores.get((account['id'], m['entity_external_id']))
            if fat and fat['badge'] == 'fatiguing' and grade in ('A', 'B'):
                grade, reason = 'C', f"{reason}; but {fat['reason']}"
            name_row = None
            if level == 'ad':
                name_row = db.row('SELECT name, status FROM creatives WHERE account_id=? AND ad_external_id=?',
                                  (account['id'], m['entity_external_id']))
            else:
                name_row = db.row('SELECT name, status FROM campaigns WHERE account_id=? AND external_id=?',
                                  (account['id'], m['entity_external_id']))
            ctr = (m['clicks'] / m['impressions'] * 100) if m['impressions'] else None
            cpl = (m['spend'] / m['leads']) if m['leads'] else None
            results.append({
                'account_id': account['id'], 'platform': account['platform'],
                'account_alias': account['alias'], 'level': level,
                'entity_external_id': m['entity_external_id'],
                'entity_name': (name_row or {}).get('name') or m['entity_external_id'],
                'entity_status': (name_row or {}).get('status'),
                'grade': grade, 'reason': reason,
                'spend': round(m['spend'] or 0, 2), 'leads': m['leads'] or 0,
                'revenue': round(m['revenue'] or 0, 2),
                'ctr': round(ctr, 2) if ctr is not None else None,
                'cpl': round(cpl, 2) if cpl is not None else None,
                'fatigue': (fat or {}).get('badge'),
                'recommendation': _recommend(grade, m, min_spend, (name_row or {}).get('status')),
            })
    results.sort(key=lambda r: (GRADE_ORDER.index(r['grade']), -r['spend']))
    return results


def _recommend(grade: str, m: dict, min_spend: float, entity_status: str | None) -> str | None:
    status = (entity_status or '').upper()
    if status in ('PAUSED', 'ARCHIVED', 'DELETED', 'REMOVED'):
        return None
    if grade == 'F' and (m['spend'] or 0) >= min_spend:
        return 'pause'
    if grade == 'A' and (m['spend'] or 0) >= min_spend:
        return 'scale'
    return None


def queue_recommendations(client_id: int) -> list[dict]:
    """Grade + insert pending grader_actions (skipping duplicates). Returns pending actions."""
    graded = grade_client(client_id)
    for row in graded:
        if not row['recommendation']:
            continue
        dup = db.row("SELECT id FROM grader_actions WHERE account_id=? AND entity_external_id=? "
                     "AND action=? AND status='pending'",
                     (row['account_id'], row['entity_external_id'], row['recommendation']))
        if dup:
            continue
        db.execute(
            'INSERT INTO grader_actions (client_id, account_id, level, entity_external_id, entity_name, '
            'action, grade, reason, metrics_json) VALUES (?,?,?,?,?,?,?,?,?)',
            (client_id, row['account_id'], row['level'], row['entity_external_id'], row['entity_name'],
             row['recommendation'], row['grade'], row['reason'],
             json.dumps({k: row[k] for k in ('spend', 'leads', 'revenue', 'ctr', 'cpl', 'platform')})))
    return pending_actions(client_id)


def pending_actions(client_id: int | None = None) -> list[dict]:
    where, params = ('WHERE g.client_id=?', [client_id]) if client_id else ('', [])
    rows = db.rows(f'SELECT g.*, a.platform, a.alias AS account_alias FROM grader_actions g '
                   f'JOIN ad_accounts a ON a.id = g.account_id {where} '
                   f'ORDER BY CASE g.status WHEN \'pending\' THEN 0 ELSE 1 END, g.id DESC LIMIT 100', params)
    for r in rows:
        r['metrics'] = db.jloads(r.pop('metrics_json', None), {})
    return rows


def _resolve(action_id: int, status: str, error: str = None):
    db.execute("UPDATE grader_actions SET status=?, error=?, resolved_at=datetime('now') WHERE id=?",
               (status, error, action_id))


def dismiss_action(action_id: int):
    _resolve(action_id, 'dismissed')


def apply_action(action_id: int) -> dict:
    """Execute a queued pause/scale against the live platform (Meta only for now)."""
    action = db.row('SELECT g.*, a.platform FROM grader_actions g '
                    'JOIN ad_accounts a ON a.id = g.account_id WHERE g.id=?', (action_id,))
    if not action:
        raise ValueError('action not found')
    if action['status'] != 'pending':
        raise ValueError(f"action is already {action['status']}")
    if action['platform'] != 'meta':
        _resolve(action_id, 'failed', f"{action['platform']} execution not supported - apply manually")
        return {'status': 'failed', 'error': f"{action['platform']} execution not supported yet - "
                                             'apply this one manually in the platform UI'}
    try:
        detail = _apply_meta(action)
        _resolve(action_id, 'applied')
        return {'status': 'applied', 'detail': detail}
    except Exception as exc:
        msg = f'{exc.__class__.__name__}: {exc}'
        _resolve(action_id, 'failed', msg[:500])
        return {'status': 'failed', 'error': msg}


def _apply_meta(action: dict) -> str:
    from app.sync.meta_sync import _init_api
    _init_api()
    scale_pct = float(db.setting('grader_scale_budget_pct', '20'))

    if action['action'] == 'pause':
        if action['level'] == 'ad':
            from facebook_business.adobjects.ad import Ad
            Ad(action['entity_external_id']).api_update(params={'status': 'PAUSED'})
        else:
            from facebook_business.adobjects.campaign import Campaign
            Campaign(action['entity_external_id']).api_update(params={'status': 'PAUSED'})
        return f"{action['level']} {action['entity_external_id']} paused"

    # scale: raise the parent ad set's daily budget (campaign-level: the campaign budget)
    from facebook_business.adobjects.adset import AdSet
    from facebook_business.adobjects.campaign import Campaign
    if action['level'] == 'ad':
        creative = db.row('SELECT adset_external_id FROM creatives WHERE account_id=? AND ad_external_id=?',
                          (action['account_id'], action['entity_external_id']))
        adset_id = (creative or {}).get('adset_external_id')
        if not adset_id:
            raise RuntimeError('ad set id unknown for this ad - re-sync Meta first')
        adset = AdSet(adset_id).api_get(fields=['daily_budget', 'lifetime_budget', 'name'])
        current = int(adset.get('daily_budget') or 0)
        if not current:
            raise RuntimeError('ad set has no daily budget (lifetime/CBO) - scale manually')
        new_budget = int(current * (1 + scale_pct / 100))
        AdSet(adset_id).api_update(params={'daily_budget': new_budget})
        return (f"ad set '{adset.get('name')}' daily budget {current / 100:.0f} -> "
                f'{new_budget / 100:.0f} (+{scale_pct:.0f}%)')
    campaign = Campaign(action['entity_external_id']).api_get(fields=['daily_budget', 'name'])
    current = int(campaign.get('daily_budget') or 0)
    if not current:
        raise RuntimeError('campaign has no daily budget - scale the ad sets manually')
    new_budget = int(current * (1 + scale_pct / 100))
    Campaign(action['entity_external_id']).api_update(params={'daily_budget': new_budget})
    return (f"campaign '{campaign.get('name')}' daily budget {current / 100:.0f} -> "
            f'{new_budget / 100:.0f} (+{scale_pct:.0f}%)')
