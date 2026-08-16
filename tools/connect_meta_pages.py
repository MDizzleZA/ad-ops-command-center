#!/usr/bin/env python3
"""Discover Facebook Pages and linked Instagram accounts, and register them as channels.

Reads /me/accounts with the stored user token, matches each Page to an ad-ops client by
name, and writes social_channels rows. Page access tokens are visible in that response but
are never stored or printed -- app/publish/meta_publish.py fetches them live at publish
time, so there is one fewer secret at rest.

Every channel is created with publish_enabled = 0 and requires_approval = 1. Registering a
channel does not grant it the ability to post; that is a separate, deliberate switch.

    python tools/connect_meta_pages.py                       # dry run, shows the plan
    python tools/connect_meta_pages.py --apply               # write the rows
    python tools/connect_meta_pages.py --apply --map 112067015181428=your-page
"""
import argparse
import re
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.publish.meta_publish import _graph, _user_token  # noqa: E402

TIMEOUT = 60


def _norm(text: str) -> set[str]:
    """Significant lowercase word tokens, for loose name matching."""
    stop = {'the', 'and', 'ltd', 'pty', 'digital', 'sa', 'group'}
    words = re.split(r'[^a-z0-9]+', (text or '').lower())
    return {w for w in words if w and w not in stop}


def match_client(page_name: str, clients: list[dict]) -> tuple[dict | None, str]:
    """Best-effort client match. Returns (client, reason)."""
    page_tokens = _norm(page_name)
    best, best_score = None, 0
    for client in clients:
        overlap = page_tokens & (_norm(client['name']) | _norm(client['slug']))
        if len(overlap) > best_score:
            best, best_score = client, len(overlap)
    if not best:
        return None, 'no token overlap with any client name'
    return best, f'matched on {best_score} shared word(s)'


def main() -> int:
    parser = argparse.ArgumentParser(
        description='Register Facebook Pages and Instagram accounts as publishing channels.')
    parser.add_argument('--apply', action='store_true',
                        help='write the rows (default is a dry run)')
    parser.add_argument('--map', action='append', default=[], metavar='PAGE_ID=CLIENT_SLUG',
                        help='force a Page to a client, repeatable')
    args = parser.parse_args()

    overrides = {}
    for pair in args.map:
        if '=' not in pair:
            print(f'ERROR: --map needs PAGE_ID=CLIENT_SLUG, got {pair!r}')
            return 1
        page_id, slug = pair.split('=', 1)
        overrides[page_id.strip()] = slug.strip()

    db.migrate()
    clients = db.rows('SELECT id, name, slug FROM clients ORDER BY name')
    by_slug = {c['slug']: c for c in clients}
    for slug in overrides.values():
        if slug not in by_slug:
            print(f'ERROR: no client with slug {slug!r}. Known: {", ".join(sorted(by_slug))}')
            return 1

    resp = requests.get(f'{_graph()}/me/accounts', params={
        'access_token': _user_token(),
        'fields': 'id,name,instagram_business_account{id,username}',
        'limit': 100,
    }, timeout=TIMEOUT)
    body = resp.json()
    if 'error' in body:
        print(f'ERROR from Meta: {body["error"].get("message")}')
        return 1
    pages = body.get('data') or []
    if not pages:
        print('This token administers no Pages.')
        return 1

    planned, unmatched = [], []
    for page in pages:
        page_id, page_name = str(page['id']), page.get('name') or page_id
        if page_id in overrides:
            client, reason = by_slug[overrides[page_id]], 'forced by --map'
        else:
            client, reason = match_client(page_name, clients)
        if not client:
            unmatched.append((page_id, page_name, reason))
            continue
        planned.append({'platform': 'facebook', 'external_id': page_id, 'name': page_name,
                        'handle': None, 'parent': None, 'client': client, 'reason': reason})
        ig = page.get('instagram_business_account') or {}
        if ig.get('id'):
            planned.append({'platform': 'instagram', 'external_id': str(ig['id']),
                            'name': f'{page_name} (Instagram)',
                            'handle': ig.get('username'), 'parent': page_id,
                            'client': client, 'reason': f'linked to Page {page_id}'})

    print(f'\n{"ACTION":<8} {"PLATFORM":<10} {"CHANNEL":<38} {"CLIENT":<22} WHY')
    print('-' * 118)
    created = skipped = 0
    for entry in planned:
        exists = db.row('SELECT id FROM social_channels WHERE platform=? AND external_id=?',
                        (entry['platform'], entry['external_id']))
        action = 'exists' if exists else ('create' if args.apply else 'would')
        label = entry['handle'] and f'@{entry["handle"]}' or entry['name']
        print(f'{action:<8} {entry["platform"]:<10} {label[:37]:<38} '
              f'{entry["client"]["name"][:21]:<22} {entry["reason"]}')
        if exists:
            skipped += 1
            continue
        if args.apply:
            db.execute(
                'INSERT INTO social_channels (client_id, platform, external_id, handle, name, '
                'token_ref, parent_external_id, publish_enabled, requires_approval) '
                "VALUES (?,?,?,?,?,'adops-meta-ads',?,0,1)",
                (entry['client']['id'], entry['platform'], entry['external_id'],
                 entry['handle'], entry['name'], entry['parent']))
            created += 1

    if unmatched:
        print('\nUnmatched Pages (pass --map PAGE_ID=CLIENT_SLUG to assign):')
        for page_id, page_name, reason in unmatched:
            print(f'  {page_id}  {page_name}  -- {reason}')
        print(f'  Known client slugs: {", ".join(sorted(by_slug))}')

    if args.apply:
        print(f'\n{created} channel(s) created, {skipped} already present.')
        print('All are publish_enabled = 0. Enable one deliberately before it can post:')
        print("  UPDATE social_channels SET publish_enabled=1 WHERE platform='facebook' "
              "AND external_id='<page id>';")
    else:
        print(f'\nDry run: {len(planned) - skipped} channel(s) would be created. '
              'Re-run with --apply.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
