"""Subscriber-facing feed URLs (what "Copy protected feed" hands out)."""

from dataclasses import dataclass
from urllib.parse import urlencode

from app.feeds import _get_base_url
from app.writer.client import writer_client


@dataclass(frozen=True)
class ProtectedFeedUrl:
    url: str
    token_id: str
    secret: str


def protected_feed_url(user_id: int, feed_id: int) -> ProtectedFeedUrl:
    """Return the user's tokenised URL for a feed.

    The writer reuses the user's existing non-revoked token for the feed, so
    repeated calls (copy button, every OPML export) return the same URL rather
    than minting a new token each time. Needs a request context for the base URL.
    """
    result = writer_client.action(
        "create_feed_access_token",
        {"user_id": user_id, "feed_id": feed_id},
        wait=True,
    )
    if not result or not result.success or not isinstance(result.data, dict):
        raise RuntimeError("Failed to create feed token")
    token_id = str(result.data["token_id"])
    secret = str(result.data["secret"])
    query = urlencode({"feed_token": token_id, "feed_secret": secret})
    return ProtectedFeedUrl(
        url=f"{_get_base_url()}/feed/{feed_id}?{query}",
        token_id=token_id,
        secret=secret,
    )


def public_feed_url(feed_id: int) -> str:
    """Feed URL when auth is disabled (no token needed)."""
    return f"{_get_base_url()}/feed/{feed_id}"
