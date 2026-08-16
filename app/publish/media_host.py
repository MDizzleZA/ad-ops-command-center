"""Host a local media file at a public URL, because Instagram will not accept bytes.

The Instagram Graph API's container step takes an `image_url` / `video_url` and Meta's
own servers fetch it. There is no multipart upload path. On a laptop with no public
address that is the hardest constraint in the whole publishing build, and the fix is to
reuse a WordPress media library we already hold credentials for: POST to wp/v2/media,
take the returned `source_url`, hand that to Meta.

Facebook, LinkedIn and X all accept direct byte upload and never come through here.

Media is hosted on the *client's own* site where one exists (the client creative goes to
the client's library, not your team's), falling back to the wp_media_host_domain setting.
Credentials come from app.config.WP_TARGETS, keyed by site slug.
"""
import json
import logging
import mimetypes
from pathlib import Path

import requests

from app import config, db
from app.publish.base import PublishError

log = logging.getLogger('adops.publish.media_host')

# The site sits behind a WAF that answers a default python-requests User-Agent with
# error 1010, so a browser UA is mandatory on every call (not optional politeness).
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) '
      'Chrome/125.0 Safari/537.36')
TIMEOUT = 120

# Instagram's documented container formats. Anything else is rejected before upload
# rather than failing opaquely inside Meta's fetch.
IG_IMAGE_TYPES = {'image/jpeg', 'image/png'}
IG_VIDEO_TYPES = {'video/mp4', 'video/quicktime'}


def _targets() -> dict:
    if not config.WP_TARGETS.exists():
        raise PublishError(f'WordPress targets file missing at {config.WP_TARGETS}',
                           retryable=False, phase='host_media')
    try:
        return json.loads(config.WP_TARGETS.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise PublishError(f'could not read {config.WP_TARGETS}: {exc}',
                           retryable=False, phase='host_media') from exc


def resolve_site(slug: str = None) -> tuple[str, str, tuple[str, str]]:
    """Returns (slug, base_url, (username, app_password)) for the site to host on."""
    slug = slug or db.setting('wp_media_host_domain', 'example')
    entry = _targets().get(slug)
    if not entry:
        raise PublishError(
            f'no WordPress target named {slug!r}. Available: '
            f'{", ".join(sorted(_targets())) or "none"}', retryable=False, phase='host_media')
    base_url = str(entry.get('url', '')).rstrip('/')
    if not base_url:
        raise PublishError(f'WordPress target {slug!r} has no url', retryable=False,
                           phase='host_media')
    return slug, base_url, (entry.get('username'), entry.get('password'))


def guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    return mime or 'application/octet-stream'


def verify_public(url: str) -> tuple[bool, str]:
    """Confirm the URL serves without credentials -- the property Meta depends on.

    Worth doing before every container call: a container built on an unreachable URL
    fails with a generic Meta error that says nothing about the real cause.
    """
    try:
        resp = requests.get(url, timeout=TIMEOUT, stream=True, headers={'User-Agent': UA})
    except requests.RequestException as exc:
        return False, f'fetch failed: {exc}'
    ctype = resp.headers.get('Content-Type', '')
    resp.close()
    if resp.status_code >= 400:
        return False, f'HTTP {resp.status_code}'
    if not ctype.startswith(('image/', 'video/')):
        return False, f'unexpected Content-Type {ctype!r}'
    return True, f'HTTP {resp.status_code}, {ctype}'


def host_media(file_path: str | Path, slug: str = None, for_instagram: bool = True,
               dry_run: bool = True) -> dict:
    """Upload a local file to a WordPress media library and return its public URL.

    Returns {slug, media_id, public_url, mime, bytes, verified, verify_note}.
    """
    path = Path(file_path)
    if not path.exists():
        raise PublishError(f'media file not found: {path}', retryable=False, phase='host_media')

    mime = guess_mime(path)
    size = path.stat().st_size
    if for_instagram and mime not in (IG_IMAGE_TYPES | IG_VIDEO_TYPES):
        raise PublishError(
            f'{path.name} is {mime}, which Instagram will not accept. '
            f'Images must be {sorted(IG_IMAGE_TYPES)}, video {sorted(IG_VIDEO_TYPES)}.',
            retryable=False, phase='host_media')

    site_slug, base_url, auth = resolve_site(slug)

    if dry_run:
        return {'slug': site_slug, 'media_id': None,
                'public_url': f'{base_url}/wp-content/uploads/<dry-run>/{path.name}',
                'mime': mime, 'bytes': size, 'verified': False,
                'verify_note': 'dry run -- nothing uploaded'}

    with path.open('rb') as fh:
        resp = requests.post(
            f'{base_url}/wp-json/wp/v2/media',
            data=fh.read(),
            auth=auth,
            headers={
                'User-Agent': UA,
                'Content-Type': mime,
                'Content-Disposition': f'attachment; filename="{path.name}"',
            },
            timeout=TIMEOUT)

    if resp.status_code == 401:
        raise PublishError(f'WordPress rejected the app password for {site_slug!r}',
                           retryable=False, phase='host_media', response_code=401)
    if resp.status_code >= 400:
        raise PublishError(
            f'WordPress media upload failed [{resp.status_code}]: {resp.text[:300]}',
            retryable=resp.status_code >= 500, phase='host_media',
            response_code=resp.status_code)

    try:
        body = resp.json()
    except ValueError as exc:
        raise PublishError(f'WordPress returned non-JSON on upload: {resp.text[:200]}',
                           retryable=True, phase='host_media') from exc

    public_url = body.get('source_url')
    if not public_url:
        raise PublishError(f'WordPress upload returned no source_url: {str(body)[:300]}',
                           retryable=False, phase='host_media')

    verified, note = verify_public(public_url)
    if not verified:
        raise PublishError(
            f'uploaded to {site_slug} but {public_url} is not publicly fetchable ({note}). '
            'Instagram would fail on this with an unhelpful error.',
            retryable=True, phase='host_media')

    log.info('hosted %s on %s as media %s', path.name, site_slug, body.get('id'))
    return {'slug': site_slug, 'media_id': body.get('id'), 'public_url': public_url,
            'mime': mime, 'bytes': size, 'verified': True, 'verify_note': note}


def unhost(media_id: int, slug: str = None, dry_run: bool = True) -> bool:
    """Delete a hosted media item from the library. Used to tidy up after a failed publish.

    Deliberately not called automatically after a successful post: Instagram re-fetches
    media in some flows, and a deleted source is worse than a stray library entry.

    Important, verified 2026-07-30: this removes the file at origin, but the site sits
    behind Cloudflare and the plain URL keeps serving a cached copy afterwards -- a
    request with a cache-busting query string 404s while the bare URL still returns 200.
    So unhost() is housekeeping, NOT erasure. Do not rely on it to make a client creative
    non-public; purge the CDN cache for that. It also means verify_public() can report a
    deleted file as still reachable.
    """
    site_slug, base_url, auth = resolve_site(slug)
    if dry_run:
        log.info('dry run: would delete media %s from %s', media_id, site_slug)
        return False
    resp = requests.delete(f'{base_url}/wp-json/wp/v2/media/{media_id}',
                           params={'force': 'true'}, auth=auth,
                           headers={'User-Agent': UA}, timeout=TIMEOUT)
    if resp.status_code >= 400:
        log.warning('could not delete media %s from %s [%s]', media_id, site_slug,
                    resp.status_code)
        return False
    return True
