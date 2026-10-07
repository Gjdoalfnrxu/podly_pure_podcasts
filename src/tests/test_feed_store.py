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
from app.feed_fetch import FeedFetchTimeout, FetchedFeed, fetch_feed_bytes
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


def _serve_once(handler) -> tuple[str, threading.Event]:
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    done = threading.Event()

    def run() -> None:
        conn, _ = srv.accept()
        try:
            conn.recv(4096)
            handler(conn, done)
        finally:
            conn.close()
            srv.close()

    threading.Thread(target=run, daemon=True).start()
    return f"http://127.0.0.1:{srv.getsockname()[1]}/feed.xml", done


def test_fetch_feed_bytes_times_out_on_silent_server():
    url, done = _serve_once(lambda conn, done: done.wait(10))
    start = time.monotonic()
    with pytest.raises((requests.exceptions.Timeout, FeedFetchTimeout)):
        fetch_feed_bytes(url, timeout=1)
    done.set()
    assert time.monotonic() - start < 4


def test_fetch_feed_bytes_deadline_beats_slow_drip():
    def drip(conn, done):
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/rss+xml\r\n\r\n")
        stop_at = time.monotonic() + 6  # bounded so a regression fails, not hangs
        while not done.is_set() and time.monotonic() < stop_at:
            try:
                conn.sendall(b"<")
            except OSError:
                return
            time.sleep(0.2)  # always under the 1 s socket read timeout

    url, done = _serve_once(drip)
    start = time.monotonic()
    with pytest.raises(FeedFetchTimeout):
        fetch_feed_bytes(url, timeout=1)
    done.set()
    assert time.monotonic() - start < 4
