"""Shared "subscribe the current user to an RSS URL" path.

Used by both ``POST /feed`` (single add) and ``POST /api/feeds/import-opml``
so the two cannot drift.
"""

import logging
from threading import Thread
from typing import Any, cast

import validators
from flask import Flask, current_app
from flask.typing import ResponseReturnValue

from app.auth import is_auth_enabled
from app.feeds import add_or_refresh_feed
from app.jobs_manager import get_jobs_manager
from app.models import Feed, User, UserFeed
from app.routes.feed_utils import (
    check_feed_allowance,
    ensure_user_feed_membership,
    whitelist_latest_for_first_member,
)

logger = logging.getLogger("global_logger")


class InvalidFeedUrlError(ValueError):
    pass


class FeedAllowanceError(Exception):
    """The user's plan is full; ``response`` is the 402 the single-add route returns."""

    def __init__(self, response: ResponseReturnValue) -> None:
        self.response = response
        message = "Feed allowance reached"
        body = response[0] if isinstance(response, tuple) else response
        payload = getattr(body, "get_json", lambda: None)()
        if isinstance(payload, dict) and payload.get("message"):
            message = str(payload["message"])
        super().__init__(message)


def is_subscribed(url: str, user: User | None) -> bool:
    """True if ``url`` is already a feed for ``user`` (or exists at all, no-auth)."""
    feed = Feed.query.filter_by(rss_url=url).first()
    if feed is None:
        return False
    if user is None:
        return True
    return (
        UserFeed.query.filter_by(feed_id=feed.id, user_id=user.id).first() is not None
    )


def subscribe_to_feed(
    url: str, user: User | None, *, process_latest: bool = True
) -> Feed:
    """Add/refresh ``url`` and link it to ``user``.

    ``url`` must already be normalised with ``fix_url``. With
    ``process_latest=False`` nothing is queued: a feed created by this call
    stores its backlog un-whitelisted (so the writer creates no jobs) and the
    latest episode is not whitelisted. Episodes released later still follow
    the normal auto-whitelist settings. Feeds that already existed (shared
    with other users) are refreshed exactly as a normal add would.

    Raises InvalidFeedUrlError, FeedAllowanceError, or whatever the
    underlying fetch/writer raises.
    """
    if not validators.url(url):
        raise InvalidFeedUrlError("Invalid URL")

    if user:
        allowance_error = check_feed_allowance(user, url)
        if allowance_error:
            raise FeedAllowanceError(allowance_error)

    feed = add_or_refresh_feed(url, whitelist_archive=process_latest)

    if not process_latest:
        if user:
            ensure_user_feed_membership(feed, user.id)
        return feed

    if user:
        created, previous_count = ensure_user_feed_membership(feed, user.id)
        if created and previous_count == 0:
            whitelist_latest_for_first_member(feed, getattr(user, "id", None))
    elif not is_auth_enabled():
        # In no-auth mode, if this feed has no members, trigger whitelisting for the latest post.
        if UserFeed.query.filter_by(feed_id=feed.id).count() == 0:
            whitelist_latest_for_first_member(feed, None)
    return feed


def _enqueue_pending_jobs_async(app: Flask) -> None:
    with app.app_context():
        try:
            get_jobs_manager().enqueue_pending_jobs(trigger="feed_refresh")
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to enqueue pending jobs asynchronously: %s", exc)


def start_enqueue_pending_jobs() -> None:
    app = cast(Any, current_app)._get_current_object()
    Thread(
        target=_enqueue_pending_jobs_async,
        args=(app,),
        daemon=True,
        name="enqueue-jobs-after-add",
    ).start()
