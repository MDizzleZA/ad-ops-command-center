#!/usr/bin/env python3
"""Publish everything due. Standalone entry point for Windows Task Scheduler.

ad-ops' in-process APScheduler only runs while the app is open, which is how metric sync
went dark for 8 days. Scheduled posts cannot depend on someone remembering to leave a
terminal running, so this exists to be driven by the OS instead.

Safe by default at every level:
  * dry-run unless --publish is passed
  * even with --publish, only clients listed in the publish_enabled_clients setting go out
  * only channels with publish_enabled = 1 are touched
  * a post past its publish_catchup_minutes window is quarantined, never published late

    python tools/publish_tick.py                 # report what would go out
    python tools/publish_tick.py --publish       # actually publish
    python tools/publish_tick.py --install-task   # print the schtasks command (does not run it)
"""
import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.publish import base  # noqa: E402

TASK_NAME = 'AdOps Publish'


def install_command(minutes: int) -> str:
    """The schtasks command that registers this script. Printed, never executed here --
    creating a scheduled task is persistent system configuration and is the user's call."""
    python = Path(sys.executable)
    script = ROOT / 'tools' / 'publish_tick.py'
    return (f'schtasks /Create /TN "{TASK_NAME}" /SC MINUTE /MO {minutes} '
            f'/TR "\'{python}\' \'{script}\' --publish" /F')


def main() -> int:
    parser = argparse.ArgumentParser(description='Publish due social posts.')
    parser.add_argument('--publish', action='store_true',
                        help='actually publish (default is a dry run)')
    parser.add_argument('--json', action='store_true', help='emit the result as JSON')
    parser.add_argument('--install-task', action='store_true',
                        help='print the schtasks command to register this as an OS task')
    parser.add_argument('--quiet', action='store_true', help='log warnings and errors only')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s %(message)s',
        filename=str(ROOT / 'data' / 'publish_tick.log'),
    )

    db.migrate()

    if args.install_task:
        minutes = max(1, int(db.setting('publish_tick_minutes', '5')))
        print('Run this in an elevated terminal to register the task:\n')
        print(f'  {install_command(minutes)}\n')
        print('Then verify with:\n')
        print(f'  schtasks /Query /TN "{TASK_NAME}"\n')
        print('Not run automatically: registering a scheduled task is persistent system '
              'configuration.')
        return 0

    enabled = db.setting('publish_enabled_clients', '')
    result = base.run_due(dry_run=not args.publish)

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    mode = 'LIVE' if args.publish else 'dry run'
    print(f'[{result["ran_at"]}] publish tick ({mode})')
    print(f'  auto-publish allow-list: {enabled or "(empty -- nothing auto-publishes)"}')
    for label, key in (('published', 'published'), ('failed', 'failed'),
                       ('late, NOT published', 'late'),
                       ('skipped, client not enabled', 'skipped_not_enabled')):
        ids = result.get(key) or []
        if ids:
            print(f'  {label}: {ids}')
    if not any(result.get(k) for k in ('published', 'failed', 'late', 'skipped_not_enabled')):
        print('  nothing due')
    # Non-zero when something needs a human: a failure or a missed slot.
    return 1 if (result.get('failed') or result.get('late')) else 0


if __name__ == '__main__':
    sys.exit(main())
