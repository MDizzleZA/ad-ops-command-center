"""Creative winning/fatiguing scoring.

Compares the last 7 days vs the prior 7 days per ad (level='ad' metrics):
- Winning: meaningful spend, CTR stable or improving, CPL inside/below the
  client target band (when defined)
- Fatiguing: CTR down more than the configured threshold, or frequency rising
  while CPL worsens
- Watch: everything else with enough spend to judge
"""
from datetime import date, timedelta

from app import db


def _window_metrics(account_ids: list[int], date_from: str, date_to: str) -> dict:
    if not account_ids:
        return {}
    marks = ','.join('?' * len(account_ids))
    rows = db.rows(
        f"SELECT account_id, entity_external_id AS ad_id, SUM(spend) AS spend, "
        f"SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(leads) AS leads, "
        f"AVG(frequency) AS frequency FROM metrics_daily "
        f"WHERE level='ad' AND account_id IN ({marks}) AND date BETWEEN ? AND ? "
        f"GROUP BY account_id, entity_external_id",
        account_ids + [date_from, date_to])
    return {(r['account_id'], r['ad_id']): r for r in rows}


def _ctr(m: dict) -> float | None:
    if not m or not m.get('impressions'):
        return None
    return m['clicks'] / m['impressions'] * 100


def _cpl(m: dict) -> float | None:
    if not m or not m.get('leads'):
        return None
    return m['spend'] / m['leads']


def score_creatives(account_ids: list[int], cpl_target: list | None = None) -> dict:
    """Returns {(account_id, ad_external_id): {'badge': ..., 'reason': ...}}."""
    min_spend = float(db.setting('fatigue_min_spend', '500'))
    ctr_drop_threshold = float(db.setting('fatigue_ctr_drop_pct', '25'))
    today = date.today()
    recent_from = (today - timedelta(days=6)).isoformat()
    prior_from = (today - timedelta(days=13)).isoformat()
    prior_to = (today - timedelta(days=7)).isoformat()

    recent = _window_metrics(account_ids, recent_from, today.isoformat())
    prior = _window_metrics(account_ids, prior_from, prior_to)

    scores = {}
    for key, cur in recent.items():
        if (cur.get('spend') or 0) < min_spend:
            continue
        prev = prior.get(key)
        ctr_now, ctr_before = _ctr(cur), _ctr(prev)
        cpl_now, cpl_before = _cpl(cur), _cpl(prev)
        freq_now = cur.get('frequency') or 0
        freq_before = (prev or {}).get('frequency') or 0

        ctr_drop = None
        if ctr_now is not None and ctr_before:
            ctr_drop = (ctr_before - ctr_now) / ctr_before * 100

        badge, reason = 'watch', 'Spending but no clear trend yet'
        if ctr_drop is not None and ctr_drop > ctr_drop_threshold:
            badge = 'fatiguing'
            reason = f'CTR down {ctr_drop:.0f}% vs prior 7 days'
        elif freq_before and freq_now > freq_before * 1.15 and cpl_before and cpl_now and cpl_now > cpl_before:
            badge = 'fatiguing'
            reason = (f'Frequency up ({freq_before:.1f} -> {freq_now:.1f}) while CPL worsened '
                      f'(R{cpl_before:.0f} -> R{cpl_now:.0f})')
        else:
            in_band = cpl_target and cpl_now is not None and cpl_now <= cpl_target[1]
            ctr_ok = ctr_drop is None or ctr_drop < 10
            if in_band and ctr_ok:
                badge = 'winning'
                reason = f'CPL R{cpl_now:.0f} within target (<= R{cpl_target[1]}), CTR stable'
            elif cpl_now is None and ctr_ok and ctr_now and ctr_before and ctr_now >= ctr_before:
                badge = 'winning'
                reason = 'CTR improving vs prior 7 days'
        scores[key] = {'badge': badge, 'reason': reason}
    return scores
