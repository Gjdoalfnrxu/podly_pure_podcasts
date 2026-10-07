"""The refresh-all job must survive hanging and failing feeds.

Live risk: ``start_refresh_all_feeds`` called ``refresh_feed(feed)`` for every
feed in one loop with no fetch timeout, no per-feed error handling, and a DB
session open for the whole loop. One host that never answers wedged the job
forever; with the scheduler's single executor thread and ``max_instances=1``
every later scheduled run was then skipped, so auto-refresh stopped until a
restart. One feed raising aborted the rest of the loop.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time
from collections.abc import Generator, Iterator
from typing import Any
from unittest import mock

import pytest
from flask import Flask
from sqlalchemy.pool import QueuePool

import app.jobs_manager as jobs_manager_module
from app import feed_refresh_all
from app.extensions import db
from app.jobs_manager import JobsManager
from app.models import Feed
from app.routes.feed_routes import feed_bp

_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>OK</title>
<item><title>Ep</title><guid>ep-1</guid></item></channel></rss>"""


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def pool_app(tmp_path) -> Generator[Flask, None, None]:
    app = Flask(__name__)
    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'refresh_all.db'}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SQLALCHEMY_ENGINE_OPTIONS={
            "pool_size": 3,
            "max_overflow": 0,
            "pool_timeout": 2,
        },
    )
    db.init_app(app)
    app.register_blueprint(feed_bp)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


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


@pytest.fixture
def refused_url() -> str:
    """A port nothing listens on: the fetch raises straight away."""
    sock = socket.create_server(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/rss"


def _add_feeds(app: Flask, urls: list[str]) -> list[int]:
    with app.app_context():
        feeds = [Feed(title=f"F{i}", rss_url=url) for i, url in enumerate(urls)]
        db.session.add_all(feeds)
        db.session.commit()
        ids = [feed.id for feed in feeds]
        db.session.remove()
    return ids


def _run_with_deadline(app: Flask, deadline: float) -> tuple[Any, float]:
    """Run refresh-all in a thread so a wedged loop fails the test, not hangs it."""
    outcome: dict[str, Any] = {}

    def work() -> None:
        with app.app_context():
            try:
                outcome["value"] = feed_refresh_all.refresh_all_feeds()
            except BaseException as exc:  # noqa: BLE001 - surfaced below
                outcome["error"] = exc

    start = time.monotonic()
    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(deadline)
    elapsed = time.monotonic() - start
    assert not worker.is_alive(), f"refresh-all still running after {deadline}s"
    if "error" in outcome:
        raise outcome["error"]
    return outcome["value"], elapsed


def test_hanging_and_failing_feeds_do_not_stop_the_others(
    pool_app, tarpit_url, rss_url, refused_url, monkeypatch
):
    fetch_limit = 1.0
    monkeypatch.setattr(feed_refresh_all, "FEED_FETCH_TIMEOUT_SECONDS", fetch_limit)
    hang_id, refused_id, ok1, apply_fails, ok2 = _add_feeds(
        pool_app, [tarpit_url, refused_url, rss_url, rss_url + "?x", rss_url + "?y"]
    )

    applied: list[int] = []

    def apply(feed: Feed, feed_data) -> None:
        assert feed_data.feed.get("title") == "OK"
        if feed.id == apply_fails:
            raise RuntimeError("writer rejected the refresh")
        applied.append(feed.id)

    with mock.patch.object(feed_refresh_all, "apply_feed_refresh", side_effect=apply):
        result, elapsed = _run_with_deadline(pool_app, fetch_limit + 5)

    assert sorted(applied) == sorted([ok1, ok2])
    assert result.total == 5
    assert sorted(result.refreshed) == sorted([ok1, ok2])
    assert sorted(result.failed) == sorted([hang_id, refused_id, apply_fails])
    # The hanging feed is what the loop waited on, and only for its fetch limit.
    assert elapsed >= fetch_limit * 0.9


def test_hanging_hosts_are_fetched_in_parallel(pool_app, tarpit_url, monkeypatch):
    """N hanging hosts cost about one fetch limit, not N of them."""
    fetch_limit = 1.0
    monkeypatch.setattr(feed_refresh_all, "FEED_FETCH_TIMEOUT_SECONDS", fetch_limit)
    n_hanging = feed_refresh_all.REFRESH_ALL_FETCH_WORKERS
    _add_feeds(pool_app, [f"{tarpit_url}?{i}" for i in range(n_hanging)])

    with mock.patch.object(feed_refresh_all, "apply_feed_refresh") as apply:
        result, elapsed = _run_with_deadline(pool_app, fetch_limit * n_hanging + 5)

    apply.assert_not_called()
    assert len(result.failed) == n_hanging
    # Sequential would take n_hanging * fetch_limit (4s); parallel takes ~1s.
    assert elapsed < fetch_limit * 2.5


def test_no_db_connection_is_held_while_fetching(pool_app, monkeypatch):
    with pool_app.app_context():
        pool = db.engine.pool
    assert isinstance(pool, QueuePool)
    n_feeds = feed_refresh_all.REFRESH_ALL_FETCH_WORKERS
    _add_feeds(pool_app, [f"https://ex.example.com/{i}" for i in range(n_feeds)])

    # Every fetch samples the pool, then waits until all of them are in
    # flight: no fetch has returned (so nothing is being applied) while any of
    # them samples.
    all_in_flight = threading.Barrier(n_feeds, timeout=5)
    checked_out: list[int] = []
    lock = threading.Lock()

    def fetch(url: str, *, timeout: float | None = None) -> object:
        assert timeout == feed_refresh_all.FEED_FETCH_TIMEOUT_SECONDS
        with lock:
            checked_out.append(pool.checkedout())
        all_in_flight.wait()
        return mock.sentinel.feed_data

    with (
        mock.patch.object(feed_refresh_all, "fetch_feed", side_effect=fetch),
        mock.patch.object(feed_refresh_all, "apply_feed_refresh") as apply,
    ):
        result, _ = _run_with_deadline(pool_app, 10)

    assert checked_out == [0] * n_feeds
    assert apply.call_count == n_feeds
    assert len(result.refreshed) == n_feeds
    # And the refresh-all thread hands its connection back when it is done.
    assert pool.checkedout() == 0


def test_feed_deleted_during_fetch_is_skipped(pool_app, monkeypatch):
    (feed_id,) = _add_feeds(pool_app, ["https://ex.example.com/gone"])

    def fetch(url: str, *, timeout: float | None = None) -> object:
        with pool_app.app_context():
            db.session.delete(db.session.get(Feed, feed_id))
            db.session.commit()
            db.session.remove()
        return mock.sentinel.feed_data

    with (
        mock.patch.object(feed_refresh_all, "fetch_feed", side_effect=fetch),
        mock.patch.object(feed_refresh_all, "apply_feed_refresh") as apply,
    ):
        result, _ = _run_with_deadline(pool_app, 10)

    apply.assert_not_called()
    assert result.total == 1
    assert result.refreshed == []
    assert result.failed == []


def test_start_refresh_all_feeds_refreshes_then_cleans_up_then_enqueues(
    pool_app, monkeypatch
):
    calls: list[str] = []
    monkeypatch.setattr(
        jobs_manager_module, "_scheduler_app_context", pool_app.app_context
    )
    monkeypatch.setattr(
        jobs_manager_module,
        "refresh_all_feeds",
        lambda: calls.append("refresh"),
    )
    manager = JobsManager.__new__(JobsManager)
    monkeypatch.setattr(
        manager, "_cleanup_inconsistent_posts", lambda: calls.append("cleanup")
    )

    def enqueue(trigger: str, context: dict[str, Any] | None) -> dict[str, Any]:
        calls.append(f"enqueue:{trigger}:{context}")
        return {"status": "ok", "enqueued": 7}

    monkeypatch.setattr(manager, "enqueue_pending_jobs", enqueue)

    result = manager.start_refresh_all_feeds(trigger="manual_refresh", context={"a": 1})

    assert calls == ["refresh", "cleanup", "enqueue:manual_refresh:{'a': 1}"]
    assert result == {"status": "ok", "enqueued": 7}


def test_refresh_all_endpoint_keeps_its_response_shape(pool_app, monkeypatch):
    _add_feeds(pool_app, ["https://ex.example.com/a", "https://ex.example.com/b"])
    manager = mock.Mock()
    manager.start_refresh_all_feeds.return_value = {"status": "ok", "enqueued": 3}
    monkeypatch.setattr("app.routes.feed_routes.get_jobs_manager", lambda: manager)

    resp = pool_app.test_client().post("/api/feeds/refresh-all")

    assert resp.status_code == 200
    assert resp.get_json() == {
        "status": "success",
        "feeds_refreshed": 2,
        "jobs_enqueued": 3,
    }
    manager.start_refresh_all_feeds.assert_called_once_with(trigger="manual_refresh")


def _scheduled_manager(app: Flask, monkeypatch: pytest.MonkeyPatch) -> JobsManager:
    monkeypatch.setattr(jobs_manager_module, "_scheduler_app_context", app.app_context)
    manager = JobsManager.__new__(JobsManager)
    monkeypatch.setattr(manager, "_cleanup_inconsistent_posts", lambda: None)
    monkeypatch.setattr(
        manager,
        "enqueue_pending_jobs",
        lambda trigger, context: {"status": "ok", "enqueued": 0},
    )
    return manager


def test_scheduled_refresh_all_completes_past_hanging_and_failing_feeds(
    pool_app, tarpit_url, rss_url, refused_url, monkeypatch
):
    """End to end through the scheduled entry point and the real feed writer."""
    fetch_limit = 1.0
    monkeypatch.setattr(feed_refresh_all, "FEED_FETCH_TIMEOUT_SECONDS", fetch_limit)
    *_, writer_fails, healthy = _add_feeds(
        pool_app, [tarpit_url, refused_url, rss_url + "?bad", rss_url]
    )
    manager = _scheduled_manager(pool_app, monkeypatch)
    writes: list[int] = []

    def writer_action(name: str, params: dict[str, Any], wait: bool) -> None:
        assert name == "refresh_feed"
        if params["feed_id"] == writer_fails:
            raise RuntimeError("writer rejected the refresh")
        writes.append(params["feed_id"])

    outcome: dict[str, Any] = {}
    worker = threading.Thread(
        target=lambda: outcome.setdefault(
            "value", manager.start_refresh_all_feeds(trigger="scheduled")
        ),
        daemon=True,
    )
    with mock.patch("app.feeds.writer_client.action", side_effect=writer_action):
        worker.start()
        worker.join(fetch_limit + 5)
        assert not worker.is_alive(), "scheduled refresh-all wedged on a hanging host"

    assert outcome["value"] == {"status": "ok", "enqueued": 0}
    assert writes == [healthy]
