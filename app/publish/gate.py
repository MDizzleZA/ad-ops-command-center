"""The compliance gate every post must pass before it can be scheduled.

Publishing is not a direct action in this system. A post walks:

    draft -> pending_compliance -> pending_approval -> scheduled -> published

and this module owns the two middle steps. The design point that matters: a 'block'
verdict is recorded on the row itself, and app/publish/base.assert_publishable() refuses
any post carrying it. So a blocked caption cannot be published by the scheduler tick, by a
manual publish-now, or by a retry -- the only way forward is to edit the caption and
re-run the check. That is what makes this safe to point at an FSP.

Reuses the existing machinery rather than reimplementing it:
  app/services/compliance.py  FAIS/POPIA audit -> pass | warn | block
  app/services/overlay.py     deterministic FSP disclaimer burn-in (never trusted to
                              an image model, because they garble small legal text)
"""
import json
import logging
from datetime import datetime
from pathlib import Path

from app import db
from app.publish.base import DATE_FMT, PublishError, set_post_status
from app.services import compliance

log = logging.getLogger('adops.publish.gate')


def _caption_and_media(post: dict) -> tuple[str, list[dict]]:
    return (post.get('caption') or ''), (db.jloads(post.get('media_json'), []) or [])


def apply_disclaimer(post_id: int, disclaimer: str = None, logo_path: str = None) -> list[dict]:
    """Burn the FSP disclaimer into every image on the post, in place of the original.

    Deterministic compositing, not a generation step. media_json entries gain
    'overlaid_from' so the pre-overlay original is still traceable.
    """
    from app.services.overlay import apply_overlay

    post = db.row('SELECT * FROM social_posts WHERE id = ?', (post_id,))
    if not post:
        raise PublishError(f'post {post_id} does not exist', retryable=False, phase='validate')
    _, media = _caption_and_media(post)
    if not media:
        return []

    updated = []
    for item in media:
        entry = dict(item)
        source = entry.get('file_path')
        if not source or entry.get('overlaid_from'):
            updated.append(entry)
            continue
        try:
            out = apply_overlay(source, disclaimer=disclaimer, logo_path=logo_path)
        except Exception as exc:
            # A failed overlay must not silently publish an image without its disclaimer.
            raise PublishError(
                f'disclaimer overlay failed on {Path(source).name}: {exc}. Refusing to '
                'continue -- publishing this image without its disclaimer is the exact '
                'failure this step exists to prevent.',
                retryable=False, phase='validate') from exc
        entry['overlaid_from'] = source
        entry['file_path'] = str(out)
        # A re-hosted image needs a fresh public URL; the old one points at the original.
        entry.pop('public_url', None)
        updated.append(entry)

    db.execute('UPDATE social_posts SET media_json = ? WHERE id = ?',
               (json.dumps(updated), post_id))
    return updated


def run_compliance(post_id: int, store: bool = True) -> dict:
    """Audit a post's caption and move it to the next state. Returns the audit result.

    pass/warn -> pending_approval (a warn is advisory; a human still sees it)
    block     -> stays at pending_compliance, and assert_publishable() will refuse it
    """
    post = db.row('SELECT * FROM social_posts WHERE id = ?', (post_id,))
    if not post:
        raise PublishError(f'post {post_id} does not exist', retryable=False, phase='validate')

    caption, _ = _caption_and_media(post)
    if not caption.strip():
        raise PublishError(f'post {post_id} has no caption to audit', retryable=False,
                           phase='validate')

    if post['status'] == 'draft':
        set_post_status(post_id, 'pending_compliance')

    result = compliance.audit(caption, post['client_id'])
    status = result.get('status', 'block')
    # Unknown verdict is treated as a block: fail closed, never open.
    if status not in ('pass', 'warn', 'block'):
        log.warning('compliance returned unrecognised status %r for post %s; treating as block',
                    status, post_id)
        status = 'block'
        result['status'] = 'block'

    if not store:
        return result

    db.execute('UPDATE social_posts SET compliance_status = ?, compliance_json = ? WHERE id = ?',
               (status, json.dumps(result), post_id))

    if status == 'block':
        log.warning('post %s blocked by compliance: %s', post_id,
                    [v.get('rule') for v in result.get('violations', [])])
    else:
        set_post_status(post_id, 'pending_approval')
    return result


def approve(post_id: int, approved_by: str, scheduled_at: str = None) -> dict:
    """Approve a post and schedule it. Refuses anything compliance blocked."""
    post = db.row('SELECT * FROM social_posts WHERE id = ?', (post_id,))
    if not post:
        raise PublishError(f'post {post_id} does not exist', retryable=False, phase='validate')
    if post['compliance_status'] == 'block':
        raise PublishError(
            f'post {post_id} was blocked by compliance and cannot be approved. Edit the '
            'caption and re-run the compliance check.', retryable=False, phase='validate')
    if post['compliance_status'] is None:
        raise PublishError(
            f'post {post_id} has not been through the compliance check yet',
            retryable=False, phase='validate')
    if not db.rows('SELECT 1 FROM social_post_targets WHERE post_id = ?', (post_id,)):
        raise PublishError(f'post {post_id} has no target channels', retryable=False,
                           phase='validate')

    when = scheduled_at or post['scheduled_at']
    if when:
        try:
            datetime.strptime(when.strip()[:16], DATE_FMT)
        except ValueError as exc:
            raise PublishError(f'scheduled_at {when!r} is not {DATE_FMT}', retryable=False,
                               phase='validate') from exc

    set_post_status(post_id, 'scheduled', scheduled_at=when,
                    approved_by=approved_by,
                    approved_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    return {'post_id': post_id, 'status': 'scheduled', 'scheduled_at': when,
            'compliance_status': post['compliance_status']}


def reject(post_id: int, reason: str = None) -> dict:
    """Send a post back to draft for editing."""
    set_post_status(post_id, 'draft')
    if reason:
        existing = db.row('SELECT compliance_json FROM social_posts WHERE id = ?', (post_id,))
        blob = db.jloads(existing['compliance_json'] if existing else None, {}) or {}
        blob['rejected_reason'] = reason
        db.execute('UPDATE social_posts SET compliance_json = ? WHERE id = ?',
                   (json.dumps(blob), post_id))
    return {'post_id': post_id, 'status': 'draft', 'reason': reason}


def submit(client_id: int, caption: str, channel_ids: list[int], media: list[dict] = None,
           scheduled_at: str = None, brief_id: int = None) -> int:
    """Create a draft post with its target fan-out. Returns the new post id."""
    if not channel_ids:
        raise PublishError('a post needs at least one target channel', retryable=False,
                           phase='validate')
    post_id = db.execute(
        'INSERT INTO social_posts (client_id, caption, media_json, scheduled_at, brief_id, '
        "status) VALUES (?,?,?,?,?,'draft')",
        (client_id, caption, json.dumps(media or []), scheduled_at, brief_id))
    for channel_id in channel_ids:
        channel = db.row('SELECT id, client_id FROM social_channels WHERE id = ?', (channel_id,))
        if not channel:
            raise PublishError(f'channel {channel_id} does not exist', retryable=False,
                               phase='validate')
        if channel['client_id'] != client_id:
            raise PublishError(
                f'channel {channel_id} belongs to a different client than post {post_id}. '
                'Cross-posting one client\'s content to another\'s channel is refused.',
                retryable=False, phase='validate')
        db.execute('INSERT INTO social_post_targets (post_id, channel_id) VALUES (?,?)',
                   (post_id, channel_id))
    return post_id
