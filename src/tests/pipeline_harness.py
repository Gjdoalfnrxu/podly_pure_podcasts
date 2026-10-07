"""Shared harness for the stage pipeline tests (test_pipeline*.py).

Runs the production pieces together: PipelineWorkers threads, the real writer
actions (writer_client's in-process test fallback runs the real action
functions), and the real PodcastProcessor.process. Only the slow edges are
stubbed; each stub records when it runs so tests can check what overlapped and
how many ran at once. The DB is a SQLite file (not :memory:) so each worker
thread gets its own connection, as in production.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from flask import Flask
from jinja2 import Template

from app.extensions import db
from app.models import Feed, ModelCall, Post, ProcessingJob, TranscriptSegment
from app.pipeline import (
    PRIORITY_AUTOMATIC,
    PipelineSettings,
)
from app.pipeline_workers import PipelineWorkers
from app.writer.client import writer_client
from podcast_processor.ad_classifier import AdClassifier
from podcast_processor.audio_processor import AudioProcessor
from podcast_processor.podcast_downloader import PodcastDownloader
from podcast_processor.podcast_processor import PodcastProcessor
from podcast_processor.processing_status_manager import ProcessingStatusManager
from podcast_processor.transcribe import Segment, Transcriber
from podcast_processor.transcription_manager import TranscriptionManager
from shared.config import Config
from shared.test_utils import create_standard_test_config

REPO_ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger("test_pipeline")


# ------------------------------------------------------------------ harness


class Timeline:
    """Records spans of stub work and the live concurrency of each kind."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.spans: list[tuple[str, str, float, float]] = []
        self._active: dict[str, int] = defaultdict(int)
        self.max_active: dict[str, int] = defaultdict(int)
        self._active_posts: dict[str, int] = defaultdict(int)
        self.max_per_post = 0

    @contextmanager
    def span(self, kind: str, guid: str) -> Iterator[None]:
        with self._lock:
            self._active[kind] += 1
            self.max_active[kind] = max(self.max_active[kind], self._active[kind])
            self._active_posts[guid] += 1
            self.max_per_post = max(self.max_per_post, self._active_posts[guid])
        start = time.monotonic()
        try:
            yield
        finally:
            end = time.monotonic()
            with self._lock:
                self._active[kind] -= 1
                self._active_posts[guid] -= 1
                self.spans.append((kind, guid, start, end))

    def of(self, kind: str, guid: str | None = None) -> list[tuple[float, float]]:
        return [
            (s, e)
            for k, g, s, e in self.spans
            if k == kind and (guid is None or g == guid)
        ]


class StubWhisper(Transcriber):
    def __init__(self, timeline: Timeline, seconds: float) -> None:
        self.timeline = timeline
        self.seconds = seconds

    @property
    def model_name(self) -> str:
        return "stub_whisper"

    def transcribe(self, audio_file_path: str) -> list[Segment]:
        guid = Path(audio_file_path).parent.parent.name  # in/jobs/<guid>/<job>/x
        with self.timeline.span("whisper", guid):
            time.sleep(self.seconds)
        return [
            Segment(start=0.0, end=5.0, text="hello"),
            Segment(start=5.0, end=10.0, text="world"),
        ]


class StubClassifier(AdClassifier):
    def __init__(self, timeline: Timeline, seconds: float) -> None:
        self.timeline = timeline
        self.seconds = seconds

    def classify(
        self,
        *,
        transcript_segments: list[TranscriptSegment],
        system_prompt: str,
        user_prompt_template: Template,
        post: Post,
    ) -> None:
        assert transcript_segments, "LLM stage ran without a transcript"
        with self.timeline.span("llm", post.guid):
            time.sleep(self.seconds)


class StubAudio(AudioProcessor):
    def __init__(self, timeline: Timeline, seconds: float) -> None:
        self.timeline = timeline
        self.seconds = seconds

    def process_audio(self, post: Post, output_path: str) -> list[tuple[int, int]]:
        with self.timeline.span("cut", post.guid):
            time.sleep(self.seconds)
            Path(output_path).write_bytes(b"cut")
        return []


class StubDownloader(PodcastDownloader):
    def __init__(self) -> None:
        pass

    def download_episode(self, post: Post, dest_path: str) -> str | None:  # ty: ignore[invalid-method-override]
        Path(dest_path).parent.mkdir(parents=True, exist_ok=True)
        Path(dest_path).write_bytes(b"audio")
        return dest_path


class Harness:
    def __init__(
        self,
        app: Flask,
        *,
        transcribe_workers: int = 1,
        llm_workers: int = 4,
        cut_slots: int = 4,
        whisper_s: float = 0.4,
        llm_s: float = 0.6,
        cut_s: float = 0.02,
        llm_call_limit: int = 0,
        real_classifier: bool = False,
        config: Config | None = None,
    ) -> None:
        self.app = app
        self.timeline = Timeline()
        self.config = config or create_standard_test_config()
        # None: each stage run builds the production AdClassifier(config).
        self.real_classifier = real_classifier
        self.whisper = StubWhisper(self.timeline, whisper_s)
        self.classifier = StubClassifier(self.timeline, llm_s)
        self.audio = StubAudio(self.timeline, cut_s)
        self.settings = PipelineSettings(
            transcribe_workers=transcribe_workers,
            llm_workers=llm_workers,
            cloud_workers=0,
            audio_cut_concurrency=cut_slots,
        )
        self.factory_calls: list[tuple[str, str, str]] = []
        self.workers = PipelineWorkers(
            self.settings,
            app_context=app.app_context,
            get_run_id=lambda: None,
            status_manager=ProcessingStatusManager(db.session, logger),
            processor_factory=self.factory,
            llm_call_limit=lambda: llm_call_limit,
        )

    def factory(self, stage: str, lane: str, job_id: str, guid: str) -> tuple[Any, Any]:
        self.factory_calls.append((stage, lane, guid))
        manager = TranscriptionManager(logger, self.config, transcriber=self.whisper)
        processor = PodcastProcessor(
            self.config,
            transcription_manager=manager,
            ad_classifier=None if self.real_classifier else self.classifier,
            audio_processor=self.audio,
            downloader=StubDownloader(),
        )
        return processor, None

    def run_until_done(self, job_ids: list[str], timeout: float = 30.0) -> float:
        start = time.monotonic()
        self.workers.start()
        try:
            deadline = start + timeout
            while time.monotonic() < deadline:
                statuses = job_statuses(self.app, job_ids)
                if all(
                    s in ("completed", "failed", "cancelled", "deleted")
                    for s in statuses
                ):
                    return time.monotonic() - start
                time.sleep(0.05)
            raise AssertionError(f"jobs not done: {job_statuses(self.app, job_ids)}")
        finally:
            self.workers.stop()


def job_statuses(app: Flask, job_ids: list[str]) -> list[str]:
    """Status per job; "deleted" when the processor removed a duplicate job."""
    with app.app_context():
        db.session.expire_all()
        jobs = [db.session.get(ProcessingJob, j) for j in job_ids]
        return [job.status if job else "deleted" for job in jobs]


def add_post(
    guid: str,
    *,
    strategy: str = "llm",
    feed_title: str = "Feed",
) -> Post:
    feed = Feed.query.filter_by(title=feed_title).first()
    if feed is None:
        feed = Feed(
            title=feed_title,
            rss_url=f"https://ex.example.com/{feed_title}",
            ad_detection_strategy=strategy,
        )
        db.session.add(feed)
        db.session.commit()
    post = Post(
        feed_id=feed.id,
        guid=guid,
        download_url=f"https://ex.example.com/{guid}.mp3",
        title=f"Episode {guid}",
        whitelisted=True,
    )
    db.session.add(post)
    db.session.commit()
    return post


_T0 = datetime(2026, 1, 1)


def add_job(
    job_id: str,
    guid: str,
    *,
    order: int = 0,
    status: str = "pending",
    stage: str | None = None,
    priority: int = PRIORITY_AUTOMATIC,
    lane: str | None = None,
) -> str:
    db.session.add(
        ProcessingJob(
            id=job_id,
            post_guid=guid,
            status=status,
            stage=stage,
            priority=priority,
            lane=lane,
            current_step=0,
            total_steps=4,
            progress_percentage=0.0,
            created_at=_T0 + timedelta(seconds=order),
        )
    )
    db.session.commit()
    return job_id


def store_transcript(post: Post, model_name: str = "stub_whisper") -> None:
    db.session.add(
        ModelCall(
            post_id=post.id,
            model_name=model_name,
            prompt="Whisper transcription job",
            first_segment_sequence_num=0,
            last_segment_sequence_num=1,
            status="success",
        )
    )
    db.session.add_all(
        [
            TranscriptSegment(
                post_id=post.id, sequence_num=0, start_time=0, end_time=5, text="a"
            ),
            TranscriptSegment(
                post_id=post.id, sequence_num=1, start_time=5, end_time=10, text="b"
            ),
        ]
    )
    db.session.commit()


def act(name: str, **params: Any) -> Any:
    result = writer_client.action(name, params, wait=True)
    assert result is not None and result.success, result
    return result.data


def get_job(job_id: str) -> ProcessingJob:
    job = db.session.get(ProcessingJob, job_id)
    assert job is not None
    return job


def overlaps(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return a[0] < b[1] and b[0] < a[1]
