"""Stage pipeline: routing, concurrency limits, pipelining, guards, restart.

These run the production pieces together: PipelineWorkers threads, the real
dequeue/advance/route writer actions (writer_client's in-process test fallback
runs the real action functions), and the real PodcastProcessor.process with its
stage split. Only the slow edges are stubbed: Whisper, the LLM classifier, the
ffmpeg cut and the download. Each stub records when it runs so the tests can
check what overlapped and how many ran at once.

The DB is a SQLite file (not :memory:) so each worker thread gets its own
connection, as in production.
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
from typing import Any, cast
from unittest import mock

import pytest
from flask import Flask
from jinja2 import Template
from sqlalchemy.pool import QueuePool

from app.extensions import db
from app.jobs_manager import JobsManager, _initial_stage
from app.lanes import LANE_CLOUD, LANE_LOCAL
from app.models import Feed, ModelCall, Post, ProcessingJob, TranscriptSegment
from app.pipeline import (
    AUDIO_CUT_SLOTS,
    PRIORITY_AUTOMATIC,
    PRIORITY_INTERACTIVE,
    STAGE_LLM,
    STAGE_TRANSCRIBE,
    PipelineSettings,
    load_pipeline_settings,
    required_db_pool_size,
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


@pytest.fixture
def papp(tmp_path, monkeypatch) -> Iterator[Flask]:
    monkeypatch.chdir(REPO_ROOT)  # prompt files are repo-relative
    monkeypatch.setenv("PODLY_INSTANCE_DIR", str(tmp_path / "instance"))
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{tmp_path / 'pipeline.db'}"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "connect_args": {"timeout": 60},
        "pool_size": 30,
    }
    db.init_app(app)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


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
    ) -> None:
        self.app = app
        self.timeline = Timeline()
        self.config = create_standard_test_config()
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
        )

    def factory(self, stage: str, lane: str, job_id: str, guid: str) -> tuple[Any, Any]:
        self.factory_calls.append((stage, lane, guid))
        manager = TranscriptionManager(logger, self.config, transcriber=self.whisper)
        processor = PodcastProcessor(
            self.config,
            transcription_manager=manager,
            ad_classifier=self.classifier,
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


# ------------------------------------------------------------------ routing


def test_initial_stage_routing(papp):
    with papp.app_context():
        fresh = add_post("fresh")
        transcribed = add_post("transcribed")
        store_transcript(transcribed)
        other_model = add_post("other-model")
        store_transcript(other_model, model_name="some_other_model")
        chapter = add_post("chap", strategy="chapter", feed_title="ChapterFeed")
        insert = add_post("ins", strategy="chapter_insert", feed_title="InsertFeed")

        cfg = create_standard_test_config()
        model = TranscriptionManager(logger, cfg).transcriber.model_name
        db.session.query(ModelCall).filter_by(post_id=transcribed.id).update(
            {"model_name": model}
        )
        db.session.commit()
        with mock.patch("app.runtime_config.config", cfg):
            assert _initial_stage(fresh) == STAGE_TRANSCRIBE
            # Reusable transcript (same rule as keep-transcript reprocess).
            assert _initial_stage(transcribed) == STAGE_LLM
            # Transcript from another model is not reusable: transcribe again.
            assert _initial_stage(other_model) == STAGE_TRANSCRIBE
            assert _initial_stage(chapter) == STAGE_LLM
            assert _initial_stage(insert) == STAGE_TRANSCRIBE


def test_dequeue_pools_priority_and_guards(papp):
    with papp.app_context():
        for guid in ("a", "b", "c", "d", "e", "busy"):
            add_post(guid)
        add_job("t-old", "a", order=0)  # NULL stage counts as transcribe
        add_job("t-new-interactive", "b", order=5, priority=PRIORITY_INTERACTIVE)
        add_job("cloud", "c", order=1, lane=LANE_CLOUD, stage=STAGE_TRANSCRIBE)
        add_job("llm-1", "d", order=2, stage=STAGE_LLM, lane=LANE_CLOUD)
        add_job("llm-2", "e", order=3, stage=STAGE_LLM)
        add_job("busy-running", "busy", order=0, status="running", stage=STAGE_LLM)
        add_job("busy-dup", "busy", order=1, stage=STAGE_LLM)

        def claim(stage: str, lane: str = LANE_LOCAL, max_running: int = 9, **kw):
            data = act(
                "dequeue_job", stage=stage, lane=lane, max_running=max_running, **kw
            )
            return data.get("job_id") if data and "job_id" in data else None

        # Interactive beats an older automatic job; local pool skips cloud jobs.
        assert claim(STAGE_TRANSCRIBE) == "t-new-interactive"
        assert claim(STAGE_TRANSCRIBE) == "t-old"
        assert claim(STAGE_TRANSCRIBE) is None
        assert claim(STAGE_TRANSCRIBE, LANE_CLOUD) == "cloud"
        # LLM pool takes any lane, never a post that is already running
        # ("busy-dup"), and honours the exclude list (posts still in a thread).
        assert claim(STAGE_LLM, exclude_post_guids=["d"]) == "llm-2"
        # max_running counts running jobs of the pool: busy-running + llm-2.
        assert claim(STAGE_LLM, max_running=2) is None
        assert claim(STAGE_LLM) == "llm-1"
        assert claim(STAGE_LLM) is None
        claimed = get_job("t-old")
        assert claimed.status == "running" and claimed.stage == STAGE_TRANSCRIBE


def test_route_job_never_lowers_priority_and_skips_running(papp):
    with papp.app_context():
        add_post("p")
        add_job("j", "p")
        assert (
            act("route_job", job_id="j", stage=STAGE_LLM, priority=2)["priority"] == 2
        )
        assert act("route_job", job_id="j", priority=0)["priority"] == 2
        assert get_job("j").stage == STAGE_LLM
        get_job("j").status = "running"
        db.session.commit()
        assert act("route_job", job_id="j", stage=STAGE_TRANSCRIBE) == {"routed": False}


def test_start_post_processing_routes_keep_transcript_job_to_llm(papp):
    """The real start path: keep-transcript reprocess goes straight to LLM."""
    with papp.app_context():
        post = add_post("kt")
        cfg = create_standard_test_config()
        model = TranscriptionManager(logger, cfg).transcriber.model_name
        store_transcript(post, model_name=model)
        jm = object.__new__(JobsManager)
        jm._status_manager = ProcessingStatusManager(db.session, logger)
        jm._run_lock = threading.Lock()
        jm._run_id = None
        jm._workers = mock.Mock()
        with (
            mock.patch("app.jobs_manager._scheduler_app_context", papp.app_context),
            mock.patch("app.runtime_config.config", cfg),
        ):
            result = jm.start_post_processing("kt", priority="interactive", manual=True)
        assert result["status"] == "started"
        db.session.expire_all()
        job = get_job(result["job_id"])
        assert job.stage == STAGE_LLM
        assert job.priority == PRIORITY_INTERACTIVE
        jm._workers.wake.assert_called()


# ------------------------------------------------------------------ concurrency


def test_stage_limits_are_never_exceeded_and_are_used(papp):
    with papp.app_context():
        ids = []
        for i in range(8):
            add_post(f"p{i}")
            ids.append(add_job(f"j{i}", f"p{i}", order=i))
    h = Harness(
        papp,
        transcribe_workers=1,
        llm_workers=3,
        cut_slots=1,
        whisper_s=0.15,
        llm_s=0.9,
        cut_s=0.15,
    )
    h.run_until_done(ids)

    assert job_statuses(papp, ids) == ["completed"] * 8
    assert h.timeline.max_active["whisper"] == 1
    assert h.timeline.max_active["llm"] == 3  # reached, never above
    assert h.timeline.max_active["cut"] == 1
    assert len(h.timeline.of("whisper")) == 8
    assert len(h.timeline.of("llm")) == 8


@pytest.mark.parametrize("slots", [1, 2])
def test_audio_cuts_are_limited(papp, slots):
    """Four episodes reach the cut together; only ``slots`` cuts run at once."""
    with papp.app_context():
        ids = []
        for i in range(4):
            post = add_post(f"p{i}")
            store_transcript(post)
            ids.append(add_job(f"j{i}", f"p{i}", order=i, stage=STAGE_LLM))
    h = Harness(papp, llm_workers=4, cut_slots=slots, llm_s=0.2, cut_s=0.4)
    h.run_until_done(ids)
    assert job_statuses(papp, ids) == ["completed"] * 4
    assert h.timeline.max_active["llm"] == 4
    assert h.timeline.max_active["cut"] == slots


def test_transcription_of_next_episode_overlaps_llm_of_previous(papp):
    with papp.app_context():
        ids = []
        for i in range(4):
            add_post(f"p{i}")
            ids.append(add_job(f"j{i}", f"p{i}", order=i))
    h = Harness(papp, whisper_s=0.4, llm_s=0.8, cut_s=0.02)
    elapsed = h.run_until_done(ids)

    t = h.timeline
    a_llm = t.of("llm", "p0")[0]
    a_whisper = t.of("whisper", "p0")[0]
    b_whisper = t.of("whisper", "p1")[0]
    # B is transcribed while A is in the LLM stage ...
    assert overlaps(b_whisper, a_llm), (b_whisper, a_llm)
    # ... and the transcriber took B as soon as A's transcript was stored.
    assert b_whisper[0] - a_whisper[1] < 0.3
    # Sequential would be 4 * (0.4 + 0.8) = 4.8 s.
    sequential = sum(e - s for k, _, s, e in t.spans if k in ("whisper", "llm"))
    assert elapsed < sequential * 0.75, (elapsed, sequential)


def test_keep_transcript_jobs_skip_whisper_and_run_in_parallel(papp):
    with papp.app_context():
        long = add_post("long")
        ids = [add_job("long", "long", order=0)]
        for i in range(3):
            post = add_post(f"kt{i}")
            store_transcript(post)
            ids.append(add_job(f"kt{i}", f"kt{i}", order=i + 1, stage=STAGE_LLM))
        del long
    h = Harness(papp, whisper_s=1.5, llm_s=0.5)
    h.run_until_done(ids)

    t = h.timeline
    assert [g for k, g, *_ in t.spans if k == "whisper"] == ["long"]
    kt_llm = [t.of("llm", f"kt{i}")[0] for i in range(3)]
    long_whisper = t.of("whisper", "long")[0]
    for span in kt_llm:  # all three ran while Whisper was busy with "long"
        assert overlaps(span, long_whisper)
    assert t.max_active["llm"] >= 3


def test_one_post_is_never_processed_twice_at_once(papp):
    """Duplicate pending jobs for the same posts, many workers: never overlap."""
    with papp.app_context():
        ids = []
        for i in range(3):
            add_post(f"p{i}")
            for dup in range(3):
                ids.append(add_job(f"j{i}-{dup}", f"p{i}", order=i * 3 + dup))
    h = Harness(papp, transcribe_workers=3, llm_workers=4, whisper_s=0.2, llm_s=0.3)
    h.run_until_done(ids)

    assert h.timeline.max_per_post == 1
    # Each post went through Whisper and the LLM exactly once; the duplicate
    # jobs were dropped (the processor removes other active jobs of a post).
    for i in range(3):
        assert len(h.timeline.of("whisper", f"p{i}")) == 1
        assert len(h.timeline.of("llm", f"p{i}")) == 1
    statuses = job_statuses(papp, ids)
    assert statuses.count("completed") == 3
    assert set(statuses) <= {"completed", "deleted"}


def test_concurrent_claims_never_hand_out_a_job_twice(papp):
    with papp.app_context():
        for i in range(20):
            add_post(f"p{i}")
            add_job(f"j{i}", f"p{i}", order=i, stage=STAGE_LLM)
    settings = PipelineSettings(1, 20, 0, 1)
    workers = PipelineWorkers(
        settings,
        app_context=papp.app_context,
        get_run_id=lambda: None,
        status_manager=mock.Mock(),
    )
    llm_pool = next(p for p in workers.pools if p.stage == STAGE_LLM)
    got: list[tuple[str, str]] = []
    lock = threading.Lock()
    barrier = threading.Barrier(20)

    def grab() -> None:
        barrier.wait()
        claimed = workers.claim(llm_pool)
        if claimed:
            with lock:
                got.append(claimed)

    threads = [threading.Thread(target=grab) for _ in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join(10)
    assert len(got) == 20
    assert len({j for j, _ in got}) == 20
    assert workers.inflight_posts() == {g for _, g in got}


# ------------------------------------------------------------------ restart


def test_startup_requeues_interrupted_jobs_and_keeps_transcripts(papp):
    with papp.app_context():
        mid_llm = add_post("mid-llm")
        store_transcript(mid_llm)
        add_job("mid-llm", "mid-llm", order=0, status="running", stage=STAGE_LLM)
        add_post("mid-whisper")
        add_job(
            "mid-whisper",
            "mid-whisper",
            order=1,
            status="running",
            stage=STAGE_TRANSCRIBE,
        )
        add_post("queued")
        add_job("queued", "queued", order=2)

        jm = object.__new__(JobsManager)
        jm._workers = mock.Mock()
        with mock.patch("app.jobs_manager._scheduler_app_context", papp.app_context):
            result = jm.requeue_interrupted_jobs()
        assert result["status"] == "success"
        assert result["requeued_jobs"] == 2 and result["pending_jobs"] == 3
        jm._workers.wake.assert_called()
        db.session.expire_all()
        assert get_job("mid-llm").stage == STAGE_LLM
        assert {j.status for j in ProcessingJob.query.all()} == {"pending"}

    h = Harness(papp, whisper_s=0.1, llm_s=0.1)
    h.run_until_done(["mid-llm", "mid-whisper", "queued"])
    assert job_statuses(papp, ["mid-llm", "mid-whisper", "queued"]) == ["completed"] * 3
    whispered = {g for k, g, *_ in h.timeline.spans if k == "whisper"}
    assert whispered == {"mid-whisper", "queued"}  # transcript kept for mid-llm


def test_startup_wires_requeue_instead_of_clearing():
    import inspect

    from app import _start_scheduler_and_jobs

    source = inspect.getsource(_start_scheduler_and_jobs)
    assert "requeue_interrupted_jobs()" in source
    assert "clear_active_jobs" not in source


# ------------------------------------------------------------------ stage edges


def test_llm_stage_without_transcript_goes_back_to_transcribe(papp):
    with papp.app_context():
        add_post("lost")
        add_job("lost", "lost", stage=STAGE_LLM)  # transcript missing
    h = Harness(papp, whisper_s=0.1, llm_s=0.1)
    h.run_until_done(["lost"])
    assert job_statuses(papp, ["lost"]) == ["completed"]
    assert [s for s, _, _ in h.factory_calls] == [
        STAGE_LLM,
        STAGE_TRANSCRIBE,
        STAGE_LLM,
    ]
    assert len(h.timeline.of("whisper")) == 1


def test_cancel_during_transcription_is_not_revived(papp):
    with papp.app_context():
        add_post("c")
        add_job("c", "c")
    h = Harness(papp, whisper_s=0.6, llm_s=0.1)

    def cancel_soon() -> None:
        time.sleep(0.3)
        with papp.app_context():
            act("mark_cancelled", job_id="c", reason="user")

    threading.Thread(target=cancel_soon).start()
    h.run_until_done(["c"])
    time.sleep(0.2)
    # The processor records a cancel it notices as failed("Cancelled"), as in
    # 2.5.0; what matters here is that it is not handed to the LLM stage.
    assert job_statuses(papp, ["c"])[0] in ("cancelled", "failed")
    assert h.timeline.of("llm") == []
    with papp.app_context():
        assert get_job("c").stage == STAGE_TRANSCRIBE


def test_advance_refuses_a_job_cancelled_after_transcription(papp):
    with papp.app_context():
        add_post("c")
        add_job("c", "c", status="cancelled", stage=STAGE_TRANSCRIBE)
        assert act("advance_job_stage", job_id="c", stage=STAGE_LLM) == {
            "advanced": False,
            "status": "cancelled",
        }


def test_ui_status_between_stages(papp):
    """After transcription the job shows as queued for ad detection at 50%."""
    with papp.app_context():
        add_post("s")
        add_job("s", "s", status="running", stage=STAGE_TRANSCRIBE)
        assert act(
            "advance_job_stage",
            job_id="s",
            stage=STAGE_LLM,
            step=2,
            step_name="Transcribed; waiting for ad detection",
            progress=50.0,
        ) == {"advanced": True, "status": "pending"}
        job = get_job("s")
        db.session.refresh(job)
        assert (job.status, job.stage, job.current_step, job.progress_percentage) == (
            "pending",
            STAGE_LLM,
            2,
            50.0,
        )


# ------------------------------------------------------------------ sessions/pool


def test_worker_threads_return_their_db_connections(papp):
    with papp.app_context():
        ids = []
        for i in range(4):
            add_post(f"p{i}")
            ids.append(add_job(f"j{i}", f"p{i}", order=i))
    h = Harness(papp, whisper_s=0.05, llm_s=0.1)
    h.run_until_done(ids)
    with papp.app_context():
        pool = cast(QueuePool, db.engine.pool)
        assert pool.checkedout() == 0


def test_required_pool_size_for_live_settings(monkeypatch):
    monkeypatch.delenv("PODLY_TRANSCRIBE_WORKERS", raising=False)
    monkeypatch.delenv("PODLY_LLM_WORKERS", raising=False)
    settings = load_pipeline_settings()
    assert (settings.transcribe_workers, settings.llm_workers) == (1, 4)
    assert settings.cloud_workers == 2
    # 4 request threads + 7 workers + 2 refresh + 1 scheduler + 2 headroom
    assert required_db_pool_size(settings, server_threads=4) == 16


def test_app_db_pool_is_sized_from_pipeline(monkeypatch):
    from app import _configure_database

    monkeypatch.setenv("SERVER_THREADS", "4")
    monkeypatch.setenv("PODLY_LLM_WORKERS", "6")
    app = Flask(__name__)
    _configure_database(app)
    # 4 + (1 + 2 + 6) + 2 + 1 + 2
    assert app.config["SQLALCHEMY_ENGINE_OPTIONS"]["pool_size"] == 18


@pytest.mark.parametrize("value", ["0", "-1", "four"])
def test_bad_worker_counts_are_rejected(monkeypatch, value):
    monkeypatch.setenv("PODLY_LLM_WORKERS", value)
    with pytest.raises(ValueError, match="PODLY_LLM_WORKERS"):
        load_pipeline_settings()


@pytest.fixture(autouse=True)
def _reset_cut_slots() -> Iterator[None]:
    yield
    AUDIO_CUT_SLOTS.reset()
