"""Review round 1 for the processing lanes (PR #4): the paid lane must only
ever be reached on purpose, and every failure must land back in the free lane.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest import mock

import httpx
import openai
import pytest

from app import cloud_lane
from app.cloud_lane import BudgetedCloudTranscriber, CloudLaneFallback
from app.extensions import db
from app.lane_store import load_lane_settings
from app.lanes import LANE_CLOUD, LANE_LOCAL
from app.models import CloudLaneUsage, ModelCall, Post, ProcessingJob, TranscriptSegment
from app.routes.post_routes import post_bp
from app.runtime_config import config as runtime_config
from podcast_processor.transcription_manager import TranscriptionManager
from shared import defaults as DEFAULTS
from shared.config import LocalWhisperConfig
from tests.test_lanes import (
    AUDIO_SECONDS,
    _act,
    _copy_audio,
    _FakeTranscriptions,
    _job_row,
    _post,
    _post_with_job,
    _store_settings,
    _transcriber,
    manager,  # noqa: F401 - pytest fixture
)

CLOUD_MODEL = DEFAULTS.CLOUD_LANE_MODEL


# ------------------------------------------------------------- B1 transcripts


def _transcript(post: Post, model_name: str) -> None:
    db.session.add(
        TranscriptSegment(
            post_id=post.id, sequence_num=0, start_time=0.0, end_time=5.0, text="hi"
        )
    )
    db.session.add(
        ModelCall(
            post_id=post.id,
            model_name=model_name,
            prompt="Whisper transcription job",
            first_segment_sequence_num=0,
            last_segment_sequence_num=0,
            status="success",
        )
    )
    db.session.commit()


@pytest.fixture
def local_whisper():
    original = runtime_config.whisper
    runtime_config.whisper = LocalWhisperConfig(model="base.en")
    yield
    runtime_config.whisper = original


def test_cloud_transcript_is_reused_by_the_local_lane(app, local_whisper):
    with app.app_context():
        _store_settings()
        post = _post(app)
        _transcript(post, CLOUD_MODEL)
        manager = TranscriptionManager(
            logger=logging.getLogger("test"),
            config=runtime_config,
            db_session=db.session,
        )
        assert manager.transcriber.model_name == "local_base.en"
        assert manager.get_reusable_transcription(post) is not None


def test_local_transcript_is_reused_by_the_cloud_lane(app, local_whisper):
    with app.app_context():
        _store_settings()
        post = _post(app)
        _transcript(post, "local_base.en")
        cloud = BudgetedCloudTranscriber(
            load_lane_settings(), job_id="j1", post_guid="g1"
        )
        manager = TranscriptionManager(
            logger=logging.getLogger("test"),
            config=runtime_config,
            db_session=db.session,
            transcriber=cloud,
        )
        assert manager.get_reusable_transcription(post) is not None


def test_transcript_from_an_unconfigured_model_is_still_stale(app, local_whisper):
    """Upstream behaviour kept: changing the local model invalidates old ones."""
    with app.app_context():
        _store_settings()
        post = _post(app)
        _transcript(post, "local_small.en")
        manager = TranscriptionManager(
            logger=logging.getLogger("test"),
            config=runtime_config,
            db_session=db.session,
        )
        assert manager.get_reusable_transcription(post) is None


def test_keep_transcript_endpoint_accepts_a_cloud_transcript(app, local_whisper):
    app.testing = True
    app.register_blueprint(post_bp)
    with app.app_context():
        _store_settings()
        post = _post(app)
        _transcript(post, CLOUD_MODEL)
    jobs = mock.Mock()
    jobs.start_post_processing.return_value = {"status": "started", "job_id": "x"}
    with (
        mock.patch("app.routes.post_routes.clear_post_processing_data_keep_transcript"),
        mock.patch("app.routes.post_routes.get_jobs_manager", return_value=jobs),
    ):
        resp = app.test_client().post("/api/posts/g1/reprocess/keep-transcript")
    assert resp.status_code == 200, resp.data
    kwargs = jobs.start_post_processing.call_args.kwargs
    assert kwargs["manual"] is True
    assert kwargs["needs_transcription"] is False


# ------------------------------------------------------------- B2 re-queues


def test_refresh_cleanup_requeue_never_lands_in_cloud(app, tmp_path):
    with app.app_context():
        _post(app, audio_path=str(tmp_path / "gone.mp3"))  # file does not exist
        db.session.add(
            ProcessingJob(
                id="done",
                post_guid="g1",
                status="completed",
                lane=LANE_CLOUD,
                lane_reason="manual; est. $0.0200",
                created_at=datetime(2026, 1, 1),
            )
        )
        db.session.commit()

        assert _act("cleanup_missing_audio_paths")["result"] == 1
        job = _job_row("done")
        assert job.status == "pending"
        assert job.lane is None
        assert job.lane_reason == "automatic job"
        assert _act("dequeue_job", lane=LANE_CLOUD, max_running=2) == {"result": None}
        assert _act("dequeue_job", lane=LANE_LOCAL, max_running=1)["job_id"] == "done"


def test_status_requeue_of_a_finished_cloud_job_goes_local(app):
    with app.app_context():
        job_id = _post_with_job(status="failed", lane=LANE_CLOUD)
        _act("update_job_status", job_id=job_id, status="pending", step=0)
        assert _job_row(job_id).lane is None
        assert _act("dequeue_job", lane=LANE_CLOUD, max_running=2) == {"result": None}


# ------------------------------------------------------------- M1 timeouts


def test_cloud_client_has_short_timeout_and_no_sdk_retries(app):
    with app.app_context():
        _store_settings()
        t = BudgetedCloudTranscriber(load_lane_settings(), job_id="j1", post_guid="g1")
        assert t.openai_client.max_retries == 0
        assert t.openai_client.timeout == DEFAULTS.CLOUD_LANE_TIMEOUT_SEC == 120


class _RaisingTranscriptions(_FakeTranscriptions):
    def __init__(self, exc: Exception) -> None:
        super().__init__()
        self.exc = exc

    def create(self, **_kwargs):
        self.calls += 1
        raise self.exc


_REQUEST = httpx.Request("POST", "http://stub.invalid/v1/audio/transcriptions")


@pytest.mark.parametrize(
    ("exc", "billed"),
    [
        (openai.APITimeoutError(request=_REQUEST), True),
        (openai.APIConnectionError(request=_REQUEST), True),
        (
            openai.InternalServerError(
                "boom", response=httpx.Response(503, request=_REQUEST), body=None
            ),
            False,
        ),
    ],
    ids=["timeout", "connection-reset", "http-503"],
)
def test_timed_out_upload_is_counted_as_billed(app, tmp_path, exc, billed):
    with app.app_context():
        _store_settings()
        t = _transcriber(_RaisingTranscriptions(exc))
        with pytest.raises(CloudLaneFallback):
            t.transcribe(_copy_audio(tmp_path))
        row = CloudLaneUsage.query.one()
        assert row.status == "failed"
        if billed:
            assert row.billed_seconds == pytest.approx(AUDIO_SECONDS, abs=0.5)
            assert row.cost_usd > 0
        else:
            assert row.billed_seconds == 0
            assert row.cost_usd == 0


# ------------------------------------------------------------- M2 wiring


class _Spy:
    def __init__(self) -> None:
        self.processed: list[str] = []

    def process(self, post, job_id, cancel_callback=None):
        self.processed.append(job_id)


def test_local_job_never_builds_the_paid_transcriber(app, manager):  # noqa: F811
    with app.app_context():
        _store_settings()
        job_id = _post_with_job(status="running", lane=None)
        local = _Spy()
        with (
            mock.patch("app.jobs_manager.get_processor", return_value=local),
            mock.patch(
                "app.jobs_manager.build_cloud_processor",
                wraps=cloud_lane.build_cloud_processor,
            ) as build,
            mock.patch.object(
                cloud_lane.BudgetedCloudTranscriber,
                "__init__",
                autospec=True,
                side_effect=AssertionError("paid transcriber built for a local job"),
            ),
            mock.patch("openai.OpenAI") as openai_client,
        ):
            manager._process_job(job_id, "g1", lane=LANE_LOCAL)
        assert local.processed == [job_id]
        build.assert_not_called()
        openai_client.assert_not_called()
        assert CloudLaneUsage.query.count() == 0


def test_real_cloud_processor_transcribes_once_and_records_usage(
    app, tmp_path, local_whisper
):
    with app.app_context():
        _store_settings()
        post = _post(app, audio_path=_copy_audio(tmp_path))
        processor, transcriber = cloud_lane.build_cloud_processor("j1", "g1")
        assert transcriber is processor.transcription_manager.transcriber
        fake = _FakeTranscriptions()
        transcriber.openai_client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=fake)
        )

        segments = processor.transcription_manager.transcribe(post)

        assert [s.text for s in segments] == ["hello"]
        assert fake.calls == 1
        row = CloudLaneUsage.query.one()
        assert (row.status, row.job_id) == ("charged", "j1")
        call = ModelCall.query.filter_by(post_id=post.id).one()
        assert (call.model_name, call.status) == (CLOUD_MODEL, "success")


def test_cloud_worker_loop_runs_cloud_jobs_on_the_cloud_lane(app, manager):  # noqa: F811
    calls: list[tuple] = []

    def dequeue(lane=LANE_LOCAL):
        calls.append(("dequeue", lane))
        if len(calls) == 1:
            return ("j1", "g1")
        manager._stop_event.set()
        return None

    with (
        mock.patch.object(manager, "_dequeue_next_job", side_effect=dequeue),
        mock.patch.object(manager, "_process_job") as process,
    ):
        worker = threading.Thread(target=manager._cloud_worker_loop, daemon=True)
        worker.start()
        worker.join(5)
    assert not worker.is_alive()
    assert calls[0] == ("dequeue", LANE_CLOUD)
    process.assert_called_once_with("j1", "g1", lane=LANE_CLOUD)


def test_upload_time_length_limit_refuses_without_calling_api(app, tmp_path):
    with app.app_context():
        _store_settings(max_episode_minutes=1)  # the file is 66 s
        fake = _FakeTranscriptions()
        t = _transcriber(fake)
        with pytest.raises(CloudLaneFallback, match="longer than the 1 min"):
            t.transcribe(_copy_audio(tmp_path))
        assert fake.calls == 0
        assert CloudLaneUsage.query.count() == 0


# ------------------------------------------------------------- minor items


def test_cancel_during_cloud_call_is_not_requeued(app, tmp_path):
    with app.app_context():
        _store_settings()
        job_id = _post_with_job(status="running", lane=LANE_CLOUD)

        class _CancelThenFail(_FakeTranscriptions):
            def create(self, **_kwargs):
                _act(
                    "update_job_status",
                    job_id=job_id,
                    status="cancelled",
                    step=2,
                    error_message="Cancelled by user",
                )
                raise RuntimeError("connection dropped")

        t = BudgetedCloudTranscriber(
            load_lane_settings(), job_id=job_id, post_guid="g1"
        )
        t.openai_client = SimpleNamespace(
            audio=SimpleNamespace(transcriptions=_CancelThenFail())
        )
        with pytest.raises(RuntimeError, match="connection dropped"):
            t.transcribe(_copy_audio(tmp_path))
        assert t.fallback_reason is None  # so _process_job does not requeue
        assert CloudLaneUsage.query.one().status == "failed"


def test_reservation_failure_falls_back(app, tmp_path):
    with app.app_context():
        _store_settings()
        fake = _FakeTranscriptions()
        t = _transcriber(fake)
        with (
            mock.patch.object(t, "_reserve", side_effect=TimeoutError("writer")),
            pytest.raises(CloudLaneFallback, match="could not reserve"),
        ):
            t.transcribe(_copy_audio(tmp_path))
        assert fake.calls == 0


def test_cloud_setup_failure_requeues_locally(app, manager):  # noqa: F811
    with app.app_context():
        job_id = _post_with_job(status="running", lane=LANE_CLOUD)
        with mock.patch(
            "app.jobs_manager.build_cloud_processor",
            side_effect=RuntimeError("settings unreadable"),
        ):
            manager._process_job(job_id, "g1", lane=LANE_CLOUD)
        job = _job_row(job_id)
        assert job.status == "pending"
        assert job.lane == LANE_LOCAL
        assert "cloud setup failed: settings unreadable" in (job.lane_reason or "")
