"""Background feed refreshes must not exhaust the DB connection pool.

Live symptom: after a restart a podcast app polling ~37 feeds made
GET /feed/<id> return 500 with "QueuePool limit of size 5 overflow 5 reached".
Each poll started its own refresh thread, and each thread held a pooled
connection for the whole upstream RSS fetch. This reproduces that with a
3-connection pool and upstream fetches that block until released.

The bounded pool brings its own hazard: hosts that never answer would pin
every worker. Background fetches therefore run with a wall-clock limit and
with no DB connection checked out.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
from collections.abc import Generator, Iterator
from unittest import mock

import pytest
from flask import Flask
from sqlalchemy.pool import QueuePool

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
    """Stands in for the slow upstream fetch (``fetch_feed``) of a refresh."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0
        self.done: list[int] = []

    def __call__(self, url: str, *, timeout: float | None = None) -> None:
        feed_id = int(url.rsplit("/", 1)[1]) + 1
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
        try:
            assert self.release.wait(10)
        finally:
            with self.lock:
                self.running -= 1
                self.done.append(feed_id)


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
        mock.patch("app.routes.feed_routes.fetch_feed", side_effect=blocker),
        mock.patch("app.routes.feed_routes.apply_feed_refresh"),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
        mock.patch("app.routes.feed_routes.generate_feed_xml", return_value=b"<rss/>"),
    ):
        try:
            statuses = []
            for feed_id in range(1, N_FEEDS + 1):
                start = time.monotonic()
                statuses.append(client.get(f"/feed/{feed_id}").status_code)
                assert time.monotonic() - start < 1.5, "request waited on the pool"
                # Like a real client between polls: let the refresh it kicked off
                # start and take its connection before the next request.
                time.sleep(0.3)
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
        mock.patch("app.routes.feed_routes.fetch_feed", side_effect=blocker),
        mock.patch("app.routes.feed_routes.apply_feed_refresh"),
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
        mock.patch("app.routes.feed_routes.fetch_feed"),
        mock.patch("app.routes.feed_routes.apply_feed_refresh"),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        assert feed_routes._spawn_async_refresh(small_pool_app, 1) is True
        _wait_idle()


def test_upstream_fetch_runs_without_a_db_connection(small_pool_app):
    blocker = _BlockingRefresh()
    with small_pool_app.app_context():
        pool = db.engine.pool
    assert isinstance(pool, QueuePool)
    with (
        mock.patch("app.routes.feed_routes.fetch_feed", side_effect=blocker),
        mock.patch("app.routes.feed_routes.apply_feed_refresh") as apply,
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        try:
            feed_routes._spawn_async_refresh(small_pool_app, 1)
            deadline = time.monotonic() + 5
            while blocker.running == 0:
                assert time.monotonic() < deadline, "refresh never started"
                time.sleep(0.01)
            assert pool.checkedout() == 0
        finally:
            blocker.release.set()
        _wait_idle()
    # The feed is re-read after the fetch and the result written to it.
    (feed, _data), _ = apply.call_args
    assert feed.id == 1


@pytest.fixture
def tarpit_url() -> Iterator[str]:
    """A server that accepts connections and never sends a byte."""
    listener = socket.create_server(("127.0.0.1", 0))
    conns: list[socket.socket] = []
    stop = threading.Event()

    def accept() -> None:
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                conns.append(listener.accept()[0])
            except TimeoutError:
                continue
            except OSError:
                return

    thread = threading.Thread(target=accept, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{listener.getsockname()[1]}/rss"
    stop.set()
    thread.join()
    listener.close()
    # Unblocks any fetch still waiting on us (e.g. one with no timeout).
    for conn in conns:
        conn.close()


_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>OK</title>
<item><title>Ep</title><guid>ep-1</guid></item></channel></rss>"""


@pytest.fixture
def rss_url() -> Iterator[str]:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(_RSS)))
            self.end_headers()
            self.wfile.write(_RSS)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/rss"
    server.shutdown()
    server.server_close()


def test_hanging_hosts_do_not_starve_other_refreshes(
    small_pool_app, tarpit_url, rss_url, monkeypatch
):
    """Two never-answering feeds take both workers; a healthy feed queued
    behind them must still refresh once their fetches time out."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    fetch_limit = 1.0
    monkeypatch.setattr(feed_routes, "FEED_FETCH_TIMEOUT_SECONDS", fetch_limit)
    with small_pool_app.app_context():
        for feed_id, url in ((1, tarpit_url), (2, tarpit_url + "?b"), (3, rss_url)):
            feed = db.session.get(Feed, feed_id)
            assert feed is not None
            feed.rss_url = url
        db.session.commit()

    refreshed = threading.Event()
    applied: list[tuple[int, str]] = []

    def apply(feed: Feed, feed_data) -> None:
        applied.append((feed.id, feed_data.feed.get("title")))
        refreshed.set()

    with (
        mock.patch("app.routes.feed_routes.apply_feed_refresh", side_effect=apply),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        try:
            start = time.monotonic()
            for feed_id in (1, 2, 3):
                assert feed_routes._spawn_async_refresh(small_pool_app, feed_id)
            # Workers free up after fetch_limit; allow generous scheduling slack.
            assert refreshed.wait(fetch_limit + 4), (
                "healthy feed never refreshed: hanging hosts pinned every worker"
            )
            assert time.monotonic() - start >= fetch_limit * 0.9
        finally:
            _wait_idle(timeout=fetch_limit + 10)

    # The hanging feeds failed and were not written; the healthy one was.
    assert applied == [(3, "OK")]
    # And they were released from the queue, so they can be retried.
    with (
        mock.patch("app.routes.feed_routes.fetch_feed"),
        mock.patch("app.routes.feed_routes.apply_feed_refresh"),
        mock.patch("app.routes.feed_routes.get_jobs_manager"),
    ):
        assert feed_routes._spawn_async_refresh(small_pool_app, 1) is True
        _wait_idle()
