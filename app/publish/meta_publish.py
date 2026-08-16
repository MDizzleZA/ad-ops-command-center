"""Facebook Page and Instagram publishing via the Meta Graph API.

Both channels ride the same app credentials already in Windows Credential Manager under
'adops-meta-ads' (the store app/sync/meta_sync.py and the meta-ads skill read). The
stored user token carries pages_manage_posts and instagram_content_publish and does not
expire, so no App Review step stands between this code and a live post.

The two platforms differ in one way that shapes everything here:

  Facebook  accepts raw bytes. Photos go up as multipart and that is the whole story.
  Instagram accepts no bytes at all. You create a *container* pointing at a public URL,
            Meta fetches it, you poll until the container is FINISHED, then publish it.
            That is why app/publish/media_host.py exists.

Page access tokens are fetched live from /me/accounts rather than stored, so there is one
fewer secret at rest. They are never logged or returned.
"""
import logging
import time

import requests

from app import db
from app.publish import media_host
from app.publish.base import PublishError

log = logging.getLogger('adops.publish.meta')

SERVICE = 'adops-meta-ads'
DEFAULT_GRAPH_VERSION = 'v21.0'
TIMEOUT = 180

# Facebook's own limit is 63206 characters; Instagram rejects captions over 2200.
FB_CAPTION_MAX = 63206
IG_CAPTION_MAX = 2200
IG_HASHTAG_MAX = 30
IG_CAROUSEL_MAX = 10

# Meta error codes worth retrying. Everything else is treated as terminal so a bad
# caption or a missing scope does not burn quota re-failing.
RETRYABLE_CODES = {
    1,    # unknown transient API error
    2,    # temporary service failure
    4,    # application request limit reached
    17,   # user request limit reached
    32,   # page request limit reached
    341,  # temporarily blocked for policing
    613,  # rate limit
}
# Instagram container states, per the Content Publishing API.
IG_TERMINAL_ERROR_STATES = {'ERROR', 'EXPIRED'}


def _graph() -> str:
    version = db.setting('meta_graph_version', DEFAULT_GRAPH_VERSION) or DEFAULT_GRAPH_VERSION
    return f'https://graph.facebook.com/{version}'


def _user_token() -> str:
    import keyring
    token = keyring.get_password(SERVICE, 'access_token')
    if not token:
        raise PublishError(
            f'no Meta access token in Credential Manager under {SERVICE!r}. '
            'Run tools/reauth_meta.py.', retryable=False, phase='validate')
    return token


def _raise_for_meta_error(resp: requests.Response, phase: str) -> dict:
    """Return the parsed body, or raise a PublishError tagged with retryability."""
    try:
        body = resp.json()
    except ValueError:
        raise PublishError(f'Meta returned non-JSON [{resp.status_code}]: {resp.text[:200]}',
                           retryable=resp.status_code >= 500, phase=phase,
                           response_code=resp.status_code) from None
    error = body.get('error')
    if error:
        code = error.get('code')
        raise PublishError(
            f'Meta error {code}/{error.get("error_subcode", "-")}: '
            f'{error.get("message", "no message")}',
            retryable=code in RETRYABLE_CODES, phase=phase, response_code=resp.status_code)
    if resp.status_code >= 400:
        raise PublishError(f'Meta HTTP {resp.status_code}: {resp.text[:200]}',
                           retryable=resp.status_code >= 500, phase=phase,
                           response_code=resp.status_code)
    return body


def _page_token(page_id: str) -> str:
    """Fetch the Page access token for page_id. Never logged, never persisted."""
    resp = requests.get(f'{_graph()}/me/accounts', params={
        'access_token': _user_token(), 'fields': 'id,name,access_token', 'limit': 100,
    }, timeout=TIMEOUT)
    body = _raise_for_meta_error(resp, 'validate')
    for page in body.get('data') or []:
        if str(page.get('id')) == str(page_id):
            token = page.get('access_token')
            if not token:
                raise PublishError(
                    f'Page {page_id} is visible but returned no Page access token; '
                    'pages_manage_posts may not be granted for it.',
                    retryable=False, phase='validate')
            return token
    visible = ', '.join(f'{p.get("name")} ({p.get("id")})' for p in (body.get('data') or []))
    raise PublishError(
        f'Page {page_id} is not administered by this token. Visible Pages: {visible or "none"}',
        retryable=False, phase='validate')


def _resolve_media(media: list[dict]) -> list[dict]:
    """Fill in file_path from generated_assets where an item references an asset id."""
    resolved = []
    for item in sorted(media, key=lambda m: m.get('position', 0)):
        entry = dict(item)
        if not entry.get('file_path') and entry.get('asset_id'):
            row = db.row('SELECT file_path FROM generated_assets WHERE id = ?',
                         (entry['asset_id'],))
            if not row or not row['file_path']:
                raise PublishError(f'generated_asset {entry["asset_id"]} has no file_path',
                                   retryable=False, phase='validate')
            entry['file_path'] = row['file_path']
        if not entry.get('file_path') and not entry.get('public_url'):
            raise PublishError('media item has neither file_path, public_url nor asset_id',
                               retryable=False, phase='validate')
        resolved.append(entry)
    return resolved


def _validate_caption(caption: str, platform: str) -> None:
    caption = caption or ''
    limit = IG_CAPTION_MAX if platform == 'instagram' else FB_CAPTION_MAX
    if len(caption) > limit:
        raise PublishError(
            f'caption is {len(caption)} characters; {platform} allows {limit}',
            retryable=False, phase='validate')
    if platform == 'instagram' and caption.count('#') > IG_HASHTAG_MAX:
        raise PublishError(
            f'caption has {caption.count("#")} hashtags; Instagram allows {IG_HASHTAG_MAX}',
            retryable=False, phase='validate')


# ------------------------------------------------------------------- Facebook Page

def _publish_facebook(channel: dict, caption: str, media: list[dict], dry_run: bool) -> dict:
    page_id = channel['external_id']
    _validate_caption(caption, 'facebook')

    plan = ('feed (text only)' if not media else
            'photos (single image)' if len(media) == 1 else
            f'photos x{len(media)} unpublished, then feed with attached_media')
    if dry_run:
        return {'ok': True, 'external_post_id': None, 'permalink': None, 'error': None,
                'retryable': False,
                'detail': {'platform': 'facebook', 'page_id': page_id, 'plan': plan,
                           'caption_chars': len(caption or ''), 'media_count': len(media)}}

    token = _page_token(page_id)

    if not media:
        body = _raise_for_meta_error(requests.post(
            f'{_graph()}/{page_id}/feed',
            data={'message': caption or '', 'access_token': token},
            timeout=TIMEOUT), 'publish')
        post_id = body.get('id')
    elif len(media) == 1:
        with open(media[0]['file_path'], 'rb') as fh:
            body = _raise_for_meta_error(requests.post(
                f'{_graph()}/{page_id}/photos',
                data={'message': caption or '', 'access_token': token},
                files={'source': fh}, timeout=TIMEOUT), 'publish')
        # /photos returns the photo id plus the resulting feed post id.
        post_id = body.get('post_id') or body.get('id')
    else:
        fbids = []
        for item in media:
            with open(item['file_path'], 'rb') as fh:
                child = _raise_for_meta_error(requests.post(
                    f'{_graph()}/{page_id}/photos',
                    data={'published': 'false', 'access_token': token},
                    files={'source': fh}, timeout=TIMEOUT), 'publish')
            fbids.append(child['id'])
        payload = {'message': caption or '', 'access_token': token}
        for index, fbid in enumerate(fbids):
            payload[f'attached_media[{index}]'] = f'{{"media_fbid":"{fbid}"}}'
        body = _raise_for_meta_error(requests.post(
            f'{_graph()}/{page_id}/feed', data=payload, timeout=TIMEOUT), 'publish')
        post_id = body.get('id')

    if not post_id:
        raise PublishError(f'Facebook accepted the request but returned no post id: {body}',
                           retryable=False, phase='publish')
    return {'ok': True, 'external_post_id': post_id,
            'permalink': f'https://www.facebook.com/{post_id}', 'error': None,
            'retryable': False,
            'detail': {'platform': 'facebook', 'page_id': page_id, 'plan': plan}}


# ---------------------------------------------------------------------- Instagram

def _ig_wait_for_container(creation_id: str, token: str, timeout_seconds: int = 300) -> None:
    """Poll a container until FINISHED. Meta fetches the media during this window."""
    deadline = time.time() + timeout_seconds
    delay = 2
    last = None
    while time.time() < deadline:
        body = _raise_for_meta_error(requests.get(
            f'{_graph()}/{creation_id}',
            params={'fields': 'status_code,status', 'access_token': token},
            timeout=TIMEOUT), 'container')
        last = body.get('status_code')
        if last == 'FINISHED':
            return
        if last in IG_TERMINAL_ERROR_STATES:
            raise PublishError(
                f'Instagram container {creation_id} ended in {last}: '
                f'{body.get("status", "no detail")}. Most often the media URL was not '
                'reachable or the image failed Instagram\'s format rules.',
                retryable=False, phase='container')
        time.sleep(delay)
        delay = min(delay * 1.5, 15)
    raise PublishError(
        f'Instagram container {creation_id} still {last} after {timeout_seconds}s',
        retryable=True, phase='container')


def _ig_container(ig_id: str, token: str, params: dict) -> str:
    body = _raise_for_meta_error(requests.post(
        f'{_graph()}/{ig_id}/media', data={**params, 'access_token': token},
        timeout=TIMEOUT), 'container')
    creation_id = body.get('id')
    if not creation_id:
        raise PublishError(f'Instagram returned no container id: {body}', retryable=False,
                           phase='container')
    return creation_id


def _publish_instagram(channel: dict, caption: str, media: list[dict], dry_run: bool,
                       client_id: int = None) -> dict:
    ig_id = channel['external_id']
    page_id = channel.get('parent_external_id')
    _validate_caption(caption, 'instagram')

    if not media:
        raise PublishError('Instagram requires at least one image or video',
                           retryable=False, phase='validate')
    if len(media) > IG_CAROUSEL_MAX:
        raise PublishError(f'Instagram carousels hold at most {IG_CAROUSEL_MAX} items; '
                           f'got {len(media)}', retryable=False, phase='validate')
    if not page_id:
        raise PublishError(
            f'Instagram channel {ig_id} has no parent_external_id. The linked Facebook '
            'Page id is needed to obtain a Page token.', retryable=False, phase='validate')

    host_slug = (db.jloads(channel.get('config_json'), {}) or {}).get('media_host_slug')

    if dry_run:
        # Still exercise the media checks -- format rejection is the common failure.
        for item in media:
            if not item.get('public_url'):
                media_host.host_media(item['file_path'], slug=host_slug, dry_run=True)
        return {'ok': True, 'external_post_id': None, 'permalink': None, 'error': None,
                'retryable': False,
                'detail': {'platform': 'instagram', 'ig_user_id': ig_id, 'page_id': page_id,
                           'plan': 'carousel' if len(media) > 1 else 'single',
                           'caption_chars': len(caption or ''), 'media_count': len(media),
                           'media_host_slug': host_slug or db.setting('wp_media_host_domain')}}

    token = _page_token(page_id)
    hosted: list[dict] = []

    # Every item needs a URL Meta can fetch. Items that already carry one are reused.
    for item in media:
        if item.get('public_url'):
            ok, note = media_host.verify_public(item['public_url'])
            if not ok:
                raise PublishError(
                    f'{item["public_url"]} is not publicly fetchable ({note}); Instagram '
                    'would fail on it with an unhelpful error', retryable=False,
                    phase='host_media')
            hosted.append({'public_url': item['public_url'], 'media_id': None})
        else:
            hosted.append(media_host.host_media(item['file_path'], slug=host_slug,
                                                dry_run=False))

    def _url_field(url: str) -> dict:
        return ({'video_url': url, 'media_type': 'REELS'} if url.lower().endswith(('.mp4', '.mov'))
                else {'image_url': url})

    if len(hosted) == 1:
        creation_id = _ig_container(ig_id, token,
                                   {**_url_field(hosted[0]['public_url']),
                                    'caption': caption or ''})
        _ig_wait_for_container(creation_id, token)
    else:
        child_ids = []
        for entry in hosted:
            child = _ig_container(ig_id, token, {**_url_field(entry['public_url']),
                                                 'is_carousel_item': 'true'})
            _ig_wait_for_container(child, token)
            child_ids.append(child)
        creation_id = _ig_container(ig_id, token, {
            'media_type': 'CAROUSEL', 'children': ','.join(child_ids),
            'caption': caption or ''})
        _ig_wait_for_container(creation_id, token)

    body = _raise_for_meta_error(requests.post(
        f'{_graph()}/{ig_id}/media_publish',
        data={'creation_id': creation_id, 'access_token': token},
        timeout=TIMEOUT), 'publish')
    ig_media_id = body.get('id')
    if not ig_media_id:
        raise PublishError(f'Instagram publish returned no media id: {body}',
                           retryable=False, phase='publish')

    permalink = None
    try:
        info = _raise_for_meta_error(requests.get(
            f'{_graph()}/{ig_media_id}', params={'fields': 'permalink', 'access_token': token},
            timeout=TIMEOUT), 'publish')
        permalink = info.get('permalink')
    except PublishError:
        pass  # the post is live; a missing permalink is cosmetic

    return {'ok': True, 'external_post_id': ig_media_id, 'permalink': permalink, 'error': None,
            'retryable': False,
            'detail': {'platform': 'instagram', 'ig_user_id': ig_id,
                       'hosted_media': [h.get('media_id') for h in hosted],
                       'plan': 'carousel' if len(hosted) > 1 else 'single'}}


# ------------------------------------------------------------------------ contract

def publish(channel: dict, post: dict, media: list[dict], dry_run: bool = True) -> dict:
    """Publish one post to one Meta channel. See app/publish/base.py for the contract."""
    platform = channel['platform']
    if platform not in ('facebook', 'instagram'):
        raise PublishError(f'meta_publish does not handle platform {platform!r}',
                           retryable=False, phase='validate')

    caption = post.get('caption') or ''
    resolved = _resolve_media(media)

    if platform == 'facebook':
        return _publish_facebook(channel, caption, resolved, dry_run)
    return _publish_instagram(channel, caption, resolved, dry_run,
                              client_id=post.get('client_id'))
