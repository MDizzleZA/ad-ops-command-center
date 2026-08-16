"""X (Twitter) publishing -- blocked because no developer app exists yet.

Verified 2026-07-30: Windows Credential Manager holds nothing under 'x:default', and there
is no X app, client id or token anywhere on this machine. Two things are needed before an
adapter can be tested:

  1. An X developer app with OAuth 2.0 (PKCE) and the tweet.write scope.
  2. A tier that permits enough writes. The free tier's monthly post cap is low enough
     that it may not sustain a client posting schedule.

Left as a shell for the same reason as LinkedIn: an adapter with no credentials to run
against cannot be verified.

When credentials exist, the sequence is:
  1. POST https://upload.twitter.com/1.1/media/upload.json  (chunked for video) -> media_id
  2. POST https://api.x.com/2/tweets with {"text": ..., "media": {"media_ids": [...]}}
X accepts raw bytes, so it does not need app/publish/media_host.py.
"""
from app.publish.base import PublishError

BLOCKED_REASON = (
    'X publishing is blocked: no developer app or credentials exist (nothing stored under '
    "Credential Manager target 'x:default'). Create an X app with OAuth 2.0 and the "
    'tweet.write scope, and confirm the tier allows enough monthly writes.')


def publish(channel: dict, post: dict, media: list[dict], dry_run: bool = True) -> dict:
    raise PublishError(BLOCKED_REASON, retryable=False, phase='validate')
