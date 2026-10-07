"""fetch_and_store_feed / add_or_refresh_feed / bounded fetch, against the real
feeds.py code with only the network and the writer process faked."""

from __future__ import annotations

import socket
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest
import requests

from app.extensions import db
from app.feed_fetch import (
    MAX_REDIRECTS,
    FeedFetchTimeout,
    FetchedFeed,
    fetch_feed_bytes,
)
from app.feeds import add_or_refresh_feed, fetch_and_store_feed, fetch_feed
from app.models import Feed

RSS = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Show</title><description>d</description>
<image><url>https://cdn.example.com/i.jpg</url></image>
<item><title>Ep 3</title><guid>https://ex.example.com/3</guid>
  <enclosure url="https://cdn.example.com/3.mp3" type="audio/mpeg"/></item>
<item><title>Ep 2</title><guid>https://ex.example.com/2</guid>
  <enclosure url="https://cdn.example.com/2.mp3" type="audio/mpeg"/></item>
<item><title>Ep 1</title><guid>https://ex.example.com/1</guid>
  <enclosure url="https://cdn.example.com/1.mp3" type="audio/mpeg"/></item>
</channel></rss>
"""


def _fake_writer(name: str, params: dict, wait: bool = True):
    if name == "add_feed":
        feed = Feed(**params["feed"])
        db.session.add(feed)
        db.session.commit()
        return SimpleNamespace(success=True, data={"feed_id": feed.id})
    raise AssertionError(f"unexpected writer action {name}")


@pytest.fixture
def store_env():
    """Real fetch_feed/feedparser over canned bytes; writer + refresh faked."""
    final_urls: dict[str, str] = {}

    def fake_bytes(url: str, timeout: float) -> FetchedFeed:
        return FetchedFeed(content=RSS, final_url=final_urls.get(url, url), headers={})

    with (
        mock.patch("app.feeds.fetch_feed_bytes", side_effect=fake_bytes),
        mock.patch("app.feeds.writer_client.action", side_effect=_fake_writer) as w,
        mock.patch("app.feeds.refresh_feed") as refresh,
        mock.patch("app.feeds.config") as cfg,
    ):
        cfg.automatically_whitelist_new_episodes = True
        cfg.number_of_episodes_to_whitelist_from_archive_of_new_feed = None
        yield SimpleNamespace(final_urls=final_urls, writer=w, refresh=refresh)


def _add_feed_payload(writer) -> dict:
    calls = [c for c in writer.call_args_list if c.args[0] == "add_feed"]
    assert len(calls) == 1
    return calls[0].args[1]


@pytest.mark.parametrize("whitelist_archive", [True, False])
def test_add_or_refresh_feed_whitelist_archive_reaches_writer(
    app, store_env, whitelist_archive
):
    real_fetch = fetch_feed
    with (
        app.app_context(),
        # add_or_refresh_feed has no timeout, so route its fetch through the
        # canned-bytes path rather than the network.
        mock.patch(
            "app.feeds.fetch_feed",
            side_effect=lambda url, timeout=None: real_fetch(url, timeout=5),
        ),
    ):
        add_or_refresh_feed(
            "https://ex.example.com/rss", whitelist_archive=whitelist_archive
        )

        posts = _add_feed_payload(store_env.writer)["posts"]
        assert len(posts) == 3
        assert [p["whitelisted"] for p in posts] == [whitelist_archive] * 3


def test_redirected_feed_is_matched_by_final_url(app, store_env):
    store_env.final_urls["https://old.example.com/rss"] = "https://new.example.com/rss"
    with app.app_context():
        first, created = fetch_and_store_feed(
            "https://old.example.com/rss", fetch_timeout=5
        )
        assert created is True
        assert first.rss_url == "https://new.example.com/rss"

        again, created_again = fetch_and_store_feed(
            "https://old.example.com/rss", fetch_timeout=5
        )

        assert created_again is False
        assert again.id == first.id
        store_env.refresh.assert_called_once_with(again, fetch_timeout=5)
        assert Feed.query.count() == 1


def test_fetch_feed_with_timeout_uses_final_url_as_href(app, store_env):
    store_env.final_urls["https://a.example.com/rss"] = "https://b.example.com/rss"
    data = fetch_feed("https://a.example.com/rss", timeout=5)
    assert data.href == "https://b.example.com/rss"
    assert data.feed.title == "Show"
    assert len(data.entries) == 3


# ------------------------------------------------------------ bounded fetch


class _Server:
    """Tiny raw-socket HTTP server; ``handler(conn, base_url, stop)`` per connection.

    Handlers stop by themselves after a few seconds so a regression fails the
    test instead of hanging it."""

    def __init__(self, handler) -> None:
        self.stop = threading.Event()
        self.hits = 0
        self._srv = socket.socket()
        self._srv.bind(("127.0.0.1", 0))
        self._srv.listen(32)
        self.base = f"http://127.0.0.1:{self._srv.getsockname()[1]}"
        self._handler = handler
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            self.hits += 1
            threading.Thread(target=self._one, args=(conn,), daemon=True).start()

    def _one(self, conn: socket.socket) -> None:
        try:
            conn.recv(4096)
            self._handler(conn, self.base, self.stop)
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self._srv.close()


def _drip(conn, prefix: bytes, unit: bytes, stop, every: float = 0.2) -> None:
    conn.sendall(prefix)
    stop_at = time.monotonic() + 6
    while not stop.is_set() and time.monotonic() < stop_at:
        conn.sendall(unit)
        time.sleep(every)  # always under the 1 s socket read timeout


def _assert_times_out_fast(url: str) -> None:
    start = time.monotonic()
    with pytest.raises(FeedFetchTimeout):
        fetch_feed_bytes(url, timeout=1)
    assert time.monotonic() - start < 1.5
    # The watchdog shut the sockets, so the worker thread unwinds (no leak).
    deadline = time.monotonic() + 2
    while any(t.name == "feed-fetch" for t in threading.enumerate()):
        assert time.monotonic() < deadline, "fetch worker thread leaked"
        time.sleep(0.05)


@pytest.mark.parametrize(
    "prefix,unit",
    [
        (b"HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\n\r\n", b"<"),
        (b"HTTP/1.1 200 OK\r\nX-Tarpit: ", b"a"),  # never finishes the headers
    ],
    ids=["body-drip", "header-drip"],
)
def test_fetch_feed_bytes_wall_clock_beats_drip(prefix, unit):
    server = _Server(lambda conn, base, stop: _drip(conn, prefix, unit, stop))
    try:
        _assert_times_out_fast(f"{server.base}/feed.xml")
    finally:
        server.close()


def test_fetch_feed_bytes_wall_clock_beats_slow_redirect_chain():
    def slow_redirect(conn, base, stop):
        stop.wait(0.4)
        conn.sendall(
            f"HTTP/1.1 302 Found\r\nLocation: {base}/next\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )

    server = _Server(slow_redirect)
    try:
        _assert_times_out_fast(f"{server.base}/feed.xml")
    finally:
        server.close()


def test_fetch_feed_bytes_caps_redirects():
    def redirect(conn, base, stop):
        conn.sendall(
            f"HTTP/1.1 302 Found\r\nLocation: {base}/next\r\n"
            "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
        )

    server = _Server(redirect)
    try:
        with pytest.raises(requests.exceptions.TooManyRedirects):
            fetch_feed_bytes(f"{server.base}/feed.xml", timeout=5)
        assert server.hits == MAX_REDIRECTS + 1
    finally:
        server.close()


def test_fetch_feed_bytes_times_out_on_silent_server():
    server = _Server(lambda conn, base, stop: stop.wait(6))
    try:
        _assert_times_out_fast(f"{server.base}/feed.xml")
    finally:
        server.close()
