import json

from fastapi import APIRouter, HTTPException

from app import db

router = APIRouter(prefix='/api', tags=['clients'])

JSON_FIELDS_CLIENT = ('kpi_json',)


def _expand(record: dict, fields) -> dict:
    for f in fields:
        record[f.replace('_json', '')] = db.jloads(record.pop(f, None))
    return record


@router.get('/clients')
def list_clients():
    clients = db.rows("SELECT * FROM clients ORDER BY CASE status WHEN 'active' THEN 0 WHEN 'paused' THEN 1 "
                      "ELSE 2 END, name")
    return [_expand(c, JSON_FIELDS_CLIENT) for c in clients]


@router.get('/clients/{client_id}')
def get_client(client_id: int):
    client = db.row('SELECT * FROM clients WHERE id=?', (client_id,))
    if not client:
        raise HTTPException(404, 'client not found')
    _expand(client, JSON_FIELDS_CLIENT)
    brand = db.row('SELECT * FROM brand_profiles WHERE client_id=?', (client_id,))
    if brand:
        _expand(brand, ('colors_json', 'fonts_json', 'ad_specs_json'))
    client['brand'] = brand
    client['personas'] = [
        _expand(p, ('demographics_json', 'pain_points_json', 'triggers_json', 'objections_json'))
        for p in db.rows('SELECT * FROM personas WHERE client_id=?', (client_id,))]
    client['competitors'] = db.rows('SELECT * FROM competitors WHERE client_id=? AND active=1', (client_id,))
    client['accounts'] = db.rows('SELECT * FROM ad_accounts WHERE client_id=?', (client_id,))
    client['compliance_rules'] = db.rows(
        'SELECT * FROM compliance_rules WHERE client_id=? OR client_id IS NULL ORDER BY severity', (client_id,))
    return client


@router.post('/clients')
def create_client(payload: dict):
    name = (payload.get('name') or '').strip()
    if not name:
        raise HTTPException(400, 'name required')
    slug = payload.get('slug') or name.lower().replace(' ', '-')
    client_id = db.execute(
        'INSERT INTO clients (name, slug, status, industry, monthly_budget_zar, notes) VALUES (?,?,?,?,?,?)',
        (name, slug, payload.get('status', 'active'), payload.get('industry'),
         payload.get('monthly_budget_zar'), payload.get('notes')))
    return {'id': client_id}


@router.put('/clients/{client_id}/brand')
def update_brand(client_id: int, payload: dict):
    db.execute(
        'INSERT INTO brand_profiles (client_id, colors_json, fonts_json, logo_path, tagline, tone_of_voice, '
        'ad_specs_json, disclaimer_text, style_rules) VALUES (?,?,?,?,?,?,?,?,?) '
        'ON CONFLICT(client_id) DO UPDATE SET colors_json=excluded.colors_json, fonts_json=excluded.fonts_json, '
        'logo_path=COALESCE(excluded.logo_path, brand_profiles.logo_path), tagline=excluded.tagline, '
        'tone_of_voice=excluded.tone_of_voice, ad_specs_json=excluded.ad_specs_json, '
        'disclaimer_text=excluded.disclaimer_text, style_rules=excluded.style_rules, '
        "updated_at=datetime('now')",
        (client_id, json.dumps(payload.get('colors')), json.dumps(payload.get('fonts')),
         payload.get('logo_path'), payload.get('tagline'), payload.get('tone_of_voice'),
         json.dumps(payload.get('ad_specs')), payload.get('disclaimer_text'), payload.get('style_rules')))
    return {'ok': True}


@router.post('/clients/{client_id}/competitors')
def add_competitor(client_id: int, payload: dict):
    name = (payload.get('name') or '').strip()
    if not name:
        raise HTTPException(400, 'name required')
    comp_id = db.execute(
        'INSERT INTO competitors (client_id, name, fb_page_url, fb_page_id, ig_handle, website, notes) '
        'VALUES (?,?,?,?,?,?,?)',
        (client_id, name, payload.get('fb_page_url'), payload.get('fb_page_id'),
         payload.get('ig_handle'), payload.get('website'), payload.get('notes')))
    return {'id': comp_id}


@router.put('/competitors/{comp_id}')
def update_competitor(comp_id: int, payload: dict):
    db.execute('UPDATE competitors SET name=?, fb_page_url=?, fb_page_id=?, ig_handle=?, website=?, '
               'notes=?, active=? WHERE id=?',
               (payload.get('name'), payload.get('fb_page_url'), payload.get('fb_page_id'),
                payload.get('ig_handle'), payload.get('website'), payload.get('notes'),
                1 if payload.get('active', True) else 0, comp_id))
    return {'ok': True}


@router.get('/settings')
def get_settings():
    return {r['key']: r['value'] for r in db.rows('SELECT key, value FROM settings')}


@router.put('/settings')
def put_settings(payload: dict):
    for key, value in payload.items():
        db.set_setting(key, str(value))
    return {'ok': True}
