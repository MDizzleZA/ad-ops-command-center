#!/usr/bin/env python3
"""Regenerate the Google Ads OAuth refresh token and write it into google-ads.yaml.

The stored refresh token was revoked ("invalid_grant"), which happens when the
Google password changes, the token is unused for 6 months, or the OAuth app is in
"Testing" mode (7-day token life). client_id / client_secret / developer_token are
still valid, so only the refresh_token needs replacing.

Usage:
    python tools/reauth_google.py

A browser window opens for Google sign-in + consent. Sign in with the account that
has access to the Ads accounts (the one behind MCC 'login_customer_id'). On success
the new refresh_token is written back into ~/google-ads.yaml (all other fields kept).
Nothing is printed to the terminal or logs.
"""
import sys
from pathlib import Path

YAML_PATH = Path.home() / 'google-ads.yaml'
SCOPES = ['https://www.googleapis.com/auth/adwords']


def main() -> int:
    try:
        import yaml
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        print(f'Missing dependency: {exc}. Run: pip install google-ads google-auth-oauthlib')
        return 1

    if not YAML_PATH.exists():
        print(f'ERROR: {YAML_PATH} not found.')
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

    cfg['refresh_token'] = creds.refresh_token
    YAML_PATH.write_text(yaml.safe_dump(cfg, default_flow_style=False, sort_keys=False),
                         encoding='utf-8')
    print(f'Success: new refresh_token written to {YAML_PATH}.')
    print('Verify with:  python "gads.py" check')
    return 0


if __name__ == '__main__':
    sys.exit(main())
