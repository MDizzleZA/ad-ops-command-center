import importlib
import traceback

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from app import db
from app.sync import base, csv_import

router = APIRouter(prefix='/api/sync', tags=['sync'])

PLATFORMS = ('meta', 'google', 'bing', 'ga4', 'gsc', 'gbp', 'linkedin')


def run_platform_sync(platform: str, client_id: int | None = None,
                      date_from: str | None = None, date_to: str | None = None):
    module = importlib.import_module(f'app.sync.{platform}_sync')
    for account in base.accounts_for(platform, client_id):
        from_d, to_d = (date_from, date_to) if date_from and date_to else base.window_for(account['id'])
        run_id = base.start_run(platform, account['id'], from_d, to_d)
        try:
            rows_written = module.sync(account, from_d, to_d)
            base.finish_run(run_id, rows_written)
        except Exception as exc:  # keep other accounts syncing
            base.finish_run(run_id, 0, f'{exc.__class__.__name__}: {exc}\n{traceback.format_exc()[-1500:]}')


def run_all_syncs():
    for platform in PLATFORMS:
        try:
            run_platform_sync(platform)
        except Exception:
            pass  # per-account errors already logged in sync_runs


@router.post('/csv')
async def import_csv(file: UploadFile = File(...), account_id: int = Form(...),
                     preset: str = Form(None), mapping: str = Form(None), mode: str = Form(None)):
    account = db.row('SELECT * FROM ad_accounts WHERE id=?', (account_id,))
    if not account:
        raise HTTPException(404, 'account not found')
    content = await file.read()
    mapping_dict = db.jloads(mapping) if mapping else None
    run_id = base.start_run(f"csv:{account['platform']}", account_id, None, None)
    try:
        result = csv_import.import_csv(account_id, content, preset=preset, mapping=mapping_dict, mode=mode)
        base.finish_run(run_id, result['rows_written'])
        return result
    except Exception as exc:
        base.finish_run(run_id, 0, str(exc))
        raise HTTPException(400, str(exc))


@router.get('/csv/presets')
def csv_presets():
    return {name: cfg for name, cfg in csv_import.PRESETS.items()}


@router.post('/{platform}')
def trigger_sync(platform: str, background: BackgroundTasks, client_id: int | None = None,
                 date_from: str | None = None, date_to: str | None = None):
    if platform == 'all':
        background.add_task(run_all_syncs)
        return {'started': list(PLATFORMS)}
    if platform not in PLATFORMS:
        raise HTTPException(400, f'unknown platform {platform}')
    background.add_task(run_platform_sync, platform, client_id, date_from, date_to)
    return {'started': platform}


@router.get('/runs')
def sync_runs(limit: int = 30):
    return db.rows(
        'SELECT s.*, a.alias, a.platform AS account_platform FROM sync_runs s '
        'LEFT JOIN ad_accounts a ON a.id = s.account_id ORDER BY s.id DESC LIMIT ?', (limit,))
