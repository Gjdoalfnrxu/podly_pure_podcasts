"""Stage pipeline: settings, stage names, job priority and shared limits.

A job runs in two stages, each with its own worker pool:

* ``transcribe``: download + Whisper. Local Whisper has
  ``transcribe_workers`` threads (default 1). The cloud fast lane (app/lanes.py)
  adds its own transcribe threads.
* ``llm``: ad detection (chunks stay sequential inside one episode) + audio cut.
  ``llm_workers`` threads (default 4).

When a job's transcript is written it goes back to ``pending`` with stage
``llm``, so the transcriber can start the next episode while the LLM stage works
on this one. A job whose transcript already exists (keep-transcript reprocess,
restart after transcription) is queued straight into the ``llm`` stage.

This module has no DB or Flask imports so the web process can size its DB pool
from it before the app exists.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from shared import defaults as DEFAULTS

STAGE_TRANSCRIBE = "transcribe"
STAGE_LLM = "llm"

# Higher runs first; equal priority runs oldest first. Re-requesting a queued
# job never lowers its priority.
PRIORITY_INTERACTIVE = 2  # someone clicked process / reprocess / add feed
PRIORITY_DOWNLOAD = 1  # a podcast app asked for the episode
PRIORITY_AUTOMATIC = 0  # feed refresh backlog
_PRIORITIES = {"interactive": PRIORITY_INTERACTIVE, "download": PRIORITY_DOWNLOAD}

# Threads in the web process that can hold a pooled DB connection besides the
# request threads and the pipeline workers.
FEED_REFRESH_WORKERS = 2  # bounded background refresh executor (PR #3)
SCHEDULER_WORKERS = 1  # APScheduler threadpool max_workers (app/__init__.py)
POOL_HEADROOM = 2  # OPML import / subscribe threads, short-lived extras


def priority_rank(priority: str | None) -> int:
    return _PRIORITIES.get(priority or "", PRIORITY_AUTOMATIC)


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


@dataclass(frozen=True)
class PipelineSettings:
    transcribe_workers: int
    llm_workers: int
    cloud_workers: int
    audio_cut_concurrency: int

    @property
    def worker_threads(self) -> int:
        return self.transcribe_workers + self.cloud_workers + self.llm_workers


def load_pipeline_settings() -> PipelineSettings:
    return PipelineSettings(
        transcribe_workers=_env_int(
            "PODLY_TRANSCRIBE_WORKERS", DEFAULTS.PIPELINE_TRANSCRIBE_WORKERS, 1
        ),
        llm_workers=_env_int("PODLY_LLM_WORKERS", DEFAULTS.PIPELINE_LLM_WORKERS, 1),
        cloud_workers=DEFAULTS.CLOUD_LANE_CONCURRENCY,
        audio_cut_concurrency=_env_int(
            "PODLY_AUDIO_CUT_CONCURRENCY", DEFAULTS.PIPELINE_AUDIO_CUT_CONCURRENCY, 1
        ),
    )


def required_db_pool_size(settings: PipelineSettings, server_threads: int) -> int:
    """Connections the web process can hold at once.

    Every pipeline worker runs a job inside its own app context, so it holds at
    most one pooled connection for the job's duration; each waitress request
    thread holds at most one. Add the feed-refresh executor, the scheduler
    thread and a little headroom for short-lived import/subscribe threads.
    """
    return (
        max(server_threads, 1)
        + settings.worker_threads
        + FEED_REFRESH_WORKERS
        + SCHEDULER_WORKERS
        + POOL_HEADROOM
    )


def server_threads_from_env() -> int:
    """Same parsing as src/main.py (invalid or missing -> 1)."""
    try:
        return int(os.environ.get("SERVER_THREADS", "1"))
    except ValueError:
        return 1


class _CutSlots:
    """Process-wide limit on concurrent ffmpeg cuts (sized on first use)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._semaphore: threading.BoundedSemaphore | None = None
        self.size = 0

    def get(self) -> threading.BoundedSemaphore:
        with self._lock:
            if self._semaphore is None:
                self.size = load_pipeline_settings().audio_cut_concurrency
                self._semaphore = threading.BoundedSemaphore(self.size)
            return self._semaphore

    def reset(self, size: int | None = None) -> None:
        with self._lock:
            self._semaphore = threading.BoundedSemaphore(size) if size else None
            self.size = size or 0


AUDIO_CUT_SLOTS = _CutSlots()


@contextmanager
def audio_cut_slot() -> Iterator[None]:
    semaphore = AUDIO_CUT_SLOTS.get()
    with semaphore:
        yield
