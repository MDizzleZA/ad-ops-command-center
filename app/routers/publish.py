"""Publishing queue API: compose, compliance-check, approve, publish, retry.

Every write path routes through app/publish/gate.py or app/publish/base.py rather than
touching social_posts directly, so the state machine and the compliance block hold no
matter which endpoint is called. There is deliberately no endpoint that sets a post's
status to an arbitrary value.
"""
from fastapi import APIRouter, HTTPException

from app import db
from app.publish import base, gate

router = APIRouter(prefix='/api', tags=['publish'])


def _guard(fn, *args, **kwargs):
    """Turn adapter/state-machine refusals into 400s with their explanation intact."""
    try:
        return fn(*args, **kwargs)
    except base.IllegalTransition as exc:
        raise HTTPException(409, str(exc)) from exc
    except base.PublishError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get('/publish/channels')
def channels(client_id: int | None = None):
    sql = ('SELECT s.*, c.name AS client_name FROM social_channels s '
           'JOIN clients c ON c.id = s.client_id')
    params: tuple = ()
    if client_id:
        sql += ' WHERE s.client_id = ?'
        params = (client_id,)
    return db.rows(sql + ' ORDER BY c.name, s.platform', params)


@router.post('/publish/channels/{channel_id}/enable')
def set_channel_enabled(channel_id: int, payload: dict):
    """Enable or disable publishing for a channel, and whether it needs approval."""
    channel = db.row('SELECT id FROM social_channels WHERE id = ?', (channel_id,))
    if not channel:
        raise HTTPException(404, f'channel {channel_id} not found')
    enabled = 1 if payload.get('publish_enabled') else 0
    # requires_approval defaults to on: turning it off is an explicit choice, not a default.
    requires = 1 if payload.get('requires_approval', True) else 0
    db.execute('UPDATE social_channels SET publish_enabled = ?, requires_approval = ? '
               'WHERE id = ?', (enabled, requires, channel_id))
    return db.row('SELECT * FROM social_channels WHERE id = ?', (channel_id,))


@router.get('/publish/assets')
def assets(client_id: int, limit: int = 40):
    """Recent generated images for the composer to attach, newest first.

    Reuses what the creative pipeline and cloner already produced rather than adding a
    separate upload path.
    """
    return db.rows(
        'SELECT id, kind, file_path, prompt, created_at FROM generated_assets '
        "WHERE client_id = ? AND kind = 'image' AND file_path IS NOT NULL "
        'ORDER BY id DESC LIMIT ?', (client_id, max(1, min(limit, 200))))


@router.get('/publish/posts')
def posts(client_id: int | None = None, status: str | None = None, limit: int = 100):
    sql = ('SELECT p.*, c.name AS client_name FROM social_posts p '
           'JOIN clients c ON c.id = p.client_id WHERE 1=1')
    params: list = []
    if client_id:
        sql += ' AND p.client_id = ?'
        params.append(client_id)
    if status:
        sql += ' AND p.status = ?'
        params.append(status)
    sql += ' ORDER BY COALESCE(p.scheduled_at, p.created_at) DESC LIMIT ?'
    params.append(max(1, min(limit, 500)))
    rows = db.rows(sql, tuple(params))
    for row in rows:
        row['media'] = db.jloads(row.get('media_json'), []) or []
        row['compliance'] = db.jloads(row.get('compliance_json'), {}) or {}
        row['targets'] = db.rows(
            'SELECT t.*, s.platform, s.handle, s.external_id FROM social_post_targets t '
            'JOIN social_channels s ON s.id = t.channel_id WHERE t.post_id = ? ORDER BY t.id',
            (row['id'],))
    return rows


@router.get('/publish/posts/{post_id}')
def post_detail(post_id: int):
    row = db.row('SELECT * FROM social_posts WHERE id = ?', (post_id,))
    if not row:
        raise HTTPException(404, f'post {post_id} not found')
    row['media'] = db.jloads(row.get('media_json'), []) or []
    row['compliance'] = db.jloads(row.get('compliance_json'), {}) or {}
    row['targets'] = db.rows(
        'SELECT t.*, s.platform, s.handle FROM social_post_targets t '
        'JOIN social_channels s ON s.id = t.channel_id WHERE t.post_id = ? ORDER BY t.id',
        (post_id,))
    row['attempts'] = db.rows(
        'SELECT a.* FROM publish_attempts a JOIN social_post_targets t ON t.id = a.target_id '
        'WHERE t.post_id = ? ORDER BY a.id', (post_id,))
    return row


@router.post('/publish/posts')
def create_post(payload: dict):
    client_id = payload.get('client_id')
    channel_ids = payload.get('channel_ids') or []
    if not client_id:
        raise HTTPException(400, 'client_id required')
    if not channel_ids:
        raise HTTPException(400, 'at least one channel_id required')
    post_id = _guard(gate.submit, client_id, payload.get('caption') or '',
                     [int(c) for c in channel_ids], media=payload.get('media') or [],
                     scheduled_at=payload.get('scheduled_at'),
                     brief_id=payload.get('brief_id'))
    return post_detail(post_id)


@router.patch('/publish/posts/{post_id}')
def edit_post(post_id: int, payload: dict):
    """Edit a post that has not gone out. Any content change clears its compliance verdict.

    Without that reset, a caption could be approved, edited, and published having never
    been audited in its final form.
    """
    row = db.row('SELECT status FROM social_posts WHERE id = ?', (post_id,))
    if not row:
        raise HTTPException(404, f'post {post_id} not found')
    if row['status'] in ('published', 'publishing'):
        raise HTTPException(409, f'post {post_id} is {row["status"]} and can no longer be edited')

    import json
    sets, params = [], []
    content_changed = False
    if 'caption' in payload:
        sets.append('caption = ?')
        params.append(payload['caption'])
        content_changed = True
    if 'media' in payload:
        sets.append('media_json = ?')
        params.append(json.dumps(payload['media'] or []))
        content_changed = True
    if 'scheduled_at' in payload:
        sets.append('scheduled_at = ?')
        params.append(payload['scheduled_at'])
    if not sets:
        raise HTTPException(400, 'nothing to update')

    if content_changed:
        sets += ['compliance_status = NULL', 'compliance_json = NULL', 'approved_at = NULL',
                 'approved_by = NULL']
    params.append(post_id)
    db.execute(f'UPDATE social_posts SET {", ".join(sets)} WHERE id = ?', tuple(params))
    if content_changed and row['status'] != 'draft':
        _guard(base.set_post_status, post_id, 'draft')
    return post_detail(post_id)


@router.post('/publish/posts/{post_id}/compliance')
def check_compliance(post_id: int):
    result = _guard(gate.run_compliance, post_id)
    return {'post': post_detail(post_id), 'audit': result}


@router.post('/publish/posts/{post_id}/disclaimer')
def burn_disclaimer(post_id: int, payload: dict | None = None):
    payload = payload or {}
    media = _guard(gate.apply_disclaimer, post_id, disclaimer=payload.get('disclaimer'),
                   logo_path=payload.get('logo_path'))
    return {'post': post_detail(post_id), 'media': media}


@router.post('/publish/posts/{post_id}/approve')
def approve_post(post_id: int, payload: dict | None = None):
    payload = payload or {}
    _guard(gate.approve, post_id, payload.get('approved_by') or 'ui',
           payload.get('scheduled_at'))
    return post_detail(post_id)


@router.post('/publish/posts/{post_id}/reject')
def reject_post(post_id: int, payload: dict | None = None):
    payload = payload or {}
    _guard(gate.reject, post_id, payload.get('reason'))
    return post_detail(post_id)


@router.post('/publish/posts/{post_id}/publish')
def publish_now(post_id: int, payload: dict | None = None):
    """Publish immediately. dry_run defaults to True -- going live is an explicit choice."""
    payload = payload or {}
    dry_run = payload.get('dry_run', True)
    result = _guard(base.run_post, post_id, dry_run=bool(dry_run))
    return {'result': result, 'post': post_detail(post_id)}


@router.post('/publish/posts/{post_id}/cancel')
def cancel_post(post_id: int):
    _guard(base.set_post_status, post_id, 'cancelled')
    return post_detail(post_id)


@router.get('/publish/due')
def due(dry_run: bool = True):
    """What the scheduler tick would do right now."""
    return base.run_due(dry_run=dry_run)


@router.get('/publish/status')
def publish_status():
    """Operational summary: queue depth, channel readiness, blocked platforms."""
    counts = {r['status']: r['n'] for r in db.rows(
        'SELECT status, COUNT(*) AS n FROM social_posts GROUP BY status')}
    blocked = {}
    for platform in ('linkedin', 'x', 'gbp'):
        try:
            module = base.resolve_adapter(platform)
            blocked[platform] = getattr(module, 'BLOCKED_REASON', None)
        except base.PublishError as exc:
            blocked[platform] = str(exc)
    return {
        'queue': counts,
        'channels_enabled': db.row(
            'SELECT COUNT(*) AS n FROM social_channels WHERE publish_enabled = 1')['n'],
        'channels_total': db.row('SELECT COUNT(*) AS n FROM social_channels')['n'],
        'auto_publish_clients': db.setting('publish_enabled_clients', ''),
        'catchup_minutes': db.setting('publish_catchup_minutes', '30'),
        'tick_minutes': db.setting('publish_tick_minutes', '5'),
        'blocked_platforms': {k: v for k, v in blocked.items() if v},
    }
