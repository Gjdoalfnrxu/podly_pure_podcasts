"""Bounded RSS download for callers that must not hang (e.g. OPML import).

feedparser.parse(url) has no timeout; this fetches with a per-operation socket
timeout, an overall deadline and a size cap, then hands bytes to feedparser.
"""

import time
from dataclasses import dataclass

import feedparser
import requests

MAX_FEED_BYTES = 64 * 1024 * 1024


class FeedFetchTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class FetchedFeed:
    content: bytes
    final_url: str
    headers: dict[str, str]


def fetch_feed_bytes(url: str, timeout: float) -> FetchedFeed:
    deadline = time.monotonic() + timeout
    with requests.get(
        url,
        timeout=(min(10.0, timeout), timeout),
        headers={"User-Agent": feedparser.USER_AGENT},
        stream=True,
        allow_redirects=True,
    ) as resp:
        resp.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        # read1 returns after a single socket read, so the deadline is checked
        # even when a server drips bytes just under the socket read timeout
        # (iter_content would block until a full chunk or EOF).
        while chunk := resp.raw.read1(64 * 1024, decode_content=True):
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_FEED_BYTES:
                raise ValueError(f"Feed larger than {MAX_FEED_BYTES} bytes: {url}")
            if time.monotonic() > deadline:
                raise FeedFetchTimeout(f"Timed out after {timeout:g}s fetching {url}")
        return FetchedFeed(
            content=b"".join(chunks),
            final_url=resp.url,
            headers={k.lower(): v for k, v in resp.headers.items()},
        )
