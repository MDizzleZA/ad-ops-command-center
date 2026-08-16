#!/usr/bin/env python3
"""Offline checks for the publish state machine, compliance gate and catch-up window.

No network, no platform credentials. Uses a scratch client and a disabled channel, then
deletes everything it created. Run before wiring any real adapter:

    python tools/test_publish_base.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Runnable as `python tools/test_publish_base.py` from the repo root, where sys.path[0]
# is tools/ rather than the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.publish import base  # noqa: E402

SLUG = '_publish_selftest'
passed, failed = 0, 0


def check(label: str, condition: bool, detail: str = '') -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f'  [PASS] {label}')
    else:
        failed += 1
        print(f'  [FAIL] {label}{" -- " + detail if detail else ""}')


def expect_raises(label: str, exc_type, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        check(label, True)
        print(f'         rejected with: {str(exc)[:110]}')
        return
    except Exception as exc:
        check(label, False, f'raised {type(exc).__name__} instead of {exc_type.__name__}: {exc}')
        return
    check(label, False, 'no exception raised')


def cleanup() -> None:
    row = db.row('SELECT id FROM clients WHERE slug = ?', (SLUG,))
    if not row:
        return
    cid = row['id']
    db.execute('DELETE FROM publish_attempts WHERE target_id IN (SELECT t.id FROM '
               'social_post_targets t JOIN social_posts p ON p.id = t.post_id '
               'WHERE p.client_id = ?)', (cid,))
    db.execute('DELETE FROM social_post_targets WHERE post_id IN '
               '(SELECT id FROM social_posts WHERE client_id = ?)', (cid,))
    db.execute('DELETE FROM social_posts WHERE client_id = ?', (cid,))
    db.execute('DELETE FROM social_channels WHERE client_id = ?', (cid,))
    db.execute('DELETE FROM clients WHERE id = ?', (cid,))


def main() -> int:
    db.migrate()
    cleanup()

    client_id = db.execute("INSERT INTO clients (name, slug, status) VALUES (?,?,'active')",
                           ('Publish Self Test', SLUG))
    channel_id = db.execute(
        'INSERT INTO social_channels (client_id, platform, external_id, name, publish_enabled) '
        "VALUES (?,'facebook','selftest-page-000','Self Test Page',0)", (client_id,))

    print('\n1. Illegal status transitions are rejected')
    post_id = db.execute("INSERT INTO social_posts (client_id, caption, status) "
                         "VALUES (?,'A test caption','draft')", (client_id,))
    expect_raises('draft -> published is refused', base.IllegalTransition,
                  base.set_post_status, post_id, 'published')
    expect_raises('draft -> scheduled is refused', base.IllegalTransition,
                  base.set_post_status, post_id, 'scheduled')
    expect_raises('draft -> publishing is refused', base.IllegalTransition,
                  base.set_post_status, post_id, 'publishing')

    print('\n2. The legal route through the machine works')
    for step in ('pending_compliance', 'pending_approval', 'scheduled'):
        base.set_post_status(post_id, step)
    check('draft -> pending_compliance -> pending_approval -> scheduled',
          db.row('SELECT status FROM social_posts WHERE id=?', (post_id,))['status'] == 'scheduled')
    base.set_post_status(post_id, 'failed')
    check("scheduled -> failed is legal (missed-window quarantine, one hop)",
          db.row('SELECT status FROM social_posts WHERE id=?', (post_id,))['status'] == 'failed')

    print('\n3. published is terminal')
    base.set_post_status(post_id, 'publishing')
    base.set_post_status(post_id, 'published')
    expect_raises('published -> draft is refused', base.IllegalTransition,
                  base.set_post_status, post_id, 'draft')

    print('\n4. Compliance block makes publishing impossible')
    blocked_id = db.execute(
        'INSERT INTO social_posts (client_id, caption, status, compliance_status) '
        "VALUES (?,'Guaranteed 20% returns, no risk','scheduled','block')", (client_id,))
    db.execute('INSERT INTO social_post_targets (post_id, channel_id) VALUES (?,?)',
               (blocked_id, channel_id))
    blocked = db.row('SELECT * FROM social_posts WHERE id=?', (blocked_id,))
    expect_raises('assert_publishable refuses a blocked post', base.PublishError,
                  base.assert_publishable, blocked)
    expect_raises('run_post refuses a blocked post even when scheduled', base.PublishError,
                  base.run_post, blocked_id, dry_run=True)

    print('\n5. A draft cannot be published even with a ready target')
    draft_id = db.execute("INSERT INTO social_posts (client_id, caption, status) "
                          "VALUES (?,'Still a draft','draft')", (client_id,))
    db.execute('INSERT INTO social_post_targets (post_id, channel_id) VALUES (?,?)',
               (draft_id, channel_id))
    expect_raises('run_post refuses a draft', base.PublishError, base.run_post, draft_id,
                  dry_run=True)

    print('\n6. Catch-up window classification')
    now = datetime(2026, 7, 30, 9, 0)
    check('a slot 5 minutes ago is due',
          base.catchup_verdict((now - timedelta(minutes=5)).strftime(base.DATE_FMT),
                               now=now, window_minutes=30) == 'due')
    check('a slot 4 hours ago is late, not published',
          base.catchup_verdict((now - timedelta(hours=4)).strftime(base.DATE_FMT),
                               now=now, window_minutes=30) == 'late')
    check('a future slot is early',
          base.catchup_verdict((now + timedelta(minutes=20)).strftime(base.DATE_FMT),
                               now=now, window_minutes=30) == 'early')
    check('no scheduled_at means publish on approval',
          base.catchup_verdict(None, now=now) == 'due')
    check('an unparseable timestamp is treated as late, not due',
          base.catchup_verdict('not a date', now=now) == 'late')

    print('\n7. A disabled channel is skipped, and a dry run mutates nothing')
    ready_id = db.execute("INSERT INTO social_posts (client_id, caption, status) "
                          "VALUES (?,'Ready to go','scheduled')", (client_id,))
    target_id = db.execute('INSERT INTO social_post_targets (post_id, channel_id) VALUES (?,?)',
                           (ready_id, channel_id))
    before = db.row('SELECT status, attempts FROM social_post_targets WHERE id=?', (target_id,))
    result = base.run_post(ready_id, dry_run=True)
    after = db.row('SELECT status, attempts FROM social_post_targets WHERE id=?', (target_id,))
    check('disabled channel reports not-enabled',
          result['results'][0]['error'] == 'channel not enabled for publishing',
          str(result['results'][0]))
    check('dry run left target status untouched',
          (before['status'], before['attempts']) == (after['status'], after['attempts']),
          f'{dict(before)} -> {dict(after)}')
    check('dry run left post status untouched',
          db.row('SELECT status FROM social_posts WHERE id=?', (ready_id,))['status'] == 'scheduled')
    check('dry run still wrote an audit row',
          db.row('SELECT COUNT(*) AS n FROM publish_attempts WHERE target_id=?',
                 (target_id,))['n'] > 0)

    print('\n8. Secrets never reach the audit trail')
    dirty = 'POST /me/photos?access_token=EAAG12345secret&message=hi'
    check('redact() masks an access_token in a request summary',
          'EAAG12345secret' not in base.redact(dirty), base.redact(dirty))
    check('redact() keeps the rest of the summary readable',
          'me/photos' in base.redact(dirty), base.redact(dirty))

    print('\n9. Auto-publish allow-list is closed by default')
    check('client not in publish_enabled_clients cannot auto-publish',
          base.client_auto_publish_enabled(client_id) is False)

    print('\n10. run_due quarantines a missed slot instead of publishing it late')
    stale_id = db.execute(
        'INSERT INTO social_posts (client_id, caption, status, scheduled_at) '
        "VALUES (?,'Should have gone out this morning','scheduled',?)",
        (client_id, (datetime.now() - timedelta(hours=5)).strftime(base.DATE_FMT)))
    db.execute('INSERT INTO social_post_targets (post_id, channel_id) VALUES (?,?)',
               (stale_id, channel_id))

    # Dry run must classify it as late without touching the row -- otherwise merely
    # inspecting the queue would quarantine genuinely scheduled posts.
    dry = base.run_due(dry_run=True)
    check('dry run reports it as late', stale_id in dry['late'], str(dry))
    check('dry run did NOT mutate the post',
          db.row('SELECT status FROM social_posts WHERE id=?',
                 (stale_id,))['status'] == 'scheduled')

    live = base.run_due(dry_run=False)
    check('live run quarantines it', stale_id in live['late'], str(live))
    check('post is marked failed, not published',
          db.row('SELECT status FROM social_posts WHERE id=?', (stale_id,))['status'] == 'failed')
    check('it never reached the published list', stale_id not in live['published'])
    check('the reason is recorded on the target',
          'missed its' in (db.row('SELECT last_error FROM social_post_targets WHERE post_id=?',
                                  (stale_id,))['last_error'] or ''))

    cleanup()
    print(f'\n{passed} passed, {failed} failed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        cleanup()
        raise
