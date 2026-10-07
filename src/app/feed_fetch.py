"""Bounded RSS download for callers that must not hang (e.g. OPML import).

feedparser.parse(url) has no timeout, and requests' timeouts are per socket
operation: a server can drip header bytes or chain slow redirects forever
without tripping them. Here the whole fetch (DNS, connect, TLS, redirects,
headers, body) runs in a worker thread with a wall-clock limit. On expiry the
caller gets FeedFetchTimeout at once and every socket the fetch opened is
shut down, so the worker unwinds instead of leaking. That covers direct
connections and HTTP(S) proxies; behind a SOCKS proxy the caller is still
bounded but the worker may outlive the limit until the socket times out.
"""

import socket
import threading
from dataclasses import dataclass
from typing import Any

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

MAX_FEED_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 5
# Wall-clock limit for feed fetches that must not hang (OPML import, background
# refreshes).
FEED_FETCH_TIMEOUT_SECONDS = 30.0
MAX_CONNECT_TIMEOUT_SECONDS = 10.0


class FeedFetchTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class FetchedFeed:
    content: bytes
    final_url: str
    headers: dict[str, str]


class _SocketWatch:
    """Holds a dup of every socket one fetch opens so a watchdog can shut them.

    A dup is needed because urllib3 TLS-wraps the plain socket and the wrap
    detaches it (fd -1); shutting down the dup shuts the shared connection,
    including mid-handshake. Dups are closed under the lock once the fetch is
    over, so kill() can never touch a reused fd number.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._dups: list[socket.socket] = []
        self.expired = False
        self._closed = False

    def add(self, sock: socket.socket) -> None:
        with self._lock:
            if self._closed:
                return
            dup = sock.dup()
            self._dups.append(dup)
            if self.expired:
                _shutdown(dup)

    def kill(self) -> None:
        with self._lock:
            self.expired = True
            for dup in self._dups:
                _shutdown(dup)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for dup in self._dups:
                dup.close()
            self._dups.clear()


def _shutdown(sock: socket.socket) -> None:
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass


def _watched_session(watch: _SocketWatch) -> requests.Session:
    class Conn(HTTPConnection):
        def _new_conn(self) -> socket.socket:
            sock = super()._new_conn()
            watch.add(sock)
            return sock

    class TLSConn(HTTPSConnection):
        def _new_conn(self) -> socket.socket:
            sock = super()._new_conn()
            watch.add(sock)
            return sock

    class Pool(HTTPConnectionPool):
        ConnectionCls = Conn

    class TLSPool(HTTPSConnectionPool):
        ConnectionCls = TLSConn

    watched_pools = {"http": Pool, "https": TLSPool}

    class Adapter(HTTPAdapter):
        def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
            super().init_poolmanager(*args, **kwargs)
            self.poolmanager.pool_classes_by_scheme = watched_pools

        def proxy_manager_for(self, proxy: str, **kwargs: Any) -> Any:
            manager = super().proxy_manager_for(proxy, **kwargs)
            # HTTP(S) proxies (env HTTP_PROXY/HTTPS_PROXY) build pools from the
            # same table. SOCKS managers use their own pool classes and are
            # left alone; there only the caller-side time limit applies.
            if not proxy.lower().startswith("socks"):
                manager.pool_classes_by_scheme = watched_pools
            return manager

    session = requests.Session()
    session.max_redirects = MAX_REDIRECTS
    session.mount("http://", Adapter())
    session.mount("https://", Adapter())
    return session


def _download(url: str, timeout: float, watch: _SocketWatch) -> FetchedFeed:
    try:
        return _get(url, timeout, watch)
    finally:
        watch.close()


def _get(url: str, timeout: float, watch: _SocketWatch) -> FetchedFeed:
    with (
        _watched_session(watch) as session,
        session.get(
            url,
            timeout=(_connect_timeout(timeout), timeout),
            headers={"User-Agent": feedparser.USER_AGENT},
            stream=True,
        ) as resp,
    ):
        resp.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        while chunk := resp.raw.read1(64 * 1024, decode_content=True):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FEED_BYTES:
                raise ValueError(f"Feed larger than {MAX_FEED_BYTES} bytes: {url}")
        return FetchedFeed(
            content=b"".join(chunks),
            final_url=resp.url,
            headers={k.lower(): v for k, v in resp.headers.items()},
        )


def _connect_timeout(timeout: float) -> float:
    return min(MAX_CONNECT_TIMEOUT_SECONDS, timeout)


def fetch_feed_bytes(url: str, timeout: float) -> FetchedFeed:
    """Fetch ``url`` with a hard wall-clock limit of ``timeout`` seconds."""
    watch = _SocketWatch()
    outcome: dict[str, Any] = {}

    def work() -> None:
        try:
            outcome["value"] = _download(url, timeout, watch)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            outcome["error"] = exc

    worker = threading.Thread(target=work, daemon=True, name="feed-fetch")
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        watch.kill()
        raise FeedFetchTimeout(f"Timed out after {timeout:g}s fetching {url}")
    if "error" in outcome:
        error = outcome["error"]
        # A socket timeout can fire just before the wall-clock join does;
        # report both the same way, with the limit that actually tripped.
        if isinstance(error, requests.exceptions.ConnectTimeout):
            raise FeedFetchTimeout(
                f"Connect timed out after {_connect_timeout(timeout):g}s fetching {url}"
            ) from error
        if isinstance(error, requests.exceptions.Timeout):
            raise FeedFetchTimeout(
                f"Timed out after {timeout:g}s fetching {url}"
            ) from error
        raise error
    return outcome["value"]
