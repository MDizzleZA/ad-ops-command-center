"""Google Business Profile publishing -- a conforming shell, blocked upstream by Google.

Not unimplemented for lack of effort: the Business Profile APIs ship at a default quota
of zero and your team's access request is still unapproved, which is why app/sync/gbp_sync.py
already fails cleanly today. Writing the real call sequence now would produce code that
cannot be run or tested, so the adapter exists only to satisfy the registry and to fail
with an explanation instead of an ImportError.

When quota is granted, the flow is localPosts.create against
mybusiness.googleapis.com/v4/accounts/{account}/locations/{location}/localPosts, with
OAuth user tokens from app.config.GBP_TOKENS.
"""
from app.publish.base import PublishError

BLOCKED_REASON = (
    'Google Business Profile publishing is blocked upstream: the Business Profile APIs '
    'ship at quota 0 and the AdOps access request is unapproved. Nothing in ad-ops can '
    'change this -- Google has to grant quota first.')


def publish(channel: dict, post: dict, media: list[dict], dry_run: bool = True) -> dict:
    raise PublishError(BLOCKED_REASON, retryable=False, phase='validate')
