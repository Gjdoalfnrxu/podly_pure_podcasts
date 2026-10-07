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

import threading
import time
from typing import Any, cast
from unittest import mock

import pytest
from flask import Flask
from sqlalchemy.pool import QueuePool

from app.extensions import db
from app.jobs_manager import JobsManager, _initial_stage
from app.lanes import LANE_CLOUD, LANE_LOCAL
from app.models import ModelCall, ProcessingJob
from app.pipeline import (
    PRIORITY_INTERACTIVE,
    STAGE_LLM,
    STAGE_TRANSCRIBE,
    PipelineSettings,
    load_pipeline_settings,
    required_db_pool_size,
)
from app.pipeline_workers import PipelineWorkers
from app.writer.client import writer_client
from podcast_processor.processing_status_manager import ProcessingStatusManager
from podcast_processor.transcription_manager import TranscriptionManager
from shared.test_utils import create_standard_test_config

from .pipeline_harness import (
    Harness,
    act,
    add_job,
    add_post,
    get_job,
    job_statuses,
    logger,
    overlaps,
    store_transcript,
)

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
        real_action = writer_client.action
        actions: list[tuple[str, dict]] = []

        def recording_action(name: str, params: dict[str, Any], **kw: Any) -> Any:
            actions.append((name, params))
            return real_action(name, params, **kw)

        with (
            mock.patch("app.jobs_manager._scheduler_app_context", papp.app_context),
            mock.patch("app.runtime_config.config", cfg),
            mock.patch.object(writer_client, "action", side_effect=recording_action),
        ):
            result = jm.start_post_processing(
                "kt", priority="interactive", manual=True, needs_transcription=False
            )
        assert result["status"] == "started"
        db.session.expire_all()
        job = get_job(result["job_id"])
        assert job.stage == STAGE_LLM
        assert job.priority == PRIORITY_INTERACTIVE
        assert job.lane != LANE_CLOUD  # keep-transcript never uses the paid lane
        jm._workers.wake.assert_called()
        # Stage and priority are in the insert itself, so no worker can see the
        # new job unrouted.
        created = [p["job_data"] for n, p in actions if n == "create_job"]
        assert len(created) == 1
        assert (created[0]["stage"], created[0]["priority"]) == (
            STAGE_LLM,
            PRIORITY_INTERACTIVE,
        )


def test_requeues_never_put_work_in_the_cloud_lane(papp):
    """Restart re-queue and the LLM->transcribe bounce send cloud jobs local."""
    with papp.app_context():
        add_post("cut-off")
        add_job(
            "cut-off",
            "cut-off",
            status="running",
            stage=STAGE_TRANSCRIBE,
            lane=LANE_CLOUD,
        )
        add_post("waiting")
        add_job("waiting", "waiting", stage=STAGE_TRANSCRIBE, lane=LANE_CLOUD)
        add_post("bounce")
        add_job("bounce", "bounce", status="running", stage=STAGE_LLM, lane=LANE_CLOUD)

        assert act("advance_job_stage", job_id="bounce", stage=STAGE_TRANSCRIBE)[
            "advanced"
        ]
        assert act("requeue_interrupted_jobs")["requeued"] == 1
        db.session.expire_all()
        assert get_job("cut-off").lane is None
        assert get_job("bounce").lane is None
        # A job that was only waiting keeps the lane it was queued with.
        assert get_job("waiting").lane == LANE_CLOUD


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
