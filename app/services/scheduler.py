"""APScheduler jobs: daily metric sync + weekly competitor scans (settings-driven)."""
import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app import db

log = logging.getLogger('adops.scheduler')
_scheduler: BackgroundScheduler | None = None


def _daily_sync():
    from app.routers.sync import run_all_syncs
    log.info('scheduled daily sync starting')
    run_all_syncs()
    # After fresh metrics land, refresh the grader's recommendation queue per active client
    try:
        from app.services import grader
        for row in db.rows("SELECT id FROM clients WHERE status='active'"):
            try:
                grader.queue_recommendations(row['id'])
            except Exception as exc:
                log.warning('grader queue failed for client %s: %s', row['id'], exc)
    except Exception as exc:
        log.warning('grader refresh skipped: %s', exc)


def _daily_pipeline():
    from app.services.pipeline import run_daily_batches
    log.info('scheduled daily creative pipeline starting')
    run_daily_batches()


def _publish_tick():
    """Publish anything due and quarantine anything past its catch-up window.

    Also registered as a Windows Task Scheduler job (tools/publish_tick.py) because this
    in-process scheduler dies with the app -- which is why metric sync once went dark for
    8 days. A missed report is an annoyance; a missed client post is a broken commitment.
    """
    from app.publish import base
    result = base.run_due(dry_run=False)
    if result['published'] or result['late'] or result['failed']:
        log.info('publish tick: published=%s late=%s failed=%s skipped=%s',
                 result['published'], result['late'], result['failed'],
                 result['skipped_not_enabled'])
    if result['late']:
        log.warning('posts missed their slot and were NOT published: %s', result['late'])


def _weekly_scans():
    from app.services.scans_runner import run_scan
    clients = db.rows('SELECT DISTINCT c.client_id FROM competitors c WHERE c.active=1')
    for row in clients:
        try:
            run_scan(row['client_id'], kind='ads')
            run_scan(row['client_id'], kind='organic')
        except Exception as exc:
            log.warning('scheduled scan failed for client %s: %s', row['client_id'], exc)


def start():
    global _scheduler
    if _scheduler:
        return
    sync_time = db.setting('sync_daily_time', '06:30')
    scan_day = db.setting('scan_weekly_day', 'mon')
    scan_time = db.setting('scan_weekly_time', '06:00')
    pipe_time = db.setting('pipeline_daily_time', '07:00')
    sync_h, sync_m = sync_time.split(':')
    scan_h, scan_m = scan_time.split(':')
    pipe_h, pipe_m = pipe_time.split(':')
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(_daily_sync, CronTrigger(hour=int(sync_h), minute=int(sync_m)), id='daily_sync')
    _scheduler.add_job(_weekly_scans, CronTrigger(day_of_week=scan_day, hour=int(scan_h), minute=int(scan_m)),
                       id='weekly_scans')
    _scheduler.add_job(_daily_pipeline, CronTrigger(hour=int(pipe_h), minute=int(pipe_m)),
                       id='daily_pipeline')
    tick = max(1, int(db.setting('publish_tick_minutes', '5')))
    _scheduler.add_job(_publish_tick, CronTrigger(minute=f'*/{tick}'), id='publish_tick',
                       max_instances=1, coalesce=True)
    _scheduler.start()
    log.info('scheduler started: daily sync %s, weekly scans %s %s, daily pipeline %s, '
             'publish tick every %sm', sync_time, scan_day, scan_time, pipe_time, tick)


def stop():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
