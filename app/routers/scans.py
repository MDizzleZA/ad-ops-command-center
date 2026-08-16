from fastapi import APIRouter, BackgroundTasks, HTTPException

from app import db
from app.services.media import media_url
from app.services.scans_runner import run_scan

router = APIRouter(prefix='/api/scans', tags=['scans'])


@router.post('/run')
def trigger_scan(payload: dict, background: BackgroundTasks):
    client_id = payload.get('client_id')
    if not client_id:
        raise HTTPException(400, 'client_id required')
    kind = payload.get('kind', 'ads')
    if kind not in ('ads', 'organic'):
        raise HTTPException(400, 'kind must be ads|organic')
    scan_id = db.execute('INSERT INTO scans (client_id, kind, status) VALUES (?,?,?)',
                         (client_id, kind, 'queued'))

    def runner():
        db.execute("UPDATE scans SET status='running' WHERE id=?", (scan_id,))
        try:
            from app.services import scans_runner
            scans_runner._run_scan_inner(scan_id, client_id, kind)
            db.execute("UPDATE scans SET status='done', finished_at=datetime('now') WHERE id=?", (scan_id,))
        except Exception as exc:
            db.execute("UPDATE scans SET status='error', error=?, finished_at=datetime('now') WHERE id=?",
                       (f'{exc.__class__.__name__}: {exc}', scan_id))

    background.add_task(runner)
    return {'scan_id': scan_id}


@router.get('')
def list_scans(client_id: int | None = None, limit: int = 20):
    where, params = ('WHERE client_id=?', [client_id]) if client_id else ('', [])
    return db.rows(f'SELECT id, client_id, kind, status, started_at, finished_at, total_ads, new_ads, '
                   f'error FROM scans {where} ORDER BY id DESC LIMIT ?', params + [limit])


@router.get('/{scan_id}')
def scan_detail(scan_id: int):
    scan = db.row('SELECT * FROM scans WHERE id=?', (scan_id,))
    if not scan:
        raise HTTPException(404, 'scan not found')
    summary = db.jloads(scan.pop('summary_json', None), {'per_competitor': [], 'insights': None})
    ads = db.rows(
        'SELECT r.*, s.is_new, c.name AS competitor_name FROM scan_ads s '
        'JOIN reference_ads r ON r.id=s.reference_ad_id '
        'LEFT JOIN competitors c ON c.id=s.competitor_id WHERE s.scan_id=? '
        'ORDER BY s.is_new DESC, r.started_running DESC', (scan_id,))
    for a in ads:
        a['media'] = media_url(a.get('local_media_path')) or a.get('media_url')
    return {'scan': scan, 'summary': summary, 'ads': ads}


@router.get('/{scan_id}/organic')
def scan_organic(scan_id: int):
    return db.rows(
        'SELECT o.*, c.name AS competitor_name FROM organic_posts o '
        'JOIN competitors c ON c.id=o.competitor_id WHERE o.scan_id=? '
        'ORDER BY (o.likes + o.comments * 3 + o.shares * 5) DESC', (scan_id,))
