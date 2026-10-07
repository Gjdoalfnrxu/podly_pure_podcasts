"""Writer actions for the processing lanes. The writer runs actions one at a
time, so the budget check and the reservation insert are atomic."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.extensions import db
from app.lane_store import month_spent_usd
from app.lanes import LANE_CLOUD, LANE_LOCAL, cost_usd
from app.models import CloudLaneSettings, CloudLaneUsage, ProcessingJob

_SETTINGS_FIELDS = {
    "enabled": bool,
    "base_url": str,
    "api_key": str,
    "model": str,
    "language": str,
    "usd_per_hour": float,
    "monthly_cap_usd": float,
    "max_episode_minutes": int,
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def reserve_cloud_usage_action(params: dict[str, Any]) -> dict[str, Any]:
    usd_per_hour = float(params["usd_per_hour"])
    cap = float(params["cap_usd"])
    estimate = cost_usd(float(params["estimated_billed_seconds"]), usd_per_hour)
    spent = month_spent_usd()
    if spent + estimate > cap + 1e-9:
        return {"reserved": False, "spent_usd": spent, "estimated_usd": estimate}
    usage = CloudLaneUsage(
        job_id=params.get("job_id"),
        post_guid=params["post_guid"],
        model=params["model"],
        status="reserved",
        audio_seconds=float(params["audio_seconds"]),
        estimated_usd=estimate,
        usd_per_hour=usd_per_hour,
        created_at=_now(),
    )
    db.session.add(usage)
    db.session.flush()
    return {
        "reserved": True,
        "usage_id": usage.id,
        "spent_usd": spent,
        "estimated_usd": estimate,
    }


def settle_cloud_usage_action(params: dict[str, Any]) -> dict[str, Any]:
    usage = db.session.get(CloudLaneUsage, int(params["usage_id"]))
    if usage is None:
        raise ValueError(f"CloudLaneUsage {params['usage_id']} not found")
    status = params["status"]
    if status not in ("charged", "failed"):
        raise ValueError(f"invalid usage status {status}")
    billed = float(params.get("billed_seconds") or 0.0)
    usage.status = status
    usage.billed_seconds = billed
    usage.cost_usd = cost_usd(billed, usage.usd_per_hour)
    usage.error = params.get("error")
    usage.settled_at = _now()
    return {"usage_id": usage.id, "cost_usd": usage.cost_usd}


def set_job_lane_action(params: dict[str, Any]) -> dict[str, Any]:
    job = db.session.get(ProcessingJob, params["job_id"])
    lane = params["lane"]
    if lane not in (LANE_LOCAL, LANE_CLOUD):
        raise ValueError(f"invalid lane {lane}")
    # Never move a job a worker has already picked up.
    if job is None or job.status != "pending":
        return {"updated": False}
    job.lane = lane
    job.lane_reason = params.get("reason")
    return {"updated": True}


def requeue_job_local_action(params: dict[str, Any]) -> dict[str, Any]:
    """Cloud lane fallback: put a failed cloud job back in the local queue."""
    job = db.session.get(ProcessingJob, params["job_id"])
    if job is None or job.status != "failed":
        return {"requeued": False}
    job.status = "pending"
    job.lane = LANE_LOCAL
    job.lane_reason = params.get("reason")
    job.current_step = 0
    job.step_name = "Queued for local processing (cloud fallback)"
    job.progress_percentage = 0.0
    job.error_message = None
    job.started_at = None
    job.completed_at = None
    return {"requeued": True}


def update_cloud_lane_settings_action(params: dict[str, Any]) -> dict[str, Any]:
    row = db.session.get(CloudLaneSettings, 1)
    if row is None:
        row = CloudLaneSettings(id=1)
        db.session.add(row)
    for key, value in params.items():
        cast = _SETTINGS_FIELDS.get(key)
        if cast is None:
            raise ValueError(f"unknown cloud lane setting {key}")
        if value is None and key in ("api_key", "max_episode_minutes"):
            setattr(row, key, None)
        else:
            setattr(row, key, cast(value))
    db.session.flush()
    return {"updated": True}
