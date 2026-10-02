"""RSS fetch: one recent episode per show, plus publisher markers / DAI tags."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any

import feedparser
import requests

from podcast_processor.experiments.auto_gold.constants import DAI_HOST_MARKERS
from podcast_processor.experiments.auto_gold.types import (
    EpisodeRef,
    PublisherMarker,
    ShowSpec,
)
from podcast_processor.podcast_downloader import find_audio_link

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 30
TRAILER_TYPES = frozenset({"trailer"})
MIN_EPISODE_SECONDS = 300.0

GetFn = Callable[..., Any]


def enclosure_is_dai(url: str | None) -> bool:
    if not url:
        return False
    lowered = url.lower()
    return any(marker in lowered for marker in DAI_HOST_MARKERS)


def parse_clock_time(raw: str) -> float | None:
    text = str(raw).strip()
    if not text:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2])
        hours = int(parts[-3]) if len(parts) == 3 else 0
    except ValueError:
        return None
    return hours * 3600.0 + minutes * 60.0 + seconds


def _local_tag(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_publisher_markers_xml(xml_bytes: bytes) -> list[PublisherMarker]:
    """Podlove Simple Chapters and similar. Positives decided later by title."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    markers: list[PublisherMarker] = []
    chapters: list[tuple[float, str]] = []
    for elem in root.iter():
        if _local_tag(elem.tag) != "chapter":
            continue
        start_raw = (
            elem.attrib.get("start")
            or elem.attrib.get("startTime")
            or elem.attrib.get("starttime")
        )
        title = elem.attrib.get("title") or elem.attrib.get("name") or (elem.text or "")
        start = parse_clock_time(start_raw or "")
        if start is None:
            continue
        chapters.append((start, str(title).strip()))
    chapters.sort(key=lambda row: row[0])
    for idx, (start, title) in enumerate(chapters):
        end = chapters[idx + 1][0] if idx + 1 < len(chapters) else None
        markers.append(
            PublisherMarker(start=start, end=end, title=title, source="psc_chapter")
        )
    return markers


def itunes_duration(entry: Any) -> float | None:
    for attr in ("itunes_duration", "itunes_duration_seconds"):
        value = getattr(entry, attr, None)
        parsed = parse_clock_time(str(value)) if value is not None else None
        if parsed is not None:
            return parsed
    if isinstance(entry, dict):
        value = entry.get("itunes_duration")
        parsed = parse_clock_time(str(value)) if value is not None else None
        if parsed is not None:
            return parsed
    return None


def entry_is_trailer(entry: Any) -> bool:
    episode_type = (
        str(
            getattr(entry, "itunes_episodetype", "")
            or getattr(entry, "itunes_episode_type", "")
            or ""
        )
        .strip()
        .lower()
    )
    return episode_type in TRAILER_TYPES


def pick_latest_audio_entry(parsed: Any) -> Any | None:
    """Most recent real episode: skip trailers and sub-5-minute feed notes."""
    entries = list(getattr(parsed, "entries", None) or [])
    fallback: Any | None = None
    for entry in entries:
        if entry_is_trailer(entry):
            continue
        audio = find_audio_link(entry)
        if not (audio and str(audio).startswith("http")):
            continue
        if fallback is None:
            fallback = entry
        duration = itunes_duration(entry)
        if duration is not None and duration < MIN_EPISODE_SECONDS:
            continue
        return entry
    return fallback


def fetch_rss(
    show: ShowSpec,
    *,
    get: GetFn | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> tuple[bytes, Any]:
    session_get = get or requests.get
    response = session_get(
        show.rss_url,
        timeout=timeout,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    content = response.content
    return content, feedparser.parse(content)


def episode_from_rss(
    show: ShowSpec,
    xml_bytes: bytes,
    parsed: Any,
) -> EpisodeRef:
    entry = pick_latest_audio_entry(parsed)
    if entry is None:
        return EpisodeRef(
            show=show,
            episode_title="",
            audio_url=None,
            duration_seconds=None,
            guid="",
            published=None,
            dai_likely=False,
            rss_ok=False,
            error="no audio episode in RSS",
        )
    audio_url = find_audio_link(entry)
    published = getattr(entry, "published", None) or getattr(entry, "updated", None)
    guid = str(getattr(entry, "id", None) or getattr(entry, "guid", None) or audio_url)
    title = str(getattr(entry, "title", None) or "untitled")
    markers = parse_publisher_markers_xml(xml_bytes)
    return EpisodeRef(
        show=show,
        episode_title=title,
        audio_url=str(audio_url) if audio_url else None,
        duration_seconds=itunes_duration(entry),
        guid=guid,
        published=str(published) if published else None,
        dai_likely=enclosure_is_dai(str(audio_url) if audio_url else None),
        publisher_markers=markers,
        rss_ok=True,
    )


def slug(text: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_")
    return cleaned.lower()[:80] or "episode"


def download_audio(
    url: str,
    dest: Path,
    *,
    get: GetFn | None = None,
    timeout: int = 180,
    max_bytes: int | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    session_get = get or requests.get
    referer = "https://open.acast.com/" if "acast.com" in url else None
    headers = {"User-Agent": USER_AGENT, "Referer": referer}
    try:
        with session_get(
            url, stream=True, timeout=timeout, headers=headers
        ) as response:
            response.raise_for_status()
            written = 0
            with dest.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=8192):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if max_bytes is not None and written > max_bytes:
                        raise OSError(f"download exceeded max_bytes={max_bytes}")
                    handle.write(chunk)
    except (OSError, requests.RequestException):
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise
    return dest


def fetch_show_episode(
    show: ShowSpec,
    output_dir: Path,
    *,
    download: bool = False,
    get: GetFn | None = None,
    max_bytes: int | None = None,
) -> tuple[EpisodeRef, Path | None]:
    try:
        xml_bytes, parsed = fetch_rss(show, get=get)
    except (OSError, requests.RequestException) as exc:
        logger.warning("RSS fetch failed for %s: %s", show.show_id, exc)
        episode = EpisodeRef(
            show=show,
            episode_title="",
            audio_url=None,
            duration_seconds=None,
            guid="",
            published=None,
            dai_likely=False,
            rss_ok=False,
            error=f"rss fetch failed: {exc}",
        )
        return episode, None
    episode = episode_from_rss(show, xml_bytes, parsed)
    rss_dir = output_dir / "rss"
    rss_dir.mkdir(parents=True, exist_ok=True)
    rss_path = rss_dir / f"{show.show_id}.xml"
    rss_path.write_bytes(xml_bytes)
    episode.raw_rss_path = rss_path
    if not download or not episode.audio_url:
        return episode, None
    audio_dir = output_dir / "audio"
    dest = audio_dir / f"{show.show_id}_{slug(episode.episode_title)}.mp3"
    try:
        path = download_audio(episode.audio_url, dest, get=get, max_bytes=max_bytes)
    except (OSError, requests.RequestException) as exc:
        logger.warning("audio download failed for %s: %s", show.show_id, exc)
        episode.error = f"audio download failed: {exc}"
        return episode, None
    return episode, path
