"""LinkedIn company-page publishing -- blocked on a LinkedIn product approval.

Verified 2026-07-30 by tools/check_publish_scopes.py: the token for app YOUR_LINKEDIN_APP_ID
carries email, openid, profile, r_ads, r_ads_reporting and rw_ads. That is the Advertising
API product only. Organic posting needs w_organization_social, which is gated behind
LinkedIn's separate Community Management API product -- so re-running linkedin_auth.py
will not grant it, no matter what scopes are requested.

Deliberately left as a shell rather than written blind. An adapter that cannot be executed
against the real API is speculation, and LinkedIn's versioned /rest/posts payload has
enough sharp edges (image initializeUpload, the LinkedIn-Version header, author URN
formats) that guessing would likely be wrong.

When the product is approved, the sequence is:
  1. POST /rest/images?action=initializeUpload  -> uploadUrl + image URN
  2. PUT the bytes to uploadUrl
  3. POST /rest/posts with author=urn:li:organization:{id} and the image URN
Use app.sync.linkedin_sync.api_version() for the LinkedIn-Version header so sync and
publish share one pin, and its token plumbing for refresh.
"""
from app.publish.base import PublishError

BLOCKED_REASON = (
    'LinkedIn organic publishing is blocked: app YOUR_LINKEDIN_APP_ID holds only the Advertising '
    'API products (r_ads, rw_ads, r_ads_reporting). w_organization_social requires the '
    'Community Management API product to be approved on the app -- re-running OAuth will '
    'not grant it.')


def publish(channel: dict, post: dict, media: list[dict], dry_run: bool = True) -> dict:
    raise PublishError(BLOCKED_REASON, retryable=False, phase='validate')
