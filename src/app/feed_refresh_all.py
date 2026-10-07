"""Refresh every feed without letting one bad feed stop the rest.

Each upstream fetch is bounded by FEED_FETCH_TIMEOUT_SECONDS and runs with no
DB connection checked out; a feed that hangs, fails to fetch, or fails to
apply is logged and skipped. Fetches run on a small pool because a run is
otherwise sum-of-feeds slow (37 feeds x up to 30s each). Results are applied
one at a time on the calling thread, which holds a connection only while
applying.
"""

import logging
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import feedparser

from app.extensions import db
from app.feed_fetch import FEED_FETCH_TIMEOUT_SECONDS
from app.feeds import apply_feed_refresh, fetch_feed
from app.models import Feed

logger = logging.getLogger("global_logger")

# Worst case for one run is ceil(feeds / workers) * FEED_FETCH_TIMEOUT_SECONDS,
# reached only if every feed hangs.
REFRESH_ALL_FETCH_WORKERS = 4


@dataclass
class RefreshAllResult:
    total: int = 0
    refreshed: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)


def refresh_all_feeds() -> RefreshAllResult:
    """Refresh every feed. Requires an active app context."""
    targets = db.session.query(Feed.id, Feed.rss_url).all()
    # Hand the pooled connection back before the slow, untrusted fetches.
    db.session.remove()

    result = RefreshAllResult(total=len(targets))
    if not targets:
        return result

    with ThreadPoolExecutor(
        max_workers=REFRESH_ALL_FETCH_WORKERS, thread_name_prefix="refresh-all"
    ) as pool:
        futures: dict[Future[feedparser.FeedParserDict], int] = {
            pool.submit(fetch_feed, url, timeout=FEED_FETCH_TIMEOUT_SECONDS): feed_id
            for feed_id, url in targets
        }
        for future in as_completed(futures):
            feed_id = futures[future]
            try:
                if _apply(feed_id, future.result()):
                    result.refreshed.append(feed_id)
            except Exception as exc:  # noqa: BLE001 - one feed must not stop the run
                result.failed.append(feed_id)
                logger.error("Refresh-all: feed %s failed: %s", feed_id, exc)
            finally:
                db.session.remove()

    logger.info(
        "Refresh-all finished: %s feeds, %s refreshed, %s failed",
        result.total,
        len(result.refreshed),
        len(result.failed),
    )
    return result


def _apply(feed_id: int, feed_data: feedparser.FeedParserDict) -> bool:
    feed = db.session.get(Feed, feed_id)
    if feed is None:
        logger.warning("Refresh-all: feed %s was deleted during its refresh", feed_id)
        return False
    apply_feed_refresh(feed, feed_data)
    return True
