"""Stage pipeline failure paths: LLM slot contention, failed hand-off writes,
lost dequeue replies, restart loops and startup ordering.

Same harness as test_pipeline.py (real PipelineWorkers threads, real writer
actions via the in-process fallback, real PodcastProcessor). The LLM tests also
run the real AdClassifier; only litellm.completion is replaced.
"""

from __future__ import annotations

import threading
import time
from typing import Any
from unittest import mock

import litellm
import pytest

import app as app_module
from app.extensions import db
from app.jobs_manager import JobsManager
from app.lanes import LANE_CLOUD
from app.models import ModelCall, Post, ProcessingJob, TranscriptSegment
from app.pipeline import STAGE_LLM, STAGE_TRANSCRIBE, PipelineSettings
from app.pipeline_workers import PipelineWorkers
from app.writer.client import writer_client
from app.writer.protocol import WriteResult
from podcast_processor import ad_classifier as ad_classifier_module
from podcast_processor import llm_concurrency_limiter
from shared import defaults as DEFAULTS
from shared.test_utils import create_standard_test_config

from .pipeline_harness import (
    Harness,
    act,
    add_job,
    add_post,
    get_job,
    job_statuses,
)

# ------------------------------------------------------------------ helpers


def store_segments(post: Post, count: int) -> None:
    """A stored transcript of ``count`` segments (Whisper ModelCall + rows)."""
    db.session.add(
        ModelCall(
            post_id=post.id,
            model_name="stub_whisper",
            prompt="Whisper transcription job",
            first_segment_sequence_num=0,
            last_segment_sequence_num=count - 1,
            status="success",
        )
    )
    db.session.add_all(
        TranscriptSegment(
            post_id=post.id,
            sequence_num=i,
            start_time=i * 5.0,
            end_time=(i + 1) * 5.0,
            text=f"[{post.guid}] segment {i}",
        )
        for i in range(count)
    )
    db.session.commit()


class FakeCompletion:
    """Stands in for litellm.completion: slow, says "no ads", records how many
    calls ran at once and which post (transcript marker) each call was for."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self._lock = threading.Lock()
        self._active = 0
        self.max_active = 0
        self.calls = 0
        self.post_spans: dict[str, list[float]] = {}

    def __call__(self, **kwargs: Any) -> Any:
        prompt = str(kwargs.get("messages"))
        with self._lock:
            self._active += 1
            self.calls += 1
            self.max_active = max(self.max_active, self._active)
        start = time.monotonic()
        try:
            time.sleep(self.seconds)
        finally:
            with self._lock:
                self._active -= 1
                for title in _TITLES:
                    if title in prompt:
                        span = self.post_spans.setdefault(title, [start, start])
                        span[1] = time.monotonic()
        return litellm.ModelResponse(
            choices=[
                {"message": {"role": "assistant", "content": '{"ad_segments": []}'}}
            ]
        )

    def max_posts_in_llm_at_once(self) -> int:
        spans = list(self.post_spans.values())
        return max(sum(1 for s, e in spans if s <= start < e) for start, _ in spans)


_TITLES = {f"[p{i}]" for i in range(4)}  # transcript markers per post


@pytest.fixture
def fresh_llm_limiter(monkeypatch):
    monkeypatch.setattr(llm_concurrency_limiter, "_CONCURRENCY_LIMITER", None)


# ------------------------------------------------------------------ B1: LLM slot


@pytest.mark.parametrize("cap_workers", [True, False])
def test_real_classifier_classifies_every_chunk_with_one_llm_slot(
    papp, monkeypatch, fresh_llm_limiter, cap_workers
):
    """LLM_MAX_CONCURRENT_CALLS=1 with 4 LLM-stage workers.

    cap_workers=True: the pool runs one LLM-stage worker (start-up cap).
    cap_workers=False: the cap is bypassed, as when the limit is lowered in the
    settings UI after start-up; 4 episodes then contend for one slot, and a
    waiter must block, not drop its chunk.
    """
    config = create_standard_test_config(
        num_segments_to_input_to_prompt=3, max_overlap_segments=0
    )
    config.llm_max_concurrent_calls = 1
    config.llm_enable_token_rate_limiting = False
    config.enable_boundary_refinement = False
    with papp.app_context():
        ids = []
        for i in range(4):
            post = add_post(f"p{i}")
            store_segments(post, 9)  # 3 chunks of 3 segments
            ids.append(add_job(f"j{i}", f"p{i}", order=i, stage=STAGE_LLM))
    fake = FakeCompletion(seconds=0.15)
    monkeypatch.setattr(ad_classifier_module.litellm, "completion", fake)
    # Old behaviour gave up after this long; 0.05 s << 0.15 s calls.
    monkeypatch.setattr(ad_classifier_module, "LLM_SLOT_WAIT_LOG_SECONDS", 0.05)

    h = Harness(
        papp,
        llm_workers=4,
        llm_call_limit=1 if cap_workers else 0,
        real_classifier=True,
        config=config,
    )
    h.run_until_done(ids, timeout=60)

    assert job_statuses(papp, ids) == ["completed"] * 4
    with papp.app_context():
        llm_calls = ModelCall.query.filter(ModelCall.model_name != "stub_whisper").all()
        assert sorted(c.status for c in llm_calls) == ["success"] * 12, [
            (c.post_id, c.first_segment_sequence_num, c.status, c.error_message)
            for c in llm_calls
        ]
        # Each chunk got its slot on the first attempt: waiting for the slot
        # is not a failed attempt (no slot timeout, no retry backoff).
        assert [c.retry_attempts for c in llm_calls] == [1] * 12
        for post in Post.query.all():
            covered = sorted(
                (c.first_segment_sequence_num, c.last_segment_sequence_num)
                for c in llm_calls
                if c.post_id == post.id
            )
            assert covered == [(0, 2), (3, 5), (6, 8)], (post.guid, covered)
    assert fake.calls == 12
    assert fake.max_active == 1  # the one slot was honoured
    if cap_workers:
        assert fake.max_posts_in_llm_at_once() == 1
    else:  # the contention this test is about really happened
        assert fake.max_posts_in_llm_at_once() > 1


def logged(mock_method: mock.Mock) -> str:
    """Formatted text of every call to a patched logger method."""
    return "\n".join(c.args[0] % c.args[1:] for c in mock_method.call_args_list)


def test_llm_pool_is_capped_at_the_llm_call_limit(papp):
    with papp.app_context():
        for i in range(3):
            add_post(f"p{i}")
            add_job(f"j{i}", f"p{i}", order=i, stage=STAGE_LLM)
    workers = PipelineWorkers(
        PipelineSettings(1, 4, 0, 1),
        app_context=papp.app_context,
        get_run_id=lambda: None,
        status_manager=mock.Mock(),
        llm_call_limit=lambda: 1,
    )
    workers._stop_event.set()  # threads exit at once; only sizing is checked
    with mock.patch("app.pipeline_workers.logger.warning") as warning:
        workers.start()
    workers.stop()
    llm_pool = next(p for p in workers.pools if p.stage == STAGE_LLM)
    assert llm_pool.size == 1
    assert len(llm_pool.threads) == 1
    assert "below PODLY_LLM_WORKERS=4: running 1 LLM-stage worker" in logged(warning)
    # The writer's max_running follows the capped size too.
    assert workers.claim(llm_pool) is not None
    assert workers.claim(llm_pool) is None


def test_slot_timeout_is_retryable_not_permanent(papp):
    with papp.app_context():
        classifier = ad_classifier_module.AdClassifier(create_standard_test_config())
        err = llm_concurrency_limiter.LLMSlotTimeoutError("no slot")
        assert classifier._is_retryable_error(err) is True


def test_limiter_is_created_once_under_concurrent_first_use(fresh_llm_limiter):
    created: list[Any] = []
    real_init = llm_concurrency_limiter.LLMConcurrencyLimiter.__init__

    def slow_init(self: Any, max_concurrent_calls: int) -> None:
        time.sleep(0.05)  # widen the check-then-create window
        real_init(self, max_concurrent_calls)
        created.append(self)

    got: list[Any] = []
    barrier = threading.Barrier(4)

    def first_use() -> None:
        barrier.wait()
        got.append(llm_concurrency_limiter.get_concurrency_limiter(1))

    with mock.patch.object(
        llm_concurrency_limiter.LLMConcurrencyLimiter, "__init__", slow_init
    ):
        threads = [threading.Thread(target=first_use) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
    assert len(created) == 1
    assert len({id(x) for x in got}) == 1


# ------------------------------------------------------------------ M1: hand-off


def _intercept(monkeypatch, hook):
    """Route writer_client.action through ``hook(name, params, real)``."""
    real = writer_client.action

    def action(name: str, params: dict[str, Any], wait: bool = True) -> Any:
        return hook(name, params, lambda: real(name, params, wait=wait))

    monkeypatch.setattr(writer_client, "action", action)
    monkeypatch.setattr("app.pipeline_recovery.HANDOFF_RETRY_SECONDS", 0.01)


def test_failed_handoff_write_fails_the_job_and_frees_the_transcriber(
    papp, monkeypatch
):
    with papp.app_context():
        ids = []
        for i in range(3):
            add_post(f"p{i}")
            ids.append(add_job(f"j{i}", f"p{i}", order=i))
    attempts = {"n": 0}

    def hook(name: str, params: dict[str, Any], real: Any) -> Any:
        if name == "advance_job_stage" and params["job_id"] == "j0":
            attempts["n"] += 1
            return WriteResult("x", False, error="database is locked")
        return real()

    _intercept(monkeypatch, hook)
    h = Harness(papp, whisper_s=0.1, llm_s=0.1)
    with mock.patch("app.pipeline_recovery.logger.error") as error:
        h.run_until_done(ids, timeout=20)

    assert job_statuses(papp, ids) == ["failed", "completed", "completed"]
    assert attempts["n"] == 3  # retried before giving up
    with papp.app_context():
        assert "hand-off failed" in (get_job("j0").error_message or "")
    whispered = sorted(g for k, g, *_ in h.timeline.spans if k == "whisper")
    assert whispered == ["p0", "p1", "p2"]
    assert "advance_job_stage for job j0 failed after 3 attempts" in logged(error)


def test_handoff_write_that_fails_once_is_retried(papp, monkeypatch):
    with papp.app_context():
        add_post("p0")
        add_job("j0", "p0")
    failures = {"left": 1}

    def hook(name: str, params: dict[str, Any], real: Any) -> Any:
        if name == "advance_job_stage" and failures["left"]:
            failures["left"] -= 1
            raise TimeoutError("Writer service did not respond")
        return real()

    _intercept(monkeypatch, hook)
    h = Harness(papp, whisper_s=0.05, llm_s=0.05)
    h.run_until_done(["j0"], timeout=20)
    assert job_statuses(papp, ["j0"]) == ["completed"]
    assert len(h.timeline.of("llm", "p0")) == 1


# ------------------------------------------------------------------ m3: dequeue


def test_lost_dequeue_reply_does_not_orphan_a_running_job(papp, monkeypatch):
    """The writer ran dequeue_job (job now running) but the reply timed out."""
    with papp.app_context():
        ids = []
        for i in range(3):
            add_post(f"p{i}")
            ids.append(add_job(f"j{i}", f"p{i}", order=i))
    lost = {"done": False}

    def hook(name: str, params: dict[str, Any], real: Any) -> Any:
        if (
            name == "dequeue_job"
            and params["stage"] == STAGE_TRANSCRIBE
            and not lost["done"]
        ):
            result = real()
            if result and result.data and result.data.get("job_id"):
                lost["done"] = True
                raise TimeoutError("Writer service did not respond")
            return result
        return real()

    _intercept(monkeypatch, hook)
    h = Harness(papp, whisper_s=0.05, llm_s=0.05)
    h.run_until_done(ids, timeout=20)
    assert lost["done"]
    assert job_statuses(papp, ids) == ["completed"] * 3


def test_orphan_sweep_leaves_jobs_this_process_runs(papp):
    with papp.app_context():
        add_post("mine")
        add_job("mine", "mine", status="running", stage=STAGE_TRANSCRIBE)
        add_post("lost")
        add_job("lost", "lost", status="running", stage=STAGE_TRANSCRIBE)
        add_post("llm")
        add_job("llm", "llm", status="running", stage=STAGE_LLM)
        data = act(
            "requeue_orphaned_jobs",
            stage=STAGE_TRANSCRIBE,
            lane="local",
            owned_post_guids=["mine"],
        )
        assert data["job_ids"] == ["lost"]
        db.session.expire_all()
        assert [get_job(j).status for j in ("mine", "lost", "llm")] == [
            "running",
            "pending",
            "running",  # another pool: not swept
        ]


def test_recovery_actions_are_registered_with_the_writer_service(papp):
    from app.writer.executor import CommandExecutor

    registered = CommandExecutor(papp).actions
    for name in (
        "advance_job_stage",
        "fail_job_if_running",
        "requeue_orphaned_jobs",
        "requeue_interrupted_jobs",
        "dequeue_job",
    ):
        assert name in registered, name


# ------------------------------------------------------------------ M2: restarts


def _restart(papp) -> dict[str, Any]:
    jm = object.__new__(JobsManager)
    jm._workers = mock.Mock()
    with mock.patch("app.jobs_manager._scheduler_app_context", papp.app_context):
        return jm.requeue_interrupted_jobs()


def test_a_job_interrupted_by_every_restart_fails_after_the_limit(papp):
    assert DEFAULTS.PIPELINE_MAX_RESTART_REQUEUES == 2
    with papp.app_context():
        add_post("poison")
        add_job("poison", "poison", status="running", stage=STAGE_LLM)
        add_post("other")
        add_job("other", "other", status="running", stage=STAGE_TRANSCRIBE)

    outcomes = []
    for restart in range(3):
        result = _restart(papp)
        outcomes.append(result["failed_jobs"])
        with papp.app_context():
            db.session.expire_all()
            poison = get_job("poison")
            if restart < 2:
                assert poison.status == "pending"
                assert poison.restart_requeues == restart + 1
                poison.status = "running"  # it got claimed and killed us again
                db.session.commit()
            # "other" finishes fine after the first restart.
            other = get_job("other")
            if restart == 0:
                assert other.status == "pending"
                other.status = "completed"
                db.session.commit()

    assert outcomes == [[], [], ["poison"]]
    with papp.app_context():
        db.session.expire_all()
        poison = get_job("poison")
        assert poison.status == "failed"
        assert poison.completed_at is not None
        assert "3 restarts" in (poison.error_message or "")
        assert get_job("other").status == "completed"


# ------------------------------------------------------------------ m1: startup


class RecordingWorkers(PipelineWorkers):
    """Real PipelineWorkers that records the job table when start() runs."""

    seen_at_start: dict[str, tuple[str, str | None]] = {}

    def start(self) -> None:
        with self._app_context():
            db.session.expire_all()
            RecordingWorkers.seen_at_start = {
                j.id: (j.status, j.lane) for j in ProcessingJob.query.all()
            }
        super().start()


def test_startup_requeues_before_any_worker_can_claim(papp, monkeypatch):
    with papp.app_context():
        add_post("interrupted")
        add_job(
            "interrupted",
            "interrupted",
            status="running",
            stage=STAGE_TRANSCRIBE,
            lane=LANE_CLOUD,
        )
        add_post("queued")
        add_job("queued", "queued", order=1)

    monkeypatch.setattr("app.jobs_manager._scheduler_app_context", papp.app_context)
    monkeypatch.setattr("app.jobs_manager.PipelineWorkers", RecordingWorkers)
    monkeypatch.setattr(
        "app.jobs_manager.load_pipeline_settings",
        lambda: PipelineSettings(1, 1, 0, 1),
    )
    for name in (
        "_clear_scheduler_jobstore",
        "setup_scheduler",
        "add_background_job",
        "schedule_cleanup_job",
    ):
        monkeypatch.setattr(app_module, name, mock.Mock())
    managers: list[JobsManager] = []

    def make_manager() -> JobsManager:
        jm = JobsManager()
        managers.append(jm)
        return jm

    monkeypatch.setattr(app_module, "get_jobs_manager", make_manager)
    # The worker threads must not run real processing on these rows.
    monkeypatch.setattr(PipelineWorkers, "claim", lambda self, pool: None)
    try:
        app_module._start_scheduler_and_jobs(papp)
    finally:
        for jm in managers:
            jm._workers.stop(timeout=1)

    assert len(managers) == 1
    assert managers[0].startup_requeue["requeued_jobs"] == 1
    # By the time workers start, nothing is left running and the cloud job was
    # moved off the paid lane; the queued job was kept, not cleared.
    assert RecordingWorkers.seen_at_start == {
        "interrupted": ("pending", None),
        "queued": ("pending", None),
    }


# ------------------------------------------------------------------ m5: UI counts


def test_stage_counts_are_not_capped_by_the_jobs_list(papp):
    from app.routes.lane_routes import lane_bp

    papp.register_blueprint(lane_bp)
    with papp.app_context():
        for i in range(150):
            db.session.add(
                ProcessingJob(
                    id=f"w{i}", post_guid=f"w{i}", status="pending", stage=STAGE_LLM
                )
            )
        db.session.add(ProcessingJob(id="t", post_guid="t", status="running"))
        db.session.add(ProcessingJob(id="d", post_guid="d", status="completed"))
        db.session.commit()
        body = papp.test_client().get("/api/lanes/status").get_json()
    assert body["stages"] == {
        "transcribe": {"pending": 0, "running": 1},
        "llm": {"pending": 150, "running": 0},
    }
