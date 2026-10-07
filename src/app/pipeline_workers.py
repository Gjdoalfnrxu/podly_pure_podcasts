"""Worker pools for the stage pipeline (see app/pipeline.py).

One pool per (stage, lane): local transcribe, cloud transcribe, LLM. A pool of
N threads runs at most N jobs; the dequeue writer action also refuses to go
over N running jobs of that pool and never claims a post that is running or
that a thread here is still busy with.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.cloud_lane import CloudLaneFallback, build_cloud_processor
from app.db_guard import db_guard, reset_session
from app.extensions import db as _db
from app.lanes import LANE_CLOUD, LANE_LOCAL
from app.models import Post, ProcessingJob
from app.pipeline import AUDIO_CUT_SLOTS, STAGE_LLM, STAGE_TRANSCRIBE, PipelineSettings
from app.writer.client import writer_client
from podcast_processor.podcast_processor import (
    NeedsTranscription,
    PodcastProcessor,
    ProcessorException,
)
from podcast_processor.processing_status_manager import ProcessingStatusManager

logger = logging.getLogger("global_logger")

# (processor, cloud transcriber or None) for one stage run of one job.
ProcessorFactory = Callable[[str, str, str, str], tuple[Any, Any]]

_IDLE_POLL_SECONDS = 5.0


def default_processor_factory(
    stage: str, lane: str, job_id: str, post_guid: str
) -> tuple[Any, Any]:
    """A fresh processor per stage run, so no state is shared between threads.

    Cloud transcribe runs get the budgeted cloud transcriber; everything else
    uses the configured transcriber (the LLM stage never calls it).
    """
    if stage == STAGE_TRANSCRIBE and lane == LANE_CLOUD:
        try:
            return build_cloud_processor(job_id, post_guid)
        except Exception as exc:  # noqa: BLE001 - never lose the job
            failed = _CloudSetupFailed(f"cloud setup failed: {exc}"[:300])
            return failed, failed
    from app.runtime_config import config

    return PodcastProcessor(config), None


class _CloudSetupFailed:
    """Stands in for a cloud processor that could not be built: the job fails
    and, because ``fallback_reason`` is set, is re-queued on the local lane."""

    def __init__(self, reason: str) -> None:
        self.fallback_reason = reason

    def process(
        self,
        post: Any,
        job_id: str,
        cancel_callback: Any = None,
        stage: str | None = None,
    ) -> None:
        raise CloudLaneFallback(self.fallback_reason)


def _warn_if_llm_calls_capped(settings: PipelineSettings) -> None:
    """LLM_MAX_CONCURRENT_CALLS is one process-wide semaphore shared by all LLM
    stage workers; below the worker count it serialises their calls."""
    from app.runtime_config import config

    calls = int(getattr(config, "llm_max_concurrent_calls", 0) or 0)
    if 0 < calls < settings.llm_workers:
        logger.warning(
            "[PIPELINE] LLM_MAX_CONCURRENT_CALLS=%d is below PODLY_LLM_WORKERS=%d: "
            "only %d LLM calls run at once across all episodes",
            calls,
            settings.llm_workers,
            calls,
        )


@dataclass
class StagePool:
    stage: str
    lane: str
    size: int
    wake_event: threading.Event = field(default_factory=threading.Event)
    threads: list[threading.Thread] = field(default_factory=list)

    @property
    def name(self) -> str:
        if self.stage == STAGE_LLM:
            return "llm"
        return f"transcribe-{self.lane}"


class PipelineWorkers:
    def __init__(
        self,
        settings: PipelineSettings,
        *,
        app_context: Callable[[], Any],
        get_run_id: Callable[[], str | None],
        status_manager: ProcessingStatusManager,
        processor_factory: ProcessorFactory = default_processor_factory,
    ) -> None:
        self.settings = settings
        self._app_context = app_context
        self._get_run_id = get_run_id
        self._status_manager = status_manager
        self._processor_factory = processor_factory
        self._stop_event = threading.Event()
        # Serialises claim + in-flight registration in this process. In
        # production the writer already serialises dequeue; this also covers
        # the in-process writer fallback used by tests.
        self._claim_lock = threading.Lock()
        self._inflight: set[str] = set()
        self.pools = [
            StagePool(STAGE_TRANSCRIBE, LANE_LOCAL, settings.transcribe_workers),
            StagePool(STAGE_TRANSCRIBE, LANE_CLOUD, settings.cloud_workers),
            StagePool(STAGE_LLM, LANE_LOCAL, settings.llm_workers),
        ]

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        AUDIO_CUT_SLOTS.reset(self.settings.audio_cut_concurrency)
        _warn_if_llm_calls_capped(self.settings)
        for pool in self.pools:
            for i in range(pool.size):
                thread = threading.Thread(
                    target=self._loop,
                    args=(pool,),
                    name=f"pipeline-{pool.name}-{i}",
                    daemon=True,
                )
                pool.threads.append(thread)
                thread.start()
        logger.info(
            "[PIPELINE] started: transcribe-local=%d transcribe-cloud=%d llm=%d "
            "audio-cut=%d",
            self.settings.transcribe_workers,
            self.settings.cloud_workers,
            self.settings.llm_workers,
            self.settings.audio_cut_concurrency,
        )

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        self.wake()
        for pool in self.pools:
            for thread in pool.threads:
                thread.join(timeout)

    def wake(self, stage: str | None = None, lane: str | None = None) -> None:
        for pool in self.pools:
            if stage is not None and pool.stage != stage:
                continue
            if lane is not None and pool.lane != lane:
                continue
            pool.wake_event.set()

    def inflight_posts(self) -> set[str]:
        with self._claim_lock:
            return set(self._inflight)

    # ------------------------------------------------------------ loop
    def _loop(self, pool: StagePool) -> None:
        while not self._stop_event.is_set():
            try:
                claimed = self.claim(pool)
                if claimed is None:
                    if pool.wake_event.wait(_IDLE_POLL_SECONDS):
                        pool.wake_event.clear()
                    continue
                job_id, post_guid = claimed
                self.run_stage(job_id, post_guid, pool.stage, pool.lane)
            except Exception as exc:
                logger.error(
                    "Pipeline worker %s error: %s", pool.name, exc, exc_info=True
                )
                reset_session(_db.session, logger, f"pipeline_{pool.name}", exc)

    def claim(self, pool: StagePool) -> tuple[str, str] | None:
        """Claim the next job for ``pool`` and mark its post in flight."""
        with self._claim_lock, self._app_context():
            try:
                result = writer_client.action(
                    "dequeue_job",
                    {
                        "run_id": self._get_run_id(),
                        "stage": pool.stage,
                        "lane": pool.lane,
                        "max_running": pool.size,
                        "exclude_post_guids": sorted(self._inflight),
                    },
                    wait=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Error dequeuing job for %s: %s", pool.name, exc)
                return None
            if not (result and result.success and result.data):
                return None
            data = result.data
            if not data.get("job_id"):
                return None
            self._inflight.add(data["post_guid"])
        logger.info(
            "[JOB_DEQUEUE] pool=%s job_id=%s post_guid=%s",
            pool.name,
            data["job_id"],
            data["post_guid"],
        )
        return data["job_id"], data["post_guid"]

    # ------------------------------------------------------------ one stage run
    def run_stage(self, job_id: str, post_guid: str, stage: str, lane: str) -> None:
        """Run one stage of a claimed job, then hand it on or let it finish.

        Always releases the post's in-flight mark, after any hand-off write, so
        the next stage cannot claim the job while this thread still holds it.
        """
        outcome = "error"
        try:
            outcome, cloud_transcriber = self._execute(job_id, post_guid, stage, lane)
            if cloud_transcriber is not None and cloud_transcriber.fallback_reason:
                self._fall_back_to_local(job_id, cloud_transcriber.fallback_reason)
            elif outcome == "transcribed":
                self._advance(
                    job_id, STAGE_LLM, 2, "Transcribed; waiting for ad detection", 50.0
                )
            elif outcome == "needs_transcription":
                self._advance(
                    job_id, STAGE_TRANSCRIBE, 1, "No transcript; re-queued", 25.0
                )
        finally:
            with self._claim_lock:
                self._inflight.discard(post_guid)
        if outcome == "transcribed":
            self.wake(STAGE_LLM)
        elif outcome in ("needs_transcription", "fallback"):
            self.wake(STAGE_TRANSCRIBE)
        else:
            self.wake()  # a post that was blocked on this one may be eligible

    def _execute(
        self, job_id: str, post_guid: str, stage: str, lane: str
    ) -> tuple[str, Any]:
        outcome = "done"
        cloud_transcriber = None
        with self._app_context():
            with db_guard("process_job", _db.session, logger):
                try:
                    try:
                        _db.session.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    _db.session.expire_all()

                    worker_post = Post.query.filter_by(guid=post_guid).first()
                    if not worker_post:
                        logger.error(
                            "Post with GUID %s not found; failing job %s",
                            post_guid,
                            job_id,
                        )
                        job = _db.session.get(ProcessingJob, job_id)
                        if job:
                            self._status_manager.update_job_status(
                                job,
                                "failed",
                                job.current_step or 0,
                                "Post not found",
                                0.0,
                            )
                        return outcome, None

                    def _cancelled() -> bool:
                        _db.session.expire_all()
                        current_job = _db.session.get(ProcessingJob, job_id)
                        return current_job is None or current_job.status == "cancelled"

                    processor, cloud_transcriber = self._processor_factory(
                        stage, lane, job_id, post_guid
                    )
                    result = processor.process(
                        worker_post,
                        job_id=job_id,
                        cancel_callback=_cancelled,
                        stage=stage,
                    )
                    if result is None and stage == STAGE_TRANSCRIBE:
                        outcome = "transcribed"
                except NeedsTranscription as exc:
                    logger.warning("Job %s: %s", job_id, exc)
                    outcome = "needs_transcription"
                except ProcessorException as exc:
                    logger.info(
                        "Job %s finished with processor exception: %s", job_id, exc
                    )
                except Exception as exc:
                    logger.error(
                        "Unexpected error in job %s: %s", job_id, exc, exc_info=True
                    )
                    self._mark_failed(job_id, exc)
                finally:
                    try:
                        _db.session.rollback()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        _db.session.remove()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to remove session after job: %s", exc)
        if cloud_transcriber is not None and cloud_transcriber.fallback_reason:
            outcome = "fallback"
        return outcome, cloud_transcriber

    def _mark_failed(self, job_id: str, exc: Exception) -> None:
        try:
            _db.session.expire_all()
            failed_job = _db.session.get(ProcessingJob, job_id)
            if failed_job and failed_job.status not in [
                "completed",
                "cancelled",
                "failed",
            ]:
                self._status_manager.update_job_status(
                    failed_job,
                    "failed",
                    failed_job.current_step or 0,
                    f"Job execution failed: {exc}",
                    failed_job.progress_percentage or 0.0,
                )
        except Exception as cleanup_error:
            logger.error(
                "Failed to update job status after error: %s",
                cleanup_error,
                exc_info=True,
            )

    def _advance(
        self, job_id: str, stage: str, step: int, step_name: str, progress: float
    ) -> None:
        with self._app_context():
            result = writer_client.action(
                "advance_job_stage",
                {
                    "job_id": job_id,
                    "stage": stage,
                    "step": step,
                    "step_name": step_name,
                    "progress": progress,
                },
                wait=True,
            )
        advanced = bool(
            result and result.success and (result.data or {}).get("advanced")
        )
        logger.info(
            "[PIPELINE] job_id=%s -> stage=%s advanced=%s", job_id, stage, advanced
        )

    def _fall_back_to_local(self, job_id: str, reason: str) -> None:
        with self._app_context():
            result = writer_client.action(
                "requeue_job_local",
                {"job_id": job_id, "reason": f"cloud fallback: {reason}"[:500]},
                wait=True,
            )
        requeued = bool(
            result and result.success and (result.data or {}).get("requeued")
        )
        logger.warning(
            "[LANE] cloud job %s fell back to local (%s), requeued=%s",
            job_id,
            reason,
            requeued,
        )
