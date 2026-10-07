"""Processing lanes: routing rules, budget accounting, cloud fallback.

Writer actions run in-process (writer_client's test fallback executes the real
action functions and commits), so budget accounting is the production code.
"""

from __future__ import annotations

import dataclasses
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest
from openai.types.audio.transcription_segment import TranscriptionSegment

from app.cloud_lane import BudgetedCloudTranscriber, CloudLaneFallback
from app.extensions import db
from app.jobs_manager import JobsManager
from app.lane_store import load_lane_settings, month_spent_usd
from app.lanes import (
    LANE_CLOUD,
    LANE_LOCAL,
    LaneSettings,
    billed_seconds_for_chunks,
    cost_usd,
    decide_lane,
    month_start,
)
from app.models import CloudLaneUsage, Feed, Post, ProcessingJob
from app.writer.client import writer_client

AUDIO = Path(__file__).parent / "data" / "count_0_99.mp3"  # 66.048 s
AUDIO_SECONDS = 66.048


_BASE_SETTINGS = LaneSettings(
    enabled=True,
    api_key="test-key",
    base_url="http://stub.invalid/v1",
    model="whisper-large-v3-turbo",
    language="en",
    usd_per_hour=0.04,
    monthly_cap_usd=1.0,
    max_episode_minutes=None,
)


def _settings(**overrides: Any) -> LaneSettings:
    return dataclasses.replace(_BASE_SETTINGS, **overrides)


def _job_row(job_id: str) -> ProcessingJob:
    db.session.expire_all()
    job = db.session.get(ProcessingJob, job_id)
    assert job is not None
    return job


def _act(name: str, **params):
    result = writer_client.action(name, params, wait=True)
    assert result is not None and result.success, getattr(result, "error", None)
    return result.data


# ------------------------------------------------------------------ routing


@pytest.mark.parametrize(
    ("manual", "settings", "spent", "seconds", "lane", "reason"),
    [
        (False, _settings(), 0.0, 600, LANE_LOCAL, "automatic job"),
        (True, _settings(enabled=False), 0.0, 600, LANE_LOCAL, "disabled"),
        (True, _settings(api_key=""), 0.0, 600, LANE_LOCAL, "no API key"),
        (True, _settings(api_key="  "), 0.0, 600, LANE_LOCAL, "no API key"),
        (True, _settings(monthly_cap_usd=0.0), 0.0, 600, LANE_LOCAL, "cap is $0"),
        (
            True,
            _settings(max_episode_minutes=30),
            0.0,
            31 * 60,
            LANE_LOCAL,
            "longer than the 30 min",
        ),
        (True, _settings(), 1.0, 600, LANE_LOCAL, "cap reached"),
        # 0.04 $/h: 2 h = $0.08; only $0.05 left of the cap.
        (True, _settings(), 0.95, 7200, LANE_LOCAL, "would exceed"),
        (True, _settings(), 0.95, 3600, LANE_CLOUD, "est. $0.0400"),
        (True, _settings(), 0.0, None, LANE_CLOUD, "length unknown"),
        (True, _settings(max_episode_minutes=30), 0.0, 30 * 60, LANE_CLOUD, "manual"),
    ],
)
def test_decide_lane(manual, settings, spent, seconds, lane, reason):
    decision = decide_lane(
        manual=manual, settings=settings, spent_usd=spent, audio_seconds=seconds
    )
    assert decision.lane == lane
    assert reason in decision.reason


def test_billing_applies_per_request_minimum():
    assert billed_seconds_for_chunks([3.0, 600.0]) == 10.0 + 600.0
    assert cost_usd(3600, 0.04) == pytest.approx(0.04)


def test_month_start_is_utc_calendar_month():
    assert month_start(datetime(2026, 10, 31, 23, 59, tzinfo=UTC)) == datetime(
        2026, 10, 1
    )


# ------------------------------------------------------------------ budget


def _reserve(app_seconds: float, cap: float, job_id: str = "j1"):
    return _act(
        "reserve_cloud_usage",
        job_id=job_id,
        post_guid="g1",
        model="m",
        audio_seconds=app_seconds,
        estimated_billed_seconds=app_seconds,
        usd_per_hour=0.04,
        cap_usd=cap,
    )


def test_reservations_are_capped_and_settled(app):
    with app.app_context():
        # $0.04 per hour; cap $0.05 -> the first hour fits, the second doesn't.
        first = _reserve(3600, cap=0.05)
        assert first["reserved"] is True
        assert month_spent_usd() == pytest.approx(0.04)  # reserved counts
        second = _reserve(3600, cap=0.05, job_id="j2")
        assert second["reserved"] is False
        assert CloudLaneUsage.query.count() == 1

        # Settling with the billed seconds replaces the estimate.
        _act(
            "settle_cloud_usage",
            usage_id=first["usage_id"],
            status="charged",
            billed_seconds=1800,
        )
        assert month_spent_usd() == pytest.approx(0.02)
        assert _reserve(900, cap=0.05, job_id="j3")["reserved"] is True


def test_failed_call_counts_only_billed_chunks(app):
    with app.app_context():
        usage = _reserve(3600, cap=1.0)
        _act(
            "settle_cloud_usage",
            usage_id=usage["usage_id"],
            status="failed",
            billed_seconds=0,
            error="boom",
        )
        assert month_spent_usd() == 0.0
        row = db.session.get(CloudLaneUsage, usage["usage_id"])
        assert row is not None
        assert row.status == "failed"
        assert row.cost_usd == 0.0


def test_previous_month_usage_is_not_counted(app):
    with app.app_context():
        db.session.add(
            CloudLaneUsage(
                post_guid="old",
                model="m",
                status="charged",
                audio_seconds=3600,
                estimated_usd=5.0,
                cost_usd=5.0,
                usd_per_hour=0.04,
                created_at=month_start().replace(year=month_start().year - 1),
            )
        )
        db.session.commit()
        assert month_spent_usd() == 0.0


# ------------------------------------------------------------------ queues


def _job(job_id: str, status: str, lane: str | None, created: int) -> ProcessingJob:
    return ProcessingJob(
        id=job_id,
        post_guid=f"g-{job_id}",
        status=status,
        lane=lane,
        created_at=datetime(2026, 1, 1, 0, 0, created),
    )


def test_lanes_dequeue_independently(app):
    with app.app_context():
        db.session.add_all(
            [
                _job("local-running", "running", None, 0),  # legacy NULL lane
                _job("local-next", "pending", LANE_LOCAL, 1),
                _job("cloud-a", "pending", LANE_CLOUD, 2),
                _job("cloud-b", "pending", LANE_CLOUD, 3),
                _job("cloud-c", "pending", LANE_CLOUD, 4),
            ]
        )
        db.session.commit()

        # Local runs one at a time; the NULL-lane running job counts as local.
        assert _act("dequeue_job", lane=LANE_LOCAL, max_running=1) == {"result": None}
        # Cloud is not blocked by the local job and runs up to 2.
        assert (
            _act("dequeue_job", lane=LANE_CLOUD, max_running=2)["job_id"] == "cloud-a"
        )
        assert (
            _act("dequeue_job", lane=LANE_CLOUD, max_running=2)["job_id"] == "cloud-b"
        )
        assert _act("dequeue_job", lane=LANE_CLOUD, max_running=2) == {"result": None}


# ------------------------------------------------------------------ cloud transcriber


def _store_settings(**overrides) -> None:
    values = {
        "enabled": True,
        "api_key": "test-key",
        "base_url": "http://stub.invalid/v1",
        "usd_per_hour": 0.04,
        "monthly_cap_usd": 1.0,
    }
    values.update(overrides)
    _act("update_cloud_lane_settings", **values)


class _FakeTranscriptions:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def create(self, **_kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("503 from provider")
        seg = TranscriptionSegment(
            id=0,
            seek=0,
            start=0.0,
            end=1.0,
            text="hello",
            tokens=[],
            temperature=0.0,
            avg_logprob=0.0,
            compression_ratio=0.0,
            no_speech_prob=0.0,
        )
        return SimpleNamespace(segments=[seg])


def _transcriber(fake: _FakeTranscriptions) -> BudgetedCloudTranscriber:
    t = BudgetedCloudTranscriber(load_lane_settings(), job_id="j1", post_guid="g1")
    t.openai_client = SimpleNamespace(audio=SimpleNamespace(transcriptions=fake))
    return t


def _copy_audio(tmp_path: Path) -> str:
    target = tmp_path / "ep.mp3"
    target.write_bytes(AUDIO.read_bytes())
    return str(target)


def test_cloud_transcriber_charges_billed_audio(app, tmp_path):
    with app.app_context():
        _store_settings()
        fake = _FakeTranscriptions()
        segments = _transcriber(fake).transcribe(_copy_audio(tmp_path))

        assert [s.text for s in segments] == ["hello"]
        assert fake.calls == 1
        row = CloudLaneUsage.query.one()
        assert row.status == "charged"
        assert row.audio_seconds == pytest.approx(AUDIO_SECONDS, abs=0.1)
        assert row.billed_seconds == pytest.approx(AUDIO_SECONDS, abs=0.5)
        assert row.cost_usd == pytest.approx(AUDIO_SECONDS / 3600 * 0.04, rel=0.01)


def test_cloud_transcriber_refuses_over_cap_without_calling_api(app, tmp_path):
    with app.app_context():
        # 66 s at $0.04/h is about $0.00073; cap below that.
        _store_settings(monthly_cap_usd=0.0005)
        fake = _FakeTranscriptions()
        t = _transcriber(fake)
        with pytest.raises(CloudLaneFallback, match="monthly cap"):
            t.transcribe(_copy_audio(tmp_path))
        assert fake.calls == 0
        assert t.fallback_reason == "would exceed the cloud monthly cap"
        assert CloudLaneUsage.query.count() == 0


def test_cloud_transcriber_failure_settles_and_falls_back(app, tmp_path):
    with app.app_context():
        _store_settings()
        fake = _FakeTranscriptions(fail=True)
        t = _transcriber(fake)
        with pytest.raises(CloudLaneFallback, match="503"):
            t.transcribe(_copy_audio(tmp_path))
        row = CloudLaneUsage.query.one()
        assert row.status == "failed"
        assert row.cost_usd == 0.0
        assert "503" in (row.error or "")
        assert (t.fallback_reason or "").startswith("cloud transcription failed")


# ------------------------------------------------------------------ jobs manager


@pytest.fixture
def manager(app):
    """A JobsManager without its worker threads, on the test app context."""
    jm = object.__new__(JobsManager)
    jm._work_event = mock.Mock()
    jm._cloud_work_event = mock.Mock()
    with mock.patch(
        "app.jobs_manager._scheduler_app_context", side_effect=app.app_context
    ):
        yield jm


def _post_with_job(status: str = "pending", lane: str | None = None) -> str:
    feed = Feed(title="F", rss_url="https://ex.example.com/f")
    db.session.add(feed)
    db.session.commit()
    post = Post(
        feed_id=feed.id,
        guid="g1",
        download_url="https://ex.example.com/1.mp3",
        title="Ep",
        duration=1800,
        whitelisted=True,
    )
    db.session.add(post)
    db.session.add(ProcessingJob(id="j1", post_guid="g1", status=status, lane=lane))
    db.session.commit()
    return "j1"


def test_assign_lane_routes_manual_to_cloud_and_never_downgrades(app, manager):
    with app.app_context():
        _store_settings()
        job_id = _post_with_job()

        manager._assign_lane("g1", job_id, manual=False)
        assert _job_row(job_id).lane == LANE_LOCAL

        manager._assign_lane("g1", job_id, manual=True)
        job = _job_row(job_id)
        assert job.lane == LANE_CLOUD
        assert "est. $0.0200" in (job.lane_reason or "")  # 30 min at $0.04/h

        # A later automatic trigger for the same pending job keeps it in cloud.
        manager._assign_lane("g1", job_id, manual=False)
        assert _job_row(job_id).lane == LANE_CLOUD


def test_assign_lane_keeps_manual_local_when_cloud_disabled(app, manager):
    with app.app_context():
        job_id = _post_with_job()  # no settings row -> disabled
        manager._assign_lane("g1", job_id, manual=True)
        job = _job_row(job_id)
        assert job.lane == LANE_LOCAL
        assert job.lane_reason == "cloud lane disabled"


class _FailingCloudProcessor:
    """Stands in for PodcastProcessor whose cloud transcription failed: the real
    processor marks the job failed and re-raises."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id

    def process(self, post, job_id, cancel_callback=None):
        _act(
            "update_job_status",
            job_id=job_id,
            status="failed",
            step=2,
            step_name="Transcribing",
            error_message="cloud transcription failed: 503",
        )
        raise RuntimeError("cloud transcription failed: 503")


def test_failed_cloud_job_is_requeued_locally(app, manager):
    with app.app_context():
        job_id = _post_with_job(status="running", lane=LANE_CLOUD)
        transcriber = SimpleNamespace(fallback_reason="cloud transcription failed: 503")
        with mock.patch(
            "app.jobs_manager._processor_for",
            return_value=(_FailingCloudProcessor(job_id), transcriber),
        ):
            manager._process_job(job_id, "g1", lane=LANE_CLOUD)

        job = _job_row(job_id)
        assert job.status == "pending"
        assert job.lane == LANE_LOCAL
        assert "cloud fallback" in (job.lane_reason or "")
        assert job.error_message is None
        manager._work_event.set.assert_called()  # local worker woken


def test_cancelled_cloud_job_is_not_requeued(app, manager):
    with app.app_context():
        job_id = _post_with_job(status="cancelled", lane=LANE_CLOUD)
        assert _act("requeue_job_local", job_id=job_id, reason="x") == {
            "requeued": False
        }


def test_cloud_jobs_do_not_take_the_local_processing_lock(app, manager):
    """A cloud job must run while a local job holds the processing lock."""
    with app.app_context():
        job_id = _post_with_job(status="running", lane=LANE_CLOUD)
    ran = threading.Event()

    class _Probe:
        def process(self, post, job_id, cancel_callback=None):
            ran.set()

    def run_cloud_job() -> None:
        with app.app_context():
            manager._process_job(job_id, "g1", lane=LANE_CLOUD)

    with (
        JobsManager._global_processing_lock,  # a local job is running
        mock.patch("app.jobs_manager._processor_for", return_value=(_Probe(), None)),
    ):
        worker = threading.Thread(target=run_cloud_job, daemon=True)
        worker.start()
        assert ran.wait(5), "cloud job blocked on the local processing lock"
        worker.join(5)


# ------------------------------------------------------------------ API


@pytest.fixture
def lane_client(app):
    from app.routes.lane_routes import lane_bp

    app.register_blueprint(lane_bp)
    return app.test_client()


def test_settings_api_never_returns_the_key(app, lane_client):
    with app.app_context():
        resp = lane_client.put(
            "/api/lanes/settings",
            json={
                "enabled": True,
                "api_key": "gsk_secret_value_123",
                "monthly_cap_usd": 2,
            },
        )
        assert resp.status_code == 200, resp.data
        body = resp.get_json()
        assert body["api_key_set"] is True
        assert "gsk_secret_value_123" not in resp.get_data(as_text=True)
        # Omitting api_key keeps it; the stored value is the real one.
        lane_client.put("/api/lanes/settings", json={"monthly_cap_usd": 3})
        assert load_lane_settings().api_key == "gsk_secret_value_123"
        assert load_lane_settings().monthly_cap_usd == 3.0


@pytest.mark.parametrize(
    "payload",
    [
        {"usd_per_hour": -1},
        {"monthly_cap_usd": "5"},
        {"enabled": "yes"},
        {"max_episode_minutes": 0},
        {"base_url": " "},
        {"surprise": 1},
    ],
)
def test_settings_api_rejects_bad_values(app, lane_client, payload):
    with app.app_context():
        assert lane_client.put("/api/lanes/settings", json=payload).status_code == 400


def test_status_reports_spend_and_queues(app, lane_client):
    with app.app_context():
        _store_settings(monthly_cap_usd=2.0)
        _reserve(3600, cap=2.0)
        db.session.add(_job("c", "pending", LANE_CLOUD, 0))
        db.session.add(_job("l", "running", None, 1))
        db.session.commit()
        body = lane_client.get("/api/lanes/status").get_json()
    assert body["cloud_available"] is True
    assert body["month_spent_usd"] == pytest.approx(0.04)
    assert body["monthly_cap_usd"] == 2.0
    assert body["queues"]["cloud"]["pending"] == 1
    assert body["queues"]["local"]["running"] == 1
