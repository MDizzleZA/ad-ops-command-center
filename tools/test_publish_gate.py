#!/usr/bin/env python3
"""Verify the compliance gate actually blocks non-compliant content from publishing.

Makes a Gemini call (the compliance audit) but never contacts a social platform. Uses the
real client compliance rules, since FAIS is the reason this gate exists.

    python tools/test_publish_gate.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.publish import base, gate  # noqa: E402

passed, failed = 0, 0

VIOLATING = ('Invest with us and earn a guaranteed 20% return every year, with zero risk. '
             'Your capital is completely safe and you cannot lose money.')

# the client's rules require the FSP disclosure verbatim on every asset, and prohibit
# suitability language ("see if X fits your retirement") because suitability needs a
# needs-analysis. A caption without the disclosure blocks no matter how careful the copy
# is, so a genuinely publishable caption has to carry it.
FSP_DISCLOSURE = ('Sample Client Management (PTY) Ltd. registration number 2002/004025/07 '
                  'is an Authorised Financial Services Provider. (FSP: 795)')
CLEAN = ('Retirement planning raises hard questions about income, tax and timing. '
         'Our advisers explain the options available and answer your questions. '
         'Book a no-obligation conversation.\n\n' + FSP_DISCLOSURE)


def check(label: str, condition: bool, detail: str = '') -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f'  [PASS] {label}')
    else:
        failed += 1
        print(f'  [FAIL] {label}{" -- " + detail if detail else ""}')


def expect_raises(label: str, fn, *args, **kwargs) -> None:
    """Either refusal type counts: the gate raises PublishError, the state machine
    raises IllegalTransition. Both mean the action was correctly blocked."""
    try:
        fn(*args, **kwargs)
    except (base.PublishError, base.IllegalTransition) as exc:
        check(label, True)
        print(f'         refused: {str(exc)[:130]}')
    except Exception as exc:
        check(label, False, f'raised {type(exc).__name__}: {exc}')
    else:
        check(label, False, 'no exception raised')


def main() -> int:
    db.migrate()
    client = db.row("SELECT id, name FROM clients WHERE slug='acme-corp'")
    if not client:
        print('ERROR: no acme-corp client to test FAIS rules against.')
        return 1
    channel = db.row("SELECT id FROM social_channels WHERE client_id=? AND platform='facebook'",
                     (client['id'],))
    if not channel:
        print('ERROR: no the client facebook channel registered. Run tools/connect_meta_pages.py.')
        return 1
    rules = db.rows('SELECT COUNT(*) AS n FROM compliance_rules WHERE client_id=? '
                    'OR client_id IS NULL', (client['id'],))
    print(f'Auditing against {rules[0]["n"]} compliance rule(s) for {client["name"]}.\n')

    created = []

    print('1. A caption with implied guaranteed returns is blocked')
    bad_id = gate.submit(client['id'], VIOLATING, [channel['id']])
    created.append(bad_id)
    result = gate.run_compliance(bad_id)
    row = db.row('SELECT status, compliance_status FROM social_posts WHERE id=?', (bad_id,))
    check("verdict is 'block'", result.get('status') == 'block', str(result.get('status')))
    check('compliance_status stored as block', row['compliance_status'] == 'block')
    check("status held at 'pending_compliance', not advanced",
          row['status'] == 'pending_compliance', row['status'])
    for violation in (result.get('violations') or [])[:4]:
        print(f'         [{violation.get("severity")}] {violation.get("rule")} '
              f'-- {str(violation.get("phrase"))[:60]!r}')

    print('\n2. A blocked post cannot be forced through any route')
    expect_raises('approve() refuses it', gate.approve, bad_id, 'selftest')
    expect_raises('run_post() refuses it', base.run_post, bad_id, dry_run=True)
    post = db.row('SELECT * FROM social_posts WHERE id=?', (bad_id,))
    expect_raises('assert_publishable() refuses it', base.assert_publishable, post)
    expect_raises('even forcing status to scheduled is rejected by the state machine',
                  base.set_post_status, bad_id, 'scheduled')

    print('\n3. Editing the caption clears the path')
    db.execute('UPDATE social_posts SET caption=? WHERE id=?', (CLEAN, bad_id))
    fixed = gate.run_compliance(bad_id)
    row = db.row('SELECT status, compliance_status FROM social_posts WHERE id=?', (bad_id,))
    check('re-audit no longer blocks', fixed.get('status') in ('pass', 'warn'),
          str(fixed.get('status')))
    check("advanced to 'pending_approval'", row['status'] == 'pending_approval', row['status'])

    print('\n4. Approval requires a compliance verdict first')
    raw_id = gate.submit(client['id'], CLEAN, [channel['id']])
    created.append(raw_id)
    expect_raises('approve() refuses a post never audited', gate.approve, raw_id, 'selftest')

    print('\n5. Approve and schedule the compliant post')
    approved = gate.approve(bad_id, 'selftest', scheduled_at='2026-12-01 09:00')
    row = db.row('SELECT status, scheduled_at, approved_by FROM social_posts WHERE id=?',
                 (bad_id,))
    check("status is 'scheduled'", row['status'] == 'scheduled', row['status'])
    check('scheduled_at recorded', row['scheduled_at'] == '2026-12-01 09:00',
          str(row['scheduled_at']))
    check('approver recorded', row['approved_by'] == 'selftest')
    check('approve() reports the compliance verdict it allowed through',
          approved['compliance_status'] in ('pass', 'warn'))

    print('\n6. A malformed schedule time is rejected')
    # Must use a post that has already cleared compliance, otherwise approve() refuses on
    # the missing-verdict check and the timestamp validation is never reached.
    gate.run_compliance(raw_id)
    verdict = db.row('SELECT compliance_status FROM social_posts WHERE id=?',
                     (raw_id,))['compliance_status']
    if verdict == 'block':
        print(f'  [SKIP] audit blocked the control caption ({verdict}); cannot isolate the '
              'timestamp check')
    else:
        expect_raises('approve() refuses a bad timestamp on an otherwise approvable post',
                      gate.approve, raw_id, 'selftest', 'next tuesday')
        still = db.row('SELECT status FROM social_posts WHERE id=?', (raw_id,))['status']
        check('the post stayed at pending_approval after the rejected timestamp',
              still == 'pending_approval', still)

    print("\n7. One client's content cannot target another client's channel")
    other = db.row('SELECT id FROM social_channels WHERE client_id != ?', (client['id'],))
    if other:
        expect_raises('submit() refuses a cross-client target', gate.submit, client['id'],
                      CLEAN, [other['id']])
    else:
        print('  [SKIP] no channel belonging to another client')

    for post_id in created:
        db.execute('DELETE FROM publish_attempts WHERE target_id IN '
                   '(SELECT id FROM social_post_targets WHERE post_id=?)', (post_id,))
        db.execute('DELETE FROM social_post_targets WHERE post_id=?', (post_id,))
        db.execute('DELETE FROM social_posts WHERE id=?', (post_id,))

    print(f'\n{passed} passed, {failed} failed')
    return 0 if failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
