import logging
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Any, cast

from sqlalchemy import case

from app.cloud_lane import choose_lane
from app.extensions import db as _db
from app.extensions import scheduler
from app.feeds import refresh_feed
from app.job_manager import JobManager as SingleJobManager
from app.lanes import LANE_LOCAL, LaneDecision
from app.models import Feed, JobsManagerRun, Post, ProcessingJob
from app.pipeline import (
    STAGE_LLM,
    STAGE_TRANSCRIBE,
    load_pipeline_settings,
    priority_rank,
)
from app.pipeline_workers import PipelineWorkers
from app.writer.client import writer_client
from podcast_processor.processing_status_manager import ProcessingStatusManager
from podcast_processor.transcription_manager import TranscriptionManager
from shared.processing_paths import find_existing_processed_audio_path

logger = logging.getLogger("global_logger")


def _scheduler_app_context() -> Any:
    scheduler_app = scheduler.app
    if scheduler_app is None:
        raise RuntimeError("Scheduler app is not initialized")
    return scheduler_app.app_context()


class JobsManager:
    """
    Centralized manager for starting, tracking, listing, and cancelling
    podcast processing jobs.

    Owns the stage worker pools (app/pipeline_workers.py) and coordinates with
    ProcessingStatusManager.
    """

    def __init__(self) -> None:
        # Status manager for DB interactions
        self._status_manager = ProcessingStatusManager(
            db_session=_db.session, logger=logger
        )

        # Track the singleton run id with thread-safe access
        self._run_lock = Lock()
        self._run_id: str | None = None

        # Stage worker pools: transcribe (local + cloud lane) and LLM.
        self._workers = PipelineWorkers(
            load_pipeline_settings(),
            app_context=_scheduler_app_context,
            get_run_id=self._get_run_id,
            status_manager=self._status_manager,
        )
        self._workers.start()

        # Initialize run via writer
        with _scheduler_app_context():
            try:
                result = writer_client.action(
                    "ensure_active_run",
                    {"trigger": "startup", "context": {"source": "init"}},
                    wait=True,
                )
                if result and result.success and result.data:
                    self._set_run_id(result.data["run_id"])
            except Exception as e:  # noqa: BLE001
                logger.error(f"Failed to initialize run: {e}")

    def _set_run_id(self, run_id: str | None) -> None:
        with self._run_lock:
            self._run_id = run_id

    def _get_run_id(self) -> str | None:
        with self._run_lock:
            return self._run_id

    def _wake_worker(self) -> None:
        self._workers.wake()

    # ------------------------ Public API ------------------------
    def start_post_processing(
        self,
        post_guid: str,
        priority: str = "interactive",
        *,
        requested_by_user_id: int | None = None,
        billing_user_id: int | None = None,
        manual: bool = False,
        needs_transcription: bool = True,
    ) -> dict[str, Any]:
        """
        Idempotently start processing for a post. If an active job exists, return it.

        ``manual`` marks a job someone explicitly asked for (process/reprocess
        click); only those may use the paid cloud lane (see app/lanes.py).
        ``needs_transcription=False`` (keep-transcript reprocess) never uses it.
        The lane is written in the same writer action that queues the job, so
        no worker can pick it up from the wrong lane.
        """
        with _scheduler_app_context():
            ensure_result = writer_client.action(
                "ensure_active_run",
                {
                    "trigger": "interactive_start",
                    "context": {"post_guid": post_guid, "priority": priority},
                },
                wait=True,
            )
            run_id = None
            if ensure_result and ensure_result.success and ensure_result.data:
                run_id = ensure_result.data.get("run_id")
            self._set_run_id(run_id)
            decision = self._decide_lane(post_guid, manual, needs_transcription)
            stage = self._decide_stage(post_guid)
            start_result = SingleJobManager(
                post_guid,
                self._status_manager,
                logger,
                run_id,
                requested_by_user_id=requested_by_user_id,
                billing_user_id=billing_user_id,
                lane=decision.lane,
                lane_reason=decision.reason,
                # Automatic re-queues never move an already queued job (so a
                # manual cloud request is not downgraded); manual ones do.
                override_queued_lane=manual,
                stage=stage,
                priority=priority_rank(priority),
            ).start_processing(priority)
            if start_result.get("status") == "started":
                logger.info(
                    "[LANE] job_id=%s post_guid=%s lane=%s reason=%s stage=%s",
                    start_result.get("job_id"),
                    post_guid,
                    decision.lane,
                    decision.reason,
                    stage,
                )
        if start_result.get("status") in {"started", "running"}:
            self._wake_worker()
        return start_result

    def _decide_lane(
        self, post_guid: str, manual: bool, needs_transcription: bool
    ) -> LaneDecision:
        post = Post.query.filter_by(guid=post_guid).first()
        duration = getattr(post, "duration", None) if post else None
        try:
            return choose_lane(
                manual=manual,
                needs_transcription=needs_transcription,
                audio_seconds=float(duration) if duration else None,
            )
        except Exception as exc:  # noqa: BLE001 - routing must never lose a job
            logger.error("Lane decision failed for %s: %s", post_guid, exc)
            return LaneDecision(LANE_LOCAL, f"lane decision failed: {exc}"[:200])

    def _decide_stage(self, post_guid: str) -> str | None:
        """Stage a job for this post starts in. None (unrouted) is treated as
        the transcribe stage, which also reuses an existing transcript, so a
        failure here never loses a job."""
        try:
            post = Post.query.filter_by(guid=post_guid).first()
            return _initial_stage(post) if post is not None else None
        except Exception as exc:  # noqa: BLE001
            logger.error("Stage decision failed for %s: %s", post_guid, exc)
            return None

    def enqueue_pending_jobs(
        self,
        trigger: str = "system",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Ensure all posts have job records and enqueue pending work.

        Returns basic stats for logging/monitoring.
        """
        with _scheduler_app_context():
            result = writer_client.action(
                "ensure_active_run", {"trigger": trigger, "context": context}, wait=True
            )

            run_id = None
            if result and result.success and result.data:
                run_id = result.data["run_id"]
            self._set_run_id(run_id)

            active_run = _db.session.get(JobsManagerRun, run_id) if run_id else None

            created_count, pending_count = self._cleanup_and_process_new_posts(
                active_run
            )

            response = {
                "status": "ok",
                "created": created_count,
                "pending": pending_count,
                "enqueued": pending_count,
                "run_id": run_id,
            }
        if pending_count:
            self._wake_worker()
        return response

    def _ensure_jobs_for_all_posts(self, run_id: str | None) -> int:
        """Ensure every post has an associated ProcessingJob record."""
        posts_without_jobs = (
            Post.query.outerjoin(ProcessingJob, ProcessingJob.post_guid == Post.guid)
            .filter(ProcessingJob.id.is_(None), Post.whitelisted.is_(True))
            .all()
        )

        created = 0
        for post in posts_without_jobs:
            # Avoid recreating jobs for posts that already have processed audio.
            existing_processed_path = find_existing_processed_audio_path(
                processed_audio_path=post.processed_audio_path,
                unprocessed_audio_path=post.unprocessed_audio_path,
                feed_title=getattr(post.feed, "title", None),
                post_title=post.title,
            )
            if existing_processed_path:
                processed_path_str = str(existing_processed_path)
                if post.processed_audio_path != processed_path_str:
                    result = writer_client.update(
                        "Post",
                        post.id,
                        {"processed_audio_path": processed_path_str},
                        wait=True,
                    )
                    if not result or not result.success:
                        logger.warning(
                            "Failed to update recovered processed path for post %s",
                            post.guid,
                        )
                continue

            SingleJobManager(
                post.guid,
                self._status_manager,
                logger,
                run_id,
            ).ensure_job()
            created += 1
        return created

    def get_post_status(self, post_guid: str) -> dict[str, Any]:
        with _scheduler_app_context():
            post = Post.query.filter_by(guid=post_guid).first()
            if not post:
                return {
                    "status": "error",
                    "error_code": "NOT_FOUND",
                    "message": "Post not found",
                }

            job = (
                ProcessingJob.query.filter_by(post_guid=post_guid)
                .order_by(ProcessingJob.created_at.desc())
                .first()
            )

            if not job:
                existing_processed_path = find_existing_processed_audio_path(
                    processed_audio_path=post.processed_audio_path,
                    unprocessed_audio_path=post.unprocessed_audio_path,
                    feed_title=getattr(post.feed, "title", None),
                    post_title=post.title,
                )
                if existing_processed_path:
                    return {
                        "status": "skipped",
                        "step": 4,
                        "step_name": "Processing skipped",
                        "total_steps": 4,
                        "progress_percentage": 100.0,
                        "message": "Post already processed",
                        "download_url": f"/api/posts/{post_guid}/download",
                    }
                return {
                    "status": "not_started",
                    "step": 0,
                    "step_name": "Not started",
                    "total_steps": 4,
                    "progress_percentage": 0.0,
                    "message": "No processing job found",
                }

            response = {
                "status": job.status,
                "step": job.current_step,
                "step_name": job.step_name or "Unknown",
                "total_steps": job.total_steps,
                "progress_percentage": job.progress_percentage,
                "message": job.step_name
                or f"Step {job.current_step} of {job.total_steps}",
            }
            if job.started_at:
                response["started_at"] = job.started_at.isoformat()
            if job.status in {
                "completed",
                "skipped",
            } and find_existing_processed_audio_path(
                processed_audio_path=post.processed_audio_path,
                unprocessed_audio_path=post.unprocessed_audio_path,
                feed_title=getattr(post.feed, "title", None),
                post_title=post.title,
            ):
                response["download_url"] = f"/api/posts/{post_guid}/download"
            if job.status == "failed" and job.error_message:
                response["error"] = job.error_message
            if job.status == "cancelled" and job.error_message:
                response["message"] = job.error_message
                response["step_name"] = job.error_message
            return response

    def get_job_status(self, job_id: str) -> dict[str, Any]:
        with _scheduler_app_context():
            job = _db.session.get(ProcessingJob, job_id)
            if not job:
                return {
                    "status": "error",
                    "error_code": "NOT_FOUND",
                    "message": "Job not found",
                }
            return {
                "job_id": job.id,
                "post_guid": job.post_guid,
                "status": job.status,
                "step": job.current_step,
                "step_name": job.step_name,
                "total_steps": job.total_steps,
                "progress_percentage": job.progress_percentage,
                "started_at": job.started_at.isoformat() if job.started_at else None,
                "completed_at": (
                    job.completed_at.isoformat() if job.completed_at else None
                ),
                "error": job.error_message,
            }

    def list_active_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with _scheduler_app_context():
            # Derive a simple priority from status: running > pending
            priority_order = case(
                (ProcessingJob.status == "running", 2),
                (ProcessingJob.status == "pending", 1),
                else_=0,
            ).label("priority")

            rows = (
                _db.session.query(ProcessingJob, Post, priority_order)
                .outerjoin(Post, ProcessingJob.post_guid == Post.guid)
                .filter(ProcessingJob.status.in_(["pending", "running"]))
                .order_by(priority_order.desc(), ProcessingJob.created_at.desc())
                .limit(limit)
                .all()
            )

            results: list[dict[str, Any]] = []
            for job, post, prio in rows:
                results.append(
                    {
                        "job_id": job.id,
                        "post_guid": job.post_guid,
                        "post_title": post.title if post else None,
                        "feed_title": post.feed.title if post and post.feed else None,
                        "status": job.status,
                        "priority": int(prio) if prio is not None else 0,
                        "step": job.current_step,
                        "step_name": job.step_name,
                        "total_steps": job.total_steps,
                        "progress_percentage": job.progress_percentage,
                        "created_at": (
                            job.created_at.isoformat() if job.created_at else None
                        ),
                        "started_at": (
                            job.started_at.isoformat() if job.started_at else None
                        ),
                        "completed_at": (
                            job.completed_at.isoformat() if job.completed_at else None
                        ),
                        "error_message": job.error_message,
                        "lane": job.lane or LANE_LOCAL,
                        "lane_reason": job.lane_reason,
                        "stage": job.stage or STAGE_TRANSCRIBE,
                    }
                )

            return results

    def list_all_jobs_detailed(self, limit: int = 200) -> list[dict[str, Any]]:
        with _scheduler_app_context():
            # Priority by status, others ranked lowest
            priority_order = case(
                (ProcessingJob.status == "running", 2),
                (ProcessingJob.status == "pending", 1),
                else_=0,
            ).label("priority")

            rows = (
                _db.session.query(ProcessingJob, Post, priority_order)
                .outerjoin(Post, ProcessingJob.post_guid == Post.guid)
                .order_by(priority_order.desc(), ProcessingJob.created_at.desc())
                .limit(limit)
                .all()
            )

            results: list[dict[str, Any]] = []
            for job, post, prio in rows:
                results.append(
                    {
                        "job_id": job.id,
                        "post_guid": job.post_guid,
                        "post_title": post.title if post else None,
                        "feed_title": post.feed.title if post and post.feed else None,
                        "status": job.status,
                        "priority": int(prio) if prio is not None else 0,
                        "step": job.current_step,
                        "step_name": job.step_name,
                        "total_steps": job.total_steps,
                        "progress_percentage": job.progress_percentage,
                        "created_at": (
                            job.created_at.isoformat() if job.created_at else None
                        ),
                        "started_at": (
                            job.started_at.isoformat() if job.started_at else None
                        ),
                        "completed_at": (
                            job.completed_at.isoformat() if job.completed_at else None
                        ),
                        "error_message": job.error_message,
                        "lane": job.lane or LANE_LOCAL,
                        "lane_reason": job.lane_reason,
                        "stage": job.stage or STAGE_TRANSCRIBE,
                    }
                )

            return results

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        with _scheduler_app_context():
            job = _db.session.get(ProcessingJob, job_id)
            if not job:
                return {
                    "status": "error",
                    "error_code": "NOT_FOUND",
                    "message": "Job not found",
                }

            if job.status in ["completed", "failed", "cancelled", "skipped"]:
                return {
                    "status": "error",
                    "error_code": "ALREADY_FINISHED",
                    "message": f"Job already {job.status}",
                }

            # Mark job as cancelled in database
            self._status_manager.mark_cancelled(job_id, "Cancelled by user request")

            return {
                "status": "cancelled",
                "job_id": job_id,
                "message": "Job cancelled",
            }

    def cancel_post_jobs(self, post_guid: str) -> dict[str, Any]:
        with _scheduler_app_context():
            # Find active jobs for this post in database
            active_jobs = (
                ProcessingJob.query.filter_by(post_guid=post_guid)
                .filter(ProcessingJob.status.in_(["pending", "running"]))
                .all()
            )

            job_ids = [job.id for job in active_jobs]
            for job in active_jobs:
                self._status_manager.mark_cancelled(job.id, "Cancelled by user request")

            return {
                "status": "cancelled",
                "post_guid": post_guid,
                "job_ids": job_ids,
                "message": f"Cancelled {len(job_ids)} jobs",
            }

    def cancel_queued_jobs(self) -> dict[str, Any]:
        """Cancel all queued (pending) jobs."""
        with _scheduler_app_context():
            queued_jobs = (
                ProcessingJob.query.filter(ProcessingJob.status == "pending")
                .order_by(ProcessingJob.created_at.asc())
                .all()
            )

            cancelled_job_ids: list[str] = []
            for job in queued_jobs:
                self._status_manager.mark_cancelled(job.id, "Cancelled by user request")
                cancelled_job_ids.append(job.id)

            return {
                "status": "cancelled",
                "cancelled_count": len(cancelled_job_ids),
                "message": f"Cancelled {len(cancelled_job_ids)} queued jobs",
            }

    def cleanup_stale_jobs(self, older_than: timedelta) -> int:
        try:
            result = writer_client.action(
                "cleanup_stale_jobs",
                {"older_than_seconds": older_than.total_seconds()},
                wait=True,
            )
            if result and result.success and result.data:
                return cast(int, result.data.get("count", 0))
            return 0
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to cleanup stale jobs: {e}")
            return 0

    def cleanup_stuck_pending_jobs(self, stuck_threshold_minutes: int = 10) -> int:
        """
        Clean up jobs that have been stuck in 'pending' status for too long.
        This indicates they were never picked up by the thread pool.
        """
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            minutes=stuck_threshold_minutes
        )
        with _scheduler_app_context():
            stuck_jobs = ProcessingJob.query.filter(
                ProcessingJob.status == "pending", ProcessingJob.created_at < cutoff
            ).all()

            count = len(stuck_jobs)
            for job in stuck_jobs:
                try:
                    logger.warning(
                        f"Marking stuck pending job {job.id} as failed (created at {job.created_at})"
                    )
                    self._status_manager.update_job_status(
                        job,
                        "failed",
                        job.current_step,
                        f"Job was stuck in pending status for over {stuck_threshold_minutes} minutes",
                    )
                except Exception as e:  # noqa: BLE001
                    logger.error(f"Failed to update stuck job {job.id}: {e}")

            return count

    def clear_all_jobs(self) -> dict[str, Any]:
        """
        Clear all processing jobs from the database.
        This is typically called during application startup to ensure a clean state.
        """
        try:
            result = writer_client.action("clear_all_jobs", {}, wait=True)
            count = result.data if result and result.success else 0
            logger.info(f"Cleared {count} processing jobs on startup")
            return {
                "status": "success",
                "cleared_jobs": count,
                "message": f"Cleared {count} jobs from database",
            }
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error clearing all jobs: {e}")
            return {"status": "error", "message": f"Failed to clear jobs: {e!s}"}

    def requeue_interrupted_jobs(self) -> dict[str, Any]:
        """Startup: put jobs a stop left running back in their stage's queue.

        Pending jobs stay queued. Jobs that were in the LLM stage keep their
        transcript and skip Whisper.
        """
        try:
            with _scheduler_app_context():
                result = writer_client.action("requeue_interrupted_jobs", {}, wait=True)
                if not (result and result.success):
                    raise RuntimeError(getattr(result, "error", None) or "no result")
                count = int((result.data or {}).get("requeued") or 0)
                pending = ProcessingJob.query.filter(
                    ProcessingJob.status == "pending"
                ).count()
        except Exception as e:  # noqa: BLE001
            logger.error(f"Error re-queueing interrupted jobs: {e}")
            return {"status": "error", "message": f"Failed to re-queue jobs: {e!s}"}
        if pending:
            self._wake_worker()
        return {
            "status": "success",
            "requeued_jobs": count,
            "pending_jobs": pending,
            "message": f"Re-queued {count} interrupted jobs; {pending} jobs queued",
        }

    def start_refresh_all_feeds(
        self,
        trigger: str = "scheduled",
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Refresh feeds and enqueue per-post processing into internal worker pool.
        """
        with _scheduler_app_context():
            feeds = Feed.query.all()
            for feed in feeds:
                refresh_feed(feed)

            # Clean up posts with missing audio files
            self._cleanup_inconsistent_posts()

            # Process new posts
            return self.enqueue_pending_jobs(trigger=trigger, context=context)

    # ------------------------ Helpers ------------------------
    def _cleanup_inconsistent_posts(self) -> None:
        """Clean up posts with missing audio files."""
        try:
            writer_client.action("cleanup_missing_audio_paths", {}, wait=True)
        except Exception as e:
            logger.error(
                f"Failed to cleanup inconsistent posts: {e}",
                exc_info=True,
            )

    def _cleanup_and_process_new_posts(
        self, active_run: JobsManagerRun | None
    ) -> tuple[int, int]:
        """Ensure all posts have jobs and return counts for monitoring."""
        run_id = active_run.id if active_run else None
        created_jobs = self._ensure_jobs_for_all_posts(run_id)

        pending_jobs = (
            ProcessingJob.query.filter(ProcessingJob.status == "pending")
            .order_by(ProcessingJob.created_at.asc())
            .all()
        )

        if active_run and pending_jobs:
            try:
                writer_client.action(
                    "reassign_pending_jobs", {"run_id": run_id}, wait=True
                )
            except Exception as e:  # noqa: BLE001
                logger.error("Failed to reassign pending jobs: %s", e)

        if created_jobs:
            logger.info("Created %s new job records", created_jobs)

        logger.info(
            "Pending jobs ready for worker: count=%s run_id=%s",
            len(pending_jobs),
            run_id,
        )

        return created_jobs, len(pending_jobs)

    # Removed _get_active_job_for_guid - now using direct database queries

    # ------------------------ Internal helpers ------------------------


def _initial_stage(post: Post) -> str:
    """Stage a newly queued job starts in (see app/pipeline.py)."""
    strategy = getattr(post.feed, "ad_detection_strategy", None) or "llm"
    if strategy == "chapter":
        return STAGE_LLM  # no Whisper: cut by chapter markers
    if strategy == "chapter_insert":
        return STAGE_TRANSCRIBE  # may need Whisper; runs whole there
    from app.runtime_config import config

    manager = TranscriptionManager(logger, config)
    if manager.get_reusable_transcription(post):
        return STAGE_LLM
    return STAGE_TRANSCRIBE


# Singleton accessor
def get_jobs_manager() -> JobsManager:
    if not hasattr(get_jobs_manager, "_instance"):
        get_jobs_manager._instance = JobsManager()  # type: ignore[attr-defined]
    return get_jobs_manager._instance  # type: ignore[attr-defined, no-any-return]


def scheduled_refresh_all_feeds() -> None:
    """Top-level function for APScheduler to invoke periodically."""
    try:
        get_jobs_manager().start_refresh_all_feeds(trigger="scheduled")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Scheduled refresh failed: {e}")
