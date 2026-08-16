#!/usr/bin/env python3
"""Authorise Google Business Profile access and write tokens for gbp_sync.

Reuses the client_id / client_secret from ~/google-ads.yaml (same GCP OAuth
client; scope consent is independent per token) and requests the
business.manage scope. On success the refresh token is written to
~/.adops/gbp-tokens.json for app/sync/gbp_sync.py.

Note: the Business Profile APIs also require Google-approved API access for
the GCP project (default quota is 0). Until Google approves the access
request, syncs will fail with a quota/permission error even after this tool
succeeds.

Usage:
    python tools/reauth_gbp.py

A browser window opens for Google sign-in + consent. Sign in with the account
that manages the Business Profile locations. Nothing secret is printed.
"""
import json
import sys
from pathlib import Path

YAML_PATH = Path.home() / 'google-ads.yaml'
TOKENS_PATH = Path(r'~/.adops/gbp-tokens.json')
SCOPES = ['https://www.googleapis.com/auth/business.manage']


def main() -> int:
    try:
        import yaml
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(f'Missing dependency: {exc}. Run: pip install google-auth-oauthlib pyyaml')
        return 1

    if not YAML_PATH.exists():
        print(f'ERROR: {YAML_PATH} not found (needed for client_id/client_secret).')
        return 1

    cfg = yaml.safe_load(YAML_PATH.read_text(encoding='utf-8'))
    client_id = cfg.get('client_id')
    client_secret = cfg.get('client_secret')
    if not client_id or not client_secret:
        print('ERROR: client_id / client_secret missing from google-ads.yaml.')
        return 1

    client_config = {
        'installed': {
            'client_id': client_id,
            'client_secret': client_secret,
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token',
            'redirect_uris': ['http://localhost'],
        }
    }
    flow = InstalledAppFlow.from_client_config(client_config, scopes=SCOPES)
    print('Opening a browser for Google sign-in… approve access, then return here.')
    # prompt=consent + access_type=offline guarantees a fresh refresh_token comes back
    creds = flow.run_local_server(port=0, prompt='consent', access_type='offline')

    if not creds.refresh_token:
        print('ERROR: Google did not return a refresh token. Retry and ensure you approve consent.')
        return 1

    TOKENS_PATH.parent.mkdir(parents=True, exist_ok=True)
    TOKENS_PATH.write_text(json.dumps({
        'client_id': client_id,
        'client_secret': client_secret,
        'refresh_token': creds.refresh_token,
        'token_uri': 'https://oauth2.googleapis.com/token',
    }, indent=2), encoding='utf-8')
    print(f'Success: GBP tokens written to {TOKENS_PATH}.')
    print('Next: seed a gbp account (external_id = numeric location id) and run POST /api/sync/gbp.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
