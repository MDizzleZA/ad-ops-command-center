#!/usr/bin/env python3
"""Phase 0 gate: report what publishing capability the stored credentials actually grant.

Read-only. Makes no writes to any platform and prints no secret values -- only scope
names, page/org names and ids, expiry dates and counts.

ad-ops today is read-only insights plus Meta pause/budget/bid. Before any publish
adapter is worth writing, we need to know which of the four target channels can
actually accept a post with the credentials already on this machine. This script
answers that, per platform, and exits non-zero if a required scope is missing.

Checks performed:
  Meta      /debug_token for granted scopes, /me/accounts for admined Pages, and
            instagram_business_account linkage per Page.
  LinkedIn  token introspection for scopes, plus a probe of recent API versions to
            find one that does not return HTTP 426 (app/sync/linkedin_sync.py is
            pinned to a stale 202506 and currently fails on every sync).
  X         presence of credentials only -- nothing is configured yet.
  WordPress that example.com can serve a public image URL, which is the
            only viable Instagram media host on a laptop with no public address.
  GBP       informational: blocked upstream at Google quota 0.

Usage:
    python tools/check_publish_scopes.py
    python tools/check_publish_scopes.py --json      # machine-readable summary
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

GRAPH = 'https://graph.facebook.com/v19.0'
META_SERVICE = 'adops-meta-ads'
LINKEDIN_SERVICES = ('linkedin:marketing_api', 'linkedin:reporting')
LINKEDIN_TOKENS = Path(r'~/.adops/linkedin-tokens.json')
LINKEDIN_API = 'https://api.linkedin.com'
LINKEDIN_INTROSPECT = 'https://www.linkedin.com/oauth/v2/introspectToken'
X_SERVICE = 'x:default'
WP_TARGETS = Path(r'~/.adops/wp-targets.json')
WP_TARGET_KEY = 'example'
GBP_TOKENS = Path(r'~/.adops/gbp-tokens.json')

TIMEOUT = 30

# Scopes each channel needs before its adapter can publish anything.
META_PAGE_SCOPES = ('pages_show_list', 'pages_read_engagement', 'pages_manage_posts')
META_IG_SCOPES = ('instagram_basic', 'instagram_content_publish')
LINKEDIN_POST_SCOPES = ('w_organization_social',)
LINKEDIN_ADMIN_SCOPES = ('rw_organization_admin', 'r_organization_admin')

OK, MISSING, FAIL, WARN, SKIP = '[OK]', '[MISSING]', '[FAIL]', '[WARN]', '[SKIP]'


def _keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None


def _get_secret(service: str, key: str) -> str | None:
    kr = _keyring()
    if not kr:
        return None
    try:
        return kr.get_password(service, key)
    except Exception:
        return None


def _expiry_note(epoch) -> str:
    """Human-readable expiry from a unix timestamp. 0 means never expires."""
    if not epoch:
        return 'never expires'
    when = datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    days = (when - datetime.now(tz=timezone.utc)).days
    state = f'in {days}d' if days >= 0 else f'{abs(days)}d AGO -- EXPIRED'
    return f'{when:%Y-%m-%d} ({state})'


def _scope_report(granted: set[str], required: tuple[str, ...], label: str) -> bool:
    """Print per-scope status. Returns True when every required scope is present."""
    absent = [s for s in required if s not in granted]
    for scope in required:
        print(f'    {OK if scope in granted else MISSING} {scope}')
    if absent:
        print(f'    -> {label} BLOCKED: grant {", ".join(absent)}')
    return not absent


# --------------------------------------------------------------------------- Meta

def check_meta() -> dict:
    print('\n=== Meta (Facebook Page + Instagram) ===')
    result = {'platform': 'meta', 'page_publish': False, 'ig_publish': False, 'pages': []}

    app_id = _get_secret(META_SERVICE, 'app_id')
    app_secret = _get_secret(META_SERVICE, 'app_secret')
    token = _get_secret(META_SERVICE, 'access_token')
    if not token or not app_id or not app_secret:
        print(f'  {FAIL} credentials not found under Credential Manager service {META_SERVICE!r}.')
        print('        Run tools/reauth_meta.py first.')
        result['error'] = 'no credentials'
        return result

    try:
        resp = requests.get(f'{GRAPH}/debug_token', params={
            'input_token': token,
            'access_token': f'{app_id}|{app_secret}',
        }, timeout=TIMEOUT)
        data = (resp.json() or {}).get('data') or {}
    except requests.RequestException as exc:
        print(f'  {FAIL} could not reach the Graph API: {exc}')
        result['error'] = str(exc)
        return result

    if not data.get('is_valid'):
        err = (data.get('error') or {}).get('message', 'token is not valid')
        print(f'  {FAIL} stored token is invalid: {err}')
        print('        Run tools/reauth_meta.py to mint a fresh one.')
        result['error'] = 'invalid token'
        return result

    granted = set(data.get('scopes') or [])
    result['scopes'] = sorted(granted)
    result['token_expires'] = _expiry_note(data.get('expires_at'))
    print(f'  Token valid. Expires: {result["token_expires"]}')
    print(f'  Granted scopes ({len(granted)}): {", ".join(sorted(granted)) or "none"}')

    print('  Facebook Page publishing:')
    result['page_publish'] = _scope_report(granted, META_PAGE_SCOPES, 'Facebook Page publishing')
    print('  Instagram publishing:')
    result['ig_publish'] = _scope_report(granted, META_IG_SCOPES, 'Instagram publishing')

    # Which Pages does this token actually administer, and which have IG attached?
    # Page access tokens come back in this response; they are never printed.
    try:
        accounts = requests.get(f'{GRAPH}/me/accounts', params={
            'access_token': token,
            'fields': 'id,name,instagram_business_account{id,username}',
            'limit': 100,
        }, timeout=TIMEOUT).json()
    except requests.RequestException as exc:
        print(f'  {WARN} could not list Pages: {exc}')
        return result

    if 'error' in accounts:
        msg = accounts['error'].get('message', 'unknown error')
        print(f'  {WARN} /me/accounts refused: {msg}')
        print('        Usually means pages_show_list was not granted.')
        return result

    pages = accounts.get('data') or []
    if not pages:
        print(f'  {WARN} token administers no Pages. Publishing has no destination.')
        return result

    print(f'  Pages this token administers ({len(pages)}):')
    for page in pages:
        ig = page.get('instagram_business_account') or {}
        ig_note = (f'IG linked: @{ig.get("username", "?")} (id {ig["id"]})' if ig.get('id')
                   else 'no IG business account linked')
        print(f'    - {page.get("name")} (id {page.get("id")}) -- {ig_note}')
        result['pages'].append({
            'id': page.get('id'),
            'name': page.get('name'),
            'ig_user_id': ig.get('id'),
            'ig_username': ig.get('username'),
        })

    if not any(p['ig_user_id'] for p in result['pages']):
        print(f'  {WARN} no Page has an Instagram Business/Creator account linked.')
        print('        Instagram publishing needs that link even with the right scopes.')
        result['ig_publish'] = False

    return result


# ----------------------------------------------------------------------- LinkedIn

def _linkedin_creds() -> tuple[str | None, str | None, dict]:
    """Returns (client_id, client_secret, token_blob).

    The credential is stored with client_id in the *username* field and the secret as
    the password -- not as separate client_id/client_secret keys -- so it has to be read
    with get_credential() rather than get_password().
    """
    kr = _keyring()
    client_id = client_secret = None
    for service in LINKEDIN_SERVICES:
        try:
            cred = kr.get_credential(service, None) if kr else None
        except Exception:
            cred = None
        if cred:
            client_id, client_secret = cred.username, cred.password
            break
    blob = {}
    if LINKEDIN_TOKENS.exists():
        try:
            blob = json.loads(LINKEDIN_TOKENS.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            blob = {}
    return client_id, client_secret, blob


def _version_candidates(count: int = 15) -> list[str]:
    """Recent YYYYMM LinkedIn version strings, newest first."""
    now = datetime.now()
    year, month = now.year, now.month
    out = []
    for _ in range(count):
        out.append(f'{year}{month:02d}')
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return out


def _probe_linkedin_version(access: str) -> tuple[str | None, str | None]:
    """Find the newest API version that does not return 426. Returns (version, note)."""
    for version in _version_candidates():
        try:
            resp = requests.get(f'{LINKEDIN_API}/rest/me', headers={
                'Authorization': f'Bearer {access}',
                'LinkedIn-Version': version,
                'X-Restli-Protocol-Version': '2.0.0',
            }, timeout=TIMEOUT)
        except requests.RequestException as exc:
            return None, f'network error: {exc}'
        if resp.status_code == 426:
            continue
        if resp.status_code in (401, 403):
            return version, f'version accepted, but auth/scope refused it (HTTP {resp.status_code})'
        if resp.status_code < 400:
            return version, 'accepted'
        return version, f'version accepted, HTTP {resp.status_code}'
    return None, 'every probed version returned 426'


def check_linkedin() -> dict:
    print('\n=== LinkedIn (company page) ===')
    result = {'platform': 'linkedin', 'post_publish': False, 'orgs': []}

    client_id, _client_secret, blob = _linkedin_creds()
    access = blob.get('access_token')
    if not access:
        print(f'  {FAIL} no access token at {LINKEDIN_TOKENS}.')
        print('        Run linkedin_auth.py (in Claude-Work) to complete the OAuth flow.')
        result['error'] = 'no access token'
        return result

    if client_id:
        print(f'  App client_id: {client_id} (secret held in Credential Manager)')

    # The token file records the scope it was granted with, so no introspection call
    # (and no client secret) is needed to know what this token can do.
    granted = set((blob.get('scope') or '').replace(',', ' ').split())
    print(f'  Token expires: {_expiry_note(blob.get("expires_at"))}')
    print(f'  Granted scopes ({len(granted)}): {", ".join(sorted(granted)) or "none"}')

    version, note = _probe_linkedin_version(access)
    result['api_version'] = version
    if version:
        print(f'  {OK} newest working API version: {version} ({note})')
        print(f'        app/sync/linkedin_sync.py is pinned to 202506 -- move this to the '
              f'linkedin_api_version setting.')
    else:
        print(f'  {FAIL} no usable API version found ({note}).')
        result['error'] = note
        return result

    if granted:
        print('  Organic company-page posting:')
        result['post_publish'] = _scope_report(granted, LINKEDIN_POST_SCOPES,
                                               'LinkedIn page posting')
        if not (set(LINKEDIN_ADMIN_SCOPES) & granted):
            print(f'    {WARN} no {" or ".join(LINKEDIN_ADMIN_SCOPES)} -- cannot enumerate '
                  'which company pages you administer.')
        # Diagnose the root cause rather than just reporting absent scopes: organic
        # posting scopes are only offered once the app holds the right LinkedIn product.
        if not result['post_publish'] and granted <= {'email', 'openid', 'profile', 'r_ads',
                                                      'r_ads_reporting', 'rw_ads',
                                                      'r_basicprofile', 'r_1st_connections_size'}:
            print('    -> Root cause: this app only holds LinkedIn\'s Advertising API '
                  'products.')
            print('       Organic posting scopes are gated behind the Community Management '
                  'API product,')
            print('       which is a separate LinkedIn application and approval. Re-running '
                  'OAuth will not')
            print('       grant w_organization_social until that product is approved on the '
                  'app.')
    else:
        print(f'  {SKIP} scope list unavailable, so page-posting capability is unconfirmed.')

    # Enumerate administered organisations when the scope allows it.
    try:
        acls = requests.get(f'{LINKEDIN_API}/rest/organizationAcls', params={
            'q': 'roleAssignee', 'role': 'ADMINISTRATOR', 'projection':
                '(elements*(organization~(localizedName)))',
        }, headers={
            'Authorization': f'Bearer {access}',
            'LinkedIn-Version': version,
            'X-Restli-Protocol-Version': '2.0.0',
        }, timeout=TIMEOUT)
        if acls.status_code >= 400:
            print(f'    {SKIP} could not list company pages (HTTP {acls.status_code}).')
        else:
            for el in (acls.json().get('elements') or []):
                urn = el.get('organization')
                name = ((el.get('organization~') or {}).get('localizedName')) or '?'
                print(f'    - {name} ({urn})')
                result['orgs'].append({'urn': urn, 'name': name})
            if not result['orgs']:
                print(f'    {WARN} no administered company pages returned.')
    except (requests.RequestException, ValueError) as exc:
        print(f'    {SKIP} could not list company pages: {exc}')

    return result


# ------------------------------------------------------------------------------ X

def check_x() -> dict:
    print('\n=== X / Twitter ===')
    result = {'platform': 'x', 'post_publish': False}
    for key in ('client_id', 'api_key', 'access_token', 'bearer_token'):
        if _get_secret(X_SERVICE, key):
            print(f'  {OK} found {key} under Credential Manager service {X_SERVICE!r}.')
            result['configured'] = True
            break
    else:
        print(f'  {MISSING} nothing stored under Credential Manager service {X_SERVICE!r}.')
        print('        X is unconfigured -- no app, no tokens. Needed before v1 can post:')
        print('          1. An X developer app with OAuth 2.0 and tweet.write scope.')
        print('          2. A paid tier if the free tier write cap is too low '
              '(free is capped per month).')
        result['configured'] = False
    return result


# ---------------------------------------------------------------- WordPress media

def check_wp_media_host() -> dict:
    """Instagram needs a URL Meta's servers can fetch. The WP media library is that host."""
    print('\n=== WordPress media host (Instagram public-URL requirement) ===')
    result = {'platform': 'wp_media_host', 'usable': False}

    if not WP_TARGETS.exists():
        print(f'  {FAIL} {WP_TARGETS} not found.')
        result['error'] = 'no wp-targets.json'
        return result
    try:
        targets = json.loads(WP_TARGETS.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        print(f'  {FAIL} could not parse wp-targets.json: {exc}')
        result['error'] = str(exc)
        return result

    entry = targets.get(WP_TARGET_KEY)
    if not entry:
        print(f'  {FAIL} no {WP_TARGET_KEY!r} entry in wp-targets.json.')
        result['error'] = 'no default entry'
        return result

    base = str(entry.get('url', '')).rstrip('/')
    host = urlparse(base).netloc
    print(f'  Host: {base}')
    result['host'] = base

    auth = (entry.get('username'), entry.get('password'))
    try:
        resp = requests.get(f'{base}/wp-json/wp/v2/media', params={'per_page': 1},
                            auth=auth, timeout=TIMEOUT,
                            headers={'User-Agent': 'Mozilla/5.0 (compatible; AdOps)'})
    except requests.RequestException as exc:
        print(f'  {FAIL} could not reach the media endpoint: {exc}')
        result['error'] = str(exc)
        return result

    if resp.status_code == 401:
        print(f'  {FAIL} authentication rejected -- the app password for {host} looks stale.')
        result['error'] = 'auth rejected'
        return result
    if resp.status_code >= 400:
        print(f'  {FAIL} media endpoint returned HTTP {resp.status_code}.')
        result['error'] = f'HTTP {resp.status_code}'
        return result

    print(f'  {OK} authenticated against wp/v2/media (HTTP {resp.status_code}).')

    # Confirm an existing item is publicly fetchable with no credentials at all --
    # that is the property Instagram depends on.
    items = resp.json() if resp.content else []
    sample = (items[0].get('source_url') if items else None)
    if not sample:
        print(f'  {WARN} media library is empty, so public reachability is unproven.')
        print('        Phase 2 will confirm it on the first real upload.')
        result['usable'] = True
        result['public_url_verified'] = False
        return result

    try:
        anon = requests.get(sample, timeout=TIMEOUT, stream=True,
                            headers={'User-Agent': 'Mozilla/5.0 (compatible; AdOps)'})
        ctype = anon.headers.get('Content-Type', '')
        # Instagram accepts both image and video containers, so either type proves the point:
        # an unauthenticated fetch of a library URL succeeds.
        if anon.status_code < 400 and ctype.startswith(('image/', 'video/')):
            print(f'  {OK} an existing media URL serves publicly with no auth '
                  f'(HTTP {anon.status_code}, {ctype}).')
            result['usable'] = True
            result['public_url_verified'] = True
        else:
            print(f'  {WARN} public fetch returned HTTP {anon.status_code} ({ctype or "no type"}).')
            result['usable'] = False
    except requests.RequestException as exc:
        print(f'  {WARN} public fetch failed: {exc}')
        result['usable'] = False

    print('        Note: this proves the URL is public from THIS network. Definitive proof is '
          'Meta fetching it in Phase 2.')
    return result


# ---------------------------------------------------------------------------- GBP

def check_gbp() -> dict:
    print('\n=== Google Business Profile (informational) ===')
    present = GBP_TOKENS.exists()
    print(f'  {OK if present else MISSING} token file at {GBP_TOKENS}')
    print(f'  {WARN} GBP APIs ship at quota 0 and the AdOps access request is unapproved.')
    print('        The adapter is built as a disabled shell; it cannot publish until Google '
          'grants quota. Nothing we build changes that.')
    return {'platform': 'gbp', 'post_publish': False, 'blocked_upstream': True,
            'tokens_present': present}


# --------------------------------------------------------------------------- main

def main() -> int:
    parser = argparse.ArgumentParser(
        description='Report which social channels the stored credentials can publish to.')
    parser.add_argument('--json', action='store_true', help='emit a machine-readable summary')
    args = parser.parse_args()

    if not _keyring():
        print('ERROR: keyring not installed. Run: pip install keyring')
        return 1

    print('Phase 0 publish-capability check -- read-only, no secrets printed.')
    results = {
        'meta': check_meta(),
        'linkedin': check_linkedin(),
        'x': check_x(),
        'wp_media_host': check_wp_media_host(),
        'gbp': check_gbp(),
    }

    meta, li = results['meta'], results['linkedin']
    verdicts = [
        ('Facebook Page', meta.get('page_publish')),
        ('Instagram', meta.get('ig_publish') and results['wp_media_host'].get('usable')),
        ('LinkedIn page', li.get('post_publish')),
        ('X', results['x'].get('post_publish')),
        ('Google Business Profile', False),
    ]

    print('\n=== Verdict: can we publish today? ===')
    for label, ready in verdicts:
        print(f'  {OK if ready else MISSING} {label}')

    ready_now = [label for label, ready in verdicts if ready]
    print(f'\n{len(ready_now)} of {len(verdicts)} channels publish-ready: '
          f'{", ".join(ready_now) or "none"}')
    if not meta.get('page_publish'):
        print('\nGATE: Facebook Page publishing is the Phase 0 gate. Until pages_manage_posts '
              'is granted\non the AdOps Page, the adapters have nothing to prove themselves '
              'against.')

    if args.json:
        print('\n' + json.dumps(results, indent=2))

    # Non-zero when the gate channel cannot publish.
    return 0 if meta.get('page_publish') else 2


if __name__ == '__main__':
    sys.exit(main())
