"""Publishing adapters: the distribution half of ad-ops.

app/sync/ pulls measurement in; app/publish/ pushes content out. Every platform
module here exposes the same signature (see base.publish for the contract), and
dry_run defaults to True everywhere so a mistake costs a log line, not a live post.
"""
