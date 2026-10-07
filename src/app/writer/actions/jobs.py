from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, or_, select

from app.extensions import db
from app.jobs_manager_run_service import recalculate_run_counts
from app.lanes import LANE_CLOUD, LANE_LOCAL
from app.models import ProcessingJob
from app.pipeline import STAGE_LLM, STAGE_TRANSCRIBE
from shared import defaults as DEFAULTS


def _pool_clause(stage: str, lane: str) -> Any:
    """Jobs that belong to one worker pool.

    Transcribe pools are per lane (local: NULL/"local", cloud: "cloud") and
    take jobs whose stage is NULL (not routed yet) or "transcribe". The LLM pool
    takes stage "llm" jobs of any lane (the lane only decides who transcribes).
    """
    if stage == STAGE_LLM:
        return ProcessingJob.stage == STAGE_LLM
    if lane == LANE_LOCAL:
        in_lane = or_(ProcessingJob.lane.is_(None), ProcessingJob.lane == LANE_LOCAL)
    else:
        in_lane = ProcessingJob.lane == lane
    in_stage = or_(
        ProcessingJob.stage.is_(None), ProcessingJob.stage == STAGE_TRANSCRIBE
    )
    return and_(in_stage, in_lane)


def dequeue_job_action(params: dict[str, Any]) -> dict[str, Any] | None:
    """Claim the next pending job for one stage worker pool.

    Returns None when ``max_running`` jobs of the pool are already running, or
    nothing is eligible. A post with a running job (any stage), or listed in
    ``exclude_post_guids`` (posts a worker thread is still busy with), is never
    claimed, so one post is never processed twice at once. Order: priority
    (higher first), then oldest first. The claimed job is marked running in
    this same writer action, so two workers cannot claim the same job.
    """
    run_id = params.get("run_id")
    stage = params.get("stage") or STAGE_TRANSCRIBE
    lane = params.get("lane") or LANE_LOCAL
    max_running = int(params.get("max_running") or 1)
    exclude = [str(g) for g in params.get("exclude_post_guids") or []]

    in_pool = _pool_clause(stage, lane)
    running = ProcessingJob.query.filter(
        ProcessingJob.status == "running", in_pool
    ).count()
    if running >= max_running:
        return None

    busy_posts = select(ProcessingJob.post_guid).where(
        ProcessingJob.status == "running"
    )
    query = ProcessingJob.query.filter(
        ProcessingJob.status == "pending",
        in_pool,
        ProcessingJob.post_guid.not_in(busy_posts),
    )
    if exclude:
        query = query.filter(ProcessingJob.post_guid.not_in(exclude))
    job = query.order_by(
        ProcessingJob.priority.desc(), ProcessingJob.created_at.asc()
    ).first()
    if not job:
        return None

    job.status = "running"
    job.stage = stage
    if job.started_at is None:
        job.started_at = datetime.now(UTC).replace(tzinfo=None)

    if run_id and job.jobs_manager_run_id != run_id:
        job.jobs_manager_run_id = run_id

    return {"job_id": job.id, "post_guid": job.post_guid, "stage": stage}


def advance_job_stage_action(params: dict[str, Any]) -> dict[str, Any]:
    """Hand a running job to another stage's queue (pending + new stage).

    Only a job that is still running moves; a job cancelled meanwhile stays
    cancelled.
    """
    job = db.session.get(ProcessingJob, params.get("job_id"))
    if job is None or job.status != "running":
        return {"advanced": False, "status": getattr(job, "status", None)}
    job.status = "pending"
    job.stage = params["stage"]
    if job.stage == STAGE_TRANSCRIBE:
        _requeue_off_cloud(job)
    job.current_step = params.get("step", job.current_step)
    job.step_name = params.get("step_name", job.step_name)
    if params.get("progress") is not None:
        job.progress_percentage = params["progress"]
    if job.jobs_manager_run_id:
        recalculate_run_counts(db.session)
    return {"advanced": True, "status": job.status}


def route_job_action(params: dict[str, Any]) -> dict[str, Any]:
    """Set a queued job's stage and raise (never lower) its priority."""
    job = db.session.get(ProcessingJob, params.get("job_id"))
    if job is None or job.status != "pending":
        return {"routed": False}
    stage = params.get("stage")
    if stage:
        job.stage = stage
    job.priority = max(job.priority or 0, int(params.get("priority") or 0))
    return {"routed": True, "stage": job.stage, "priority": job.priority}


def _requeue_off_cloud(job: ProcessingJob) -> None:
    """Re-queued work never uses the paid lane; only queueing a job with
    create_job/set_job_lane puts it there (same rule as update_job_status)."""
    if job.lane == LANE_CLOUD:
        job.lane = None
        job.lane_reason = "re-queued: local lane"


def requeue_interrupted_jobs_action(params: dict[str, Any]) -> dict[str, Any]:
    """Startup: jobs left running by a stop go back to pending, same stage.

    A job that was in the LLM stage keeps stage "llm" (its transcript is in the
    DB), so it skips Whisper. Pending jobs are left as they are. A job already
    re-queued ``max_requeues`` times fails instead: if it is what kills the
    process (OOM), re-queueing it again would crash-loop the container.
    """
    max_requeues = int(
        params.get("max_requeues", DEFAULTS.PIPELINE_MAX_RESTART_REQUEUES)
    )
    interrupted = ProcessingJob.query.filter(ProcessingJob.status == "running").all()
    failed: list[str] = []
    now = datetime.now(UTC).replace(tzinfo=None)
    for job in interrupted:
        count = job.restart_requeues or 0
        if count >= max_requeues:
            job.status = "failed"
            job.step_name = "Failed: interrupted by repeated restarts"
            job.error_message = (
                f"Interrupted by {count + 1} restarts; not re-queued again "
                "(it may be what stops the server, e.g. out of memory)"
            )
            job.completed_at = now
            failed.append(job.id)
            continue
        job.restart_requeues = count + 1
        job.status = "pending"
        job.step_name = "Re-queued after restart"
        _requeue_off_cloud(job)
    if interrupted:
        recalculate_run_counts(db.session)
    return {"requeued": len(interrupted) - len(failed), "failed": failed}


def requeue_orphaned_jobs_action(params: dict[str, Any]) -> dict[str, Any]:
    """Re-queue running jobs of one pool that no worker thread owns.

    ``owned_post_guids`` are the posts the worker process is running. Any other
    running job of the pool was claimed by a dequeue whose reply never arrived
    (or its hand-off could not be written), so nothing will ever finish it.
    Not counted as a restart re-queue: the job did not crash anything.
    """
    stage = params.get("stage") or STAGE_TRANSCRIBE
    lane = params.get("lane") or LANE_LOCAL
    owned = [str(g) for g in params.get("owned_post_guids") or []]
    query = ProcessingJob.query.filter(
        ProcessingJob.status == "running", _pool_clause(stage, lane)
    )
    if owned:
        query = query.filter(ProcessingJob.post_guid.not_in(owned))
    orphans = query.all()
    for job in orphans:
        job.status = "pending"
        job.step_name = "Re-queued: no worker owned it"
        _requeue_off_cloud(job)
    if orphans:
        recalculate_run_counts(db.session)
    return {"job_ids": [job.id for job in orphans]}


def fail_job_if_running_action(params: dict[str, Any]) -> dict[str, Any]:
    """Fail a job only if it is still running (a cancel or finish meanwhile
    wins)."""
    job = db.session.get(ProcessingJob, params.get("job_id"))
    if job is None or job.status != "running":
        return {"failed": False, "status": getattr(job, "status", None)}
    job.status = "failed"
    job.error_message = params.get("error_message") or "failed"
    job.step_name = "Failed"
    job.completed_at = datetime.now(UTC).replace(tzinfo=None)
    if job.jobs_manager_run_id:
        recalculate_run_counts(db.session)
    return {"failed": True, "status": job.status}


def cleanup_stale_jobs_action(params: dict[str, Any]) -> dict[str, Any]:
    older_than_seconds = params.get("older_than_seconds", 3600)
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=older_than_seconds
    )

    old_jobs = ProcessingJob.query.filter(ProcessingJob.created_at < cutoff).all()

    count = len(old_jobs)
    for job in old_jobs:
        db.session.delete(job)

    return {"count": count}


def clear_all_jobs_action(params: dict[str, Any]) -> int:
    all_jobs = ProcessingJob.query.all()
    count = len(all_jobs)
    for job in all_jobs:
        db.session.delete(job)
    return count


def clear_active_jobs_action(params: dict[str, Any]) -> int:
    """Clear only pending and running jobs. Preserves completed/failed/skipped/cancelled."""
    active_jobs = ProcessingJob.query.filter(
        ProcessingJob.status.in_(["pending", "running"])
    ).all()
    count = len(active_jobs)
    for job in active_jobs:
        db.session.delete(job)
    if count > 0:
        recalculate_run_counts(db.session)
    return count


def create_job_action(params: dict[str, Any]) -> dict[str, Any]:
    job_data = params.get("job_data")
    if not isinstance(job_data, dict):
        raise ValueError("job_data must be a dictionary")

    # Convert date strings back to datetime objects if necessary
    if "created_at" in job_data and isinstance(job_data["created_at"], str):
        job_data["created_at"] = datetime.fromisoformat(job_data["created_at"])

    job = ProcessingJob(**job_data)
    db.session.add(job)

    if job.jobs_manager_run_id:
        recalculate_run_counts(db.session)

    db.session.flush()
    return {"job_id": job.id}


def create_job_if_missing_action(params: dict[str, Any]) -> dict[str, Any]:
    """Create a new pending job only if no completed/skipped job already exists for the post."""
    job_data = params.get("job_data")
    if not isinstance(job_data, dict):
        raise ValueError("job_data must be a dictionary")

    post_guid = job_data.get("post_guid")
    if not post_guid:
        raise ValueError("job_data must contain post_guid")

    existing = ProcessingJob.query.filter_by(post_guid=post_guid).first()
    if existing:
        return {"job_id": None, "skipped": True}

    return create_job_action({"job_data": job_data})


def cancel_existing_jobs_action(params: dict[str, Any]) -> int:
    post_guid = params.get("post_guid")
    current_job_id = params.get("current_job_id")

    existing_jobs = (
        ProcessingJob.query.filter_by(post_guid=post_guid)
        .filter(
            ProcessingJob.status.in_(["pending", "running"]),
            ProcessingJob.id != current_job_id,
        )
        .all()
    )

    count = len(existing_jobs)
    for existing_job in existing_jobs:
        db.session.delete(existing_job)

    if count > 0:
        recalculate_run_counts(db.session)

    return count


def update_job_status_action(params: dict[str, Any]) -> dict[str, Any]:
    job_id = params.get("job_id")
    status = params.get("status")
    step = params.get("step")
    step_name = params.get("step_name")
    progress = params.get("progress")
    error_message = params.get("error_message")

    job = db.session.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    if status == "pending" and job.status != "pending" and job.lane == LANE_CLOUD:
        # Only create_job/set_job_lane put work in the paid lane; anything else
        # re-queueing a job sends it to the free local lane.
        job.lane = None
        job.lane_reason = "automatic job"
    job.status = status
    job.current_step = step
    job.step_name = step_name
    if progress is not None:
        job.progress_percentage = progress

    if error_message:
        job.error_message = error_message

    if status == "running" and not job.started_at:
        job.started_at = datetime.now(UTC).replace(tzinfo=None)
    elif (
        status in ["completed", "failed", "cancelled", "skipped"]
        and not job.completed_at
    ):
        job.completed_at = datetime.now(UTC).replace(tzinfo=None)

    if job.jobs_manager_run_id:
        recalculate_run_counts(db.session)

    return {"job_id": job.id, "status": job.status}


def mark_cancelled_action(params: dict[str, Any]) -> dict[str, Any]:
    job_id = params.get("job_id")
    reason = params.get("reason") or "Cancelled by user request"

    job = db.session.get(ProcessingJob, job_id)
    if not job:
        raise ValueError(f"Job {job_id} not found")

    job.status = "cancelled"
    job.step_name = reason
    job.error_message = reason
    job.completed_at = datetime.now(UTC).replace(tzinfo=None)

    if job.jobs_manager_run_id:
        recalculate_run_counts(db.session)

    return {"job_id": job.id, "status": "cancelled"}


def reassign_pending_jobs_action(params: dict[str, Any]) -> int:
    run_id = params.get("run_id")
    if not run_id:
        return 0

    pending_jobs = (
        ProcessingJob.query.filter(ProcessingJob.status == "pending")
        .order_by(ProcessingJob.created_at.asc())
        .all()
    )

    reassigned = 0
    for job in pending_jobs:
        if job.jobs_manager_run_id != run_id:
            job.jobs_manager_run_id = run_id
            reassigned += 1

    if reassigned:
        recalculate_run_counts(db.session)

    return reassigned
