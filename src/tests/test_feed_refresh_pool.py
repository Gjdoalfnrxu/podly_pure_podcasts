"""Background feed refreshes must not exhaust the DB connection pool.

Live symptom: after a restart a podcast app polling ~37 feeds made
GET /feed/<id> return 500 with "QueuePool limit of size 5 overflow 5 reached".
Each poll started its own refresh thread, and each thread held a pooled
connection for the whole upstream RSS fetch. This reproduces that with a
3-connection pool and refreshes that block until released.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Generator
from unittest import mock

import pytest
from flask import Flask

from app.extensions import db
from app.models import Feed
from app.routes import feed_routes
from app.routes.feed_routes import feed_bp

N_FEEDS = 6


@pytest.fixture
def small_pool_app(tmp_path) -> Generator[Flask, None, None]:
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'pool.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        # Room for the 2 refresh workers plus one request thread, nothing more.
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_size": feed_routes._BACKGROUND_REFRESH_WORKERS + 1,
            "max_overflow": 0,
            "pool_timeout": 2,
        },
    )
    db.init_app(app)
    app.register_blueprint(feed_bp)
    with app.app_context():
        db.create_all()
        for i in range(N_FEEDS):
            db.session.add(Feed(title=f"F{i}", rss_url=f"https://ex.example.com/{i}"))
        db.session.commit()
    with feed_routes._BACKGROUND_REFRESH_LOCK:
        feed_routes._BACKGROUND_REFRESH_LAST_KICKOFF.clear()
        feed_routes._BACKGROUND_REFRESH_PENDING.clear()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


class _BlockingRefresh:
    """Stands in for refresh_feed's slow upstream fetch, called while the
    background thread already holds its pooled connection."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0
        self.done: list[int] = []

    def __call__(self, feed: Feed) -> None:
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            assert self.release.wait(10)
        finally:
            with self.lock:
                self.running -= 1
                self.done.append(feed.id)


def _wait_idle(timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    while True:
        with feed_routes._BACKGROUND_REFRESH_LOCK:
            if not feed_routes._BACKGROUND_REFRESH_PENDING:
                return
        assert time.monotonic() < deadline, "background refreshes never finished"
        time.sleep(0.02)


def test_polling_many_feeds_does_not_exhaust_pool(small_pool_app):
    blocker = _BlockingRefresh()
    client = small_pool_app.test_client()
    with (
        mock.patch("app.routes.feed_routes.refresh_feed", side_effect=blocker),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
    ):
        try:
            statuses = []
            for feed_id in range(1, N_FEEDS + 1):
                start = time.monotonic()
                statuses.append(client.get(f"/feed/{feed_id}").status_code)
                assert time.monotonic() - start < 1.5, "request waited on the pool"
            assert statuses == [200] * N_FEEDS
            assert blocker.max_running <= feed_routes._BACKGROUND_REFRESH_WORKERS
        finally:
            blocker.release.set()
        _wait_idle()

    # Queued, not dropped: every polled feed was refreshed exactly once.
    assert sorted(blocker.done) == list(range(1, N_FEEDS + 1))


def test_refresh_of_already_queued_feed_is_coalesced(small_pool_app):
    blocker = _BlockingRefresh()
    with (
        mock.patch("app.routes.feed_routes.refresh_feed", side_effect=blocker),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        try:
            assert feed_routes._spawn_async_refresh(small_pool_app, 1) is True
            assert feed_routes._spawn_async_refresh(small_pool_app, 1) is False
            # The manual refresh endpoint shares the same bounded queue.
            resp = small_pool_app.test_client().post("/api/feeds/1/refresh")
            assert resp.status_code == 202
        finally:
            blocker.release.set()
        _wait_idle()
    assert blocker.done == [1]
    # Once finished, the feed can be queued again.
    with (
        mock.patch("app.routes.feed_routes.refresh_feed"),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        assert feed_routes._spawn_async_refresh(small_pool_app, 1) is True
        _wait_idle()
