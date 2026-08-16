#!/usr/bin/env python3
"""Refresh the Meta Ads access token in Windows Credential Manager.

The stored token was invalidated (a Facebook password change / security event revokes
active sessions). app_id and app_secret are still valid, so you only need a new user
token. This script exchanges a short-lived token for a 60-day long-lived one and stores
it under the same keyring service the meta-ads skill and the ad-ops app both read.

One-time step you must do (Meta requires a human):
  1. Open https://developers.facebook.com/tools/explorer
  2. Pick the app, click "Generate Access Token", grant ads_management + ads_read
  3. Copy the short-lived token

Then run:
    python tools/reauth_meta.py               # paste the token when prompted (hidden)
    python tools/reauth_meta.py --token XXX    # or pass it directly

Nothing is echoed; the token goes straight into Credential Manager (service
'adops-meta-ads', key 'access_token').
"""
import argparse
import getpass
import sys

import requests

SERVICE = 'adops-meta-ads'
GRAPH = 'https://graph.facebook.com/v19.0'


def main() -> int:
    parser = argparse.ArgumentParser(description='Refresh the Meta Ads long-lived access token.')
    parser.add_argument('--token', help='Short-lived token from Graph API Explorer '
                                         '(omit to be prompted without echo).')
    args = parser.parse_args()

    try:
        import keyring
    except ImportError:
        print('ERROR: keyring not installed. Run: pip install keyring')
        return 1

    app_id = keyring.get_password(SERVICE, 'app_id')
    app_secret = keyring.get_password(SERVICE, 'app_secret')
    if not app_id or not app_secret:
        print(f'ERROR: app_id / app_secret not found under keyring service {SERVICE!r}. '
              'Run the meta-ads setup_credentials.py first.')
        return 1

    short = args.token or getpass.getpass('Paste the short-lived token from Graph API Explorer: ').strip()
    if not short:
        print('No token provided.')
        return 1

    resp = requests.get(f'{GRAPH}/oauth/access_token', params={
        'grant_type': 'fb_exchange_token',
        'client_id': app_id,
        'client_secret': app_secret,
        'fb_exchange_token': short,
    }, timeout=30)
    data = resp.json()
    if resp.status_code >= 400 or 'access_token' not in data:
        print(f'Exchange failed: {data.get("error", data)}')
        return 1

    long_token = data['access_token']

    # Confirm it works and show which ad accounts it can see (no secrets printed).
    me = requests.get(f'{GRAPH}/me', params={'access_token': long_token}, timeout=30).json()
    accts = requests.get(f'{GRAPH}/me/adaccounts',
                         params={'access_token': long_token, 'fields': 'account_id,name', 'limit': 50},
                         timeout=30).json()

    keyring.set_password(SERVICE, 'access_token', long_token)
    print(f'Success: long-lived token stored for {me.get("name", "user")} '
          f'(expires ~60 days). It can see {len(accts.get("data", []))} ad account(s).')
    print('Restart the ad-ops server (or run a Meta sync) to pull data.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
