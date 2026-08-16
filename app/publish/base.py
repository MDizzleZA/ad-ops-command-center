"""Publish contract, queue state machine and attempt logging for all app/publish/ modules.

Mirrors the app/sync/base.py arrangement -- one uniform adapter signature plus shared
helpers -- but for outbound content instead of inbound metrics. Every platform module
in app/publish/ exposes:

    publish(channel: dict, post: dict, media: list[dict], dry_run: bool = True) -> dict

where `channel` is a social_channels row, `post` a social_posts row, and `media` the
decoded post['media_json'] list. The return value is a PublishOutcome as a dict.

Two design choices carry most of the safety here:

1. `dry_run` defaults to True in every adapter, matching the --push convention in
   ~/.adops/connectors/sync_products.py. Publishing for real is opt-in.
2. Status changes go through set_post_status(), which rejects any transition not in
   POST_TRANSITIONS. There is no path from 'draft' to 'published', and assert_publishable()
   refuses a post whose compliance verdict is 'block'. A caption that fails FAIS review
   cannot be published by any code path, including a retry or a manual publish-now.
"""
import json
import logging
import re
from datetime import datetime, timedelta

from app import db

log = logging.getLogger('adops.publish')

DATE_FMT = '%Y-%m-%d %H:%M'
DEFAULT_CATCHUP_MINUTES = 30

# Statuses from which a post may legitimately be handed to an adapter.
PUBLISHABLE_STATUSES = ('scheduled', 'partial', 'failed')

# Legal social_posts.status transitions. Absent pairs are rejected outright.
# Note there is deliberately no route into 'publishing' that skips 'scheduled',
# and 'published' is terminal.
POST_TRANSITIONS: dict[str, set[str]] = {
    'draft': {'pending_compliance', 'cancelled'},
    'pending_compliance': {'pending_approval', 'draft', 'cancelled'},
    'pending_approval': {'scheduled', 'draft', 'cancelled'},
    # 'scheduled' -> 'failed' is legal so a post that missed its catch-up window can be
    # quarantined in one hop, rather than being faked through 'publishing' to get there.
    'scheduled': {'publishing', 'failed', 'draft', 'cancelled'},
    'publishing': {'published', 'partial', 'failed'},
    'partial': {'publishing', 'failed', 'cancelled'},
    'failed': {'publishing', 'draft', 'cancelled'},
    'published': set(),
    'cancelled': {'draft'},
}

TARGET_TRANSITIONS: dict[str, set[str]] = {
    'pending': {'publishing', 'skipped'},
    'publishing': {'published', 'failed'},
    'failed': {'publishing', 'skipped'},
    'published': set(),
    'skipped': {'pending'},
}

# platform -> module name in this package. Imported lazily so a missing platform SDK
# or an unconfigured channel cannot break app startup, same as app/sync/.
ADAPTERS = {
    'facebook': 'meta_publish',
    'instagram': 'meta_publish',
    'linkedin': 'linkedin_publish',
    'x': 'x_publish',
    'gbp': 'gbp_publish',
}

_TOKEN_PATTERN = re.compile(
    r'((?:access_token|client_secret|password|bearer|authorization)["\':=\s]+)([^\s&"\',]+)',
    re.IGNORECASE)


class PublishError(Exception):
    """An adapter failure, tagged with whether retrying could plausibly help.

    retryable=True  -- rate limit, timeout, 5xx, media still processing.
    retryable=False -- rejected caption, missing scope, revoked token, bad media spec.
                       Retrying just burns quota and re-fails.
    """

    def __init__(self, message: str, retryable: bool = False, phase: str = None,
                 response_code: int = None):
        super().__init__(message)
        self.retryable = retryable
        self.phase = phase
        self.response_code = response_code


class IllegalTransition(Exception):
    """Raised when a status change is not permitted by the state machine."""


def redact(text: str) -> str:
    """Strip anything token-shaped before it reaches the database or the log.

    publish_attempts is an audit trail that gets read by humans and shipped into
    reports, so it must never carry a credential.
    """
    if not text:
        return text
    return _TOKEN_PATTERN.sub(r'\1<redacted>', str(text))


def outcome(ok: bool, dry_run: bool, external_post_id: str = None, permalink: str = None,
            error: str = None, retryable: bool = False, detail: dict = None) -> dict:
    """The dict shape every adapter returns."""
    return {
        'ok': ok,
        'dry_run': dry_run,
        'external_post_id': external_post_id,
        'permalink': permalink,
        'error': redact(error) if error else None,
        'retryable': retryable,
        'detail': detail or {},
    }


# ------------------------------------------------------------------ state machine

def set_post_status(post_id: int, new_status: str, **fields) -> None:
    """Move a post to new_status, rejecting transitions the state machine forbids."""
    current = db.row('SELECT status FROM social_posts WHERE id = ?', (post_id,))
    if not current:
        raise IllegalTransition(f'post {post_id} does not exist')
    old = current['status']
    if new_status == old:
        allowed = True
    else:
        allowed = new_status in POST_TRANSITIONS.get(old, set())
    if not allowed:
        raise IllegalTransition(
            f'post {post_id}: {old} -> {new_status} is not a legal transition '
            f'(legal: {sorted(POST_TRANSITIONS.get(old, set())) or "none, terminal"})')
    assignments = ['status = ?']
    params: list = [new_status]
    for key, value in fields.items():
        assignments.append(f'{key} = ?')
        params.append(value)
    params.append(post_id)
    db.execute(f'UPDATE social_posts SET {", ".join(assignments)} WHERE id = ?', tuple(params))


def set_target_status(target_id: int, new_status: str, **fields) -> None:
    """Move a target to new_status, rejecting forbidden transitions."""
    current = db.row('SELECT status FROM social_post_targets WHERE id = ?', (target_id,))
    if not current:
        raise IllegalTransition(f'target {target_id} does not exist')
    old = current['status']
    if new_status != old and new_status not in TARGET_TRANSITIONS.get(old, set()):
        raise IllegalTransition(
            f'target {target_id}: {old} -> {new_status} is not a legal transition '
            f'(legal: {sorted(TARGET_TRANSITIONS.get(old, set())) or "none, terminal"})')
    assignments = ['status = ?']
    params: list = [new_status]
    for key, value in fields.items():
        assignments.append(f'{key} = ?')
        params.append(value)
    params.append(target_id)
    db.execute(f'UPDATE social_post_targets SET {", ".join(assignments)} WHERE id = ?',
               tuple(params))


def assert_publishable(post: dict) -> None:
    """The hard gate. Raises PublishError unless this post may legitimately go out.

    A 'block' compliance verdict is refused here rather than in the UI, so no route --
    scheduler tick, manual publish-now, or retry -- can bypass it.
    """
    if post['status'] not in PUBLISHABLE_STATUSES:
        raise PublishError(
            f'post {post["id"]} has status {post["status"]!r}; publishing requires one of '
            f'{PUBLISHABLE_STATUSES}', retryable=False, phase='validate')
    if post.get('compliance_status') == 'block':
        raise PublishError(
            f'post {post["id"]} was blocked by compliance review and cannot be published. '
            'Edit the caption and re-run the compliance check.',
            retryable=False, phase='validate')
    if not (post.get('caption') or '').strip() and not db.jloads(post.get('media_json'), []):
        raise PublishError(f'post {post["id"]} has neither caption nor media',
                           retryable=False, phase='validate')


# --------------------------------------------------------------------- scheduling

def catchup_verdict(scheduled_at: str | None, now: datetime = None,
                    window_minutes: int = None) -> str:
    """Classify a scheduled time as 'due', 'early' or 'late'.

    'late' exists so a laptop that was asleep at 09:00 does not wake at 14:00 and fire a
    morning post into the afternoon. Late posts are surfaced for a human decision instead.
    """
    if not scheduled_at:
        return 'due'
    now = now or datetime.now()
    if window_minutes is None:
        window_minutes = int(db.setting('publish_catchup_minutes', str(DEFAULT_CATCHUP_MINUTES)))
    try:
        due_at = datetime.strptime(scheduled_at.strip()[:16], DATE_FMT)
    except (ValueError, AttributeError):
        return 'late'
    if now < due_at:
        return 'early'
    return 'due' if now - due_at <= timedelta(minutes=window_minutes) else 'late'


def due_posts(now: datetime = None) -> list[dict]:
    """Scheduled posts whose time has arrived, oldest first."""
    now = now or datetime.now()
    return db.rows(
        "SELECT * FROM social_posts WHERE status = 'scheduled' AND scheduled_at IS NOT NULL "
        'AND scheduled_at <= ? ORDER BY scheduled_at',
        (now.strftime(DATE_FMT),))


def client_auto_publish_enabled(client_id: int) -> bool:
    """True when this client's slug appears in the publish_enabled_clients allow-list."""
    allowed = {s.strip() for s in db.setting('publish_enabled_clients', '').split(',') if s.strip()}
    if not allowed:
        return False
    row = db.row('SELECT slug FROM clients WHERE id = ?', (client_id,))
    return bool(row and row['slug'] in allowed)


# ----------------------------------------------------------------- attempt logging

def log_attempt(target_id: int, phase: str, dry_run: bool, request_summary: str = None,
                response_code: int = None, response_summary: str = None,
                error: str = None) -> int:
    """Append to the immutable audit trail. Returns the attempt number used."""
    prev = db.row('SELECT COALESCE(MAX(attempt_no), 0) AS n FROM publish_attempts '
                  'WHERE target_id = ?', (target_id,))
    attempt_no = (prev['n'] if prev else 0) + 1
    db.execute(
        'INSERT INTO publish_attempts (target_id, attempt_no, phase, dry_run, request_summary, '
        'response_code, response_summary, error) VALUES (?,?,?,?,?,?,?,?)',
        (target_id, attempt_no, phase, 1 if dry_run else 0, redact(request_summary),
         response_code, redact(response_summary), redact(error)))
    return attempt_no


# ----------------------------------------------------------------------- execution

def resolve_adapter(platform: str):
    """Import and return the adapter module for a platform."""
    name = ADAPTERS.get(platform)
    if not name:
        raise PublishError(f'no publish adapter registered for platform {platform!r}',
                           retryable=False, phase='validate')
    import importlib
    return importlib.import_module(f'app.publish.{name}')


def run_target(target: dict, post: dict, dry_run: bool = True) -> dict:
    """Publish one post to one channel, recording the outcome and an audit row."""
    channel = db.row('SELECT * FROM social_channels WHERE id = ?', (target['channel_id'],))
    if not channel:
        raise PublishError(f'channel {target["channel_id"]} missing', retryable=False)

    if not channel['publish_enabled']:
        if not dry_run:
            set_target_status(target['id'], 'skipped',
                              last_error='channel has publish_enabled = 0')
        log_attempt(target['id'], 'validate', dry_run,
                    request_summary=f'channel {channel["platform"]}:{channel["external_id"]}',
                    error='channel not enabled for publishing')
        return outcome(False, dry_run, error='channel not enabled for publishing')

    media = db.jloads(post.get('media_json'), []) or []
    # A dry run must leave the queue exactly as it found it -- it only writes audit rows.
    if not dry_run:
        set_target_status(target['id'], 'publishing', attempts=target['attempts'] + 1)

    def _fail(message: str, phase: str = 'publish', response_code: int = None,
              retryable: bool = False) -> dict:
        if not dry_run:
            set_target_status(target['id'], 'failed', last_error=message[:500])
        log_attempt(target['id'], phase, dry_run, error=message, response_code=response_code)
        return outcome(False, dry_run, error=message, retryable=retryable)

    try:
        adapter = resolve_adapter(channel['platform'])
        result = adapter.publish(channel, post, media, dry_run=dry_run)
    except PublishError as exc:
        return _fail(str(exc), phase=exc.phase or 'publish', response_code=exc.response_code,
                     retryable=exc.retryable)
    except Exception as exc:  # adapter bug or unexpected platform response
        log.exception('adapter %s raised', channel['platform'])
        return _fail(f'{type(exc).__name__}: {exc}')

    if not dry_run:
        if result.get('ok'):
            set_target_status(target['id'], 'published',
                              external_post_id=result.get('external_post_id'),
                              permalink=result.get('permalink'),
                              published_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                              last_error=None)
        else:
            set_target_status(target['id'], 'failed',
                              last_error=(result.get('error') or 'unknown failure')[:500])

    log_attempt(target['id'], 'publish', dry_run,
                request_summary=json.dumps({'platform': channel['platform'],
                                            'external_id': channel['external_id'],
                                            'media_count': len(media)}),
                response_summary=json.dumps(result.get('detail') or {})[:2000],
                error=result.get('error'))
    return result


def run_post(post_id: int, dry_run: bool = True) -> dict:
    """Fan a post out to every pending target and roll the results up onto the post."""
    post = db.row('SELECT * FROM social_posts WHERE id = ?', (post_id,))
    if not post:
        raise PublishError(f'post {post_id} does not exist', retryable=False)
    assert_publishable(post)

    targets = db.rows(
        "SELECT * FROM social_post_targets WHERE post_id = ? AND status IN ('pending','failed') "
        'ORDER BY id', (post_id,))
    if not targets:
        raise PublishError(f'post {post_id} has no pending targets', retryable=False,
                           phase='validate')

    if not dry_run:
        set_post_status(post_id, 'publishing')

    results = [run_target(t, post, dry_run=dry_run) for t in targets]

    if dry_run:
        return {'post_id': post_id, 'dry_run': True, 'results': results}

    # Roll up across every target on the post, not just the ones attempted now.
    final = db.rows('SELECT status FROM social_post_targets WHERE post_id = ?', (post_id,))
    statuses = {r['status'] for r in final}
    live = statuses - {'skipped'}
    if live and live <= {'published'}:
        rollup = 'published'
    elif 'published' in statuses:
        rollup = 'partial'
    else:
        rollup = 'failed'
    set_post_status(post_id, rollup)
    return {'post_id': post_id, 'dry_run': False, 'status': rollup, 'results': results}


def run_due(dry_run: bool = True, now: datetime = None) -> dict:
    """Scheduler entry point: publish everything due, quarantine everything late."""
    now = now or datetime.now()
    published, late, skipped, failed = [], [], [], []

    for post in due_posts(now):
        verdict = catchup_verdict(post['scheduled_at'], now=now)
        if verdict == 'early':
            continue
        if verdict == 'late':
            # Deliberately not published. A post hours past its slot is a human decision.
            # A dry run reports this without touching the queue, so inspecting what would
            # happen can never itself quarantine a real post.
            if not dry_run:
                set_post_status(post['id'], 'failed')
                db.execute('UPDATE social_post_targets SET last_error = ? '
                           "WHERE post_id = ? AND status = 'pending'",
                           (f'missed its {post["scheduled_at"]} slot by more than the '
                            'catch-up window; not published', post['id']))
            late.append(post['id'])
            log.warning('post %s missed its slot (%s) -- %s', post['id'], post['scheduled_at'],
                        'would be quarantined' if dry_run else 'quarantined, not published')
            continue
        if not dry_run and not client_auto_publish_enabled(post['client_id']):
            skipped.append(post['id'])
            continue
        result = run_post(post['id'], dry_run=dry_run)
        (published if result.get('status') == 'published' else failed).append(post['id'])

    return {'ran_at': now.strftime('%Y-%m-%d %H:%M:%S'), 'dry_run': dry_run,
            'published': published, 'late': late, 'skipped_not_enabled': skipped,
            'failed': failed}
