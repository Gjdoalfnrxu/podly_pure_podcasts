"""Cloud fast lane: status for everyone, settings for admins."""

from __future__ import annotations

import math
from typing import Any

from flask import Blueprint, jsonify, request
from flask.typing import ResponseReturnValue
from sqlalchemy import func

from app.auth.guards import require_admin
from app.extensions import db
from app.lane_store import load_lane_settings, month_spent_usd
from app.lanes import LANE_CLOUD, LANE_LOCAL, cloud_unavailable_reason, month_start
from app.models import CloudLaneUsage, ProcessingJob
from app.routes.config_routes import _mask_secret
from app.writer.client import writer_client
from shared import defaults as DEFAULTS

lane_bp = Blueprint("lanes", __name__)


def _queue_counts() -> dict[str, dict[str, int]]:
    lane = func.coalesce(ProcessingJob.lane, LANE_LOCAL)
    rows = (
        db.session.query(lane, ProcessingJob.status, func.count())
        .filter(ProcessingJob.status.in_(["pending", "running"]))
        .group_by(lane, ProcessingJob.status)
        .all()
    )
    counts = {
        LANE_LOCAL: {"pending": 0, "running": 0},
        LANE_CLOUD: {"pending": 0, "running": 0},
    }
    for lane_name, status, count in rows:
        counts.setdefault(lane_name, {"pending": 0, "running": 0})[status] = count
    return counts


@lane_bp.route("/api/lanes/status", methods=["GET"])
def lane_status() -> ResponseReturnValue:
    settings = load_lane_settings()
    usage_count = (
        db.session.query(func.count(CloudLaneUsage.id))
        .filter(CloudLaneUsage.created_at >= month_start())
        .scalar()
    )
    unavailable = cloud_unavailable_reason(settings)
    return jsonify(
        {
            "cloud_available": unavailable is None,
            "cloud_unavailable_reason": unavailable,
            "month_spent_usd": round(month_spent_usd(), 6),
            "monthly_cap_usd": settings.monthly_cap_usd,
            "month_cloud_jobs": int(usage_count or 0),
            "cloud_concurrency": DEFAULTS.CLOUD_LANE_CONCURRENCY,
            "queues": _queue_counts(),
        }
    )


@lane_bp.route("/api/lanes/settings", methods=["GET"])
def get_lane_settings() -> ResponseReturnValue:
    _, error = require_admin("view cloud lane settings")
    if error:
        return error
    s = load_lane_settings()
    return jsonify(
        {
            "enabled": s.enabled,
            "base_url": s.base_url,
            "api_key_set": bool(s.api_key),
            "api_key_preview": _mask_secret(s.api_key),
            "model": s.model,
            "language": s.language,
            "usd_per_hour": s.usd_per_hour,
            "monthly_cap_usd": s.monthly_cap_usd,
            "max_episode_minutes": s.max_episode_minutes,
        }
    )


def _text(value: Any) -> Any:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("must be a non-empty string")
    return value.strip()


def _flag(value: Any) -> Any:
    if not isinstance(value, bool):
        raise ValueError("must be true or false")
    return value


def _positive_money(value: Any) -> Any:
    value = _money(value)
    if value <= 0:
        raise ValueError("must be a number > 0")
    return value


def _money(value: Any) -> Any:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or math.isnan(value)
        or value < 0
    ):
        raise ValueError("must be a number >= 0")
    return float(value)


def _minutes(value: Any) -> Any:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
    ):
        raise ValueError("must be a positive integer or null")
    return value


def _key(value: Any) -> Any:
    # Omit the field to keep the stored key; "" or null clears it.
    if value is not None and not isinstance(value, str):
        raise ValueError("must be a string or null")
    return (value or "").strip() or None


_VALIDATORS = {
    "enabled": _flag,
    "base_url": _text,
    "api_key": _key,
    "model": _text,
    "language": _text,
    "usd_per_hour": _positive_money,
    "monthly_cap_usd": _money,
    "max_episode_minutes": _minutes,
}


def _validate(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    unknown = set(payload) - set(_VALIDATORS)
    if unknown:
        return {}, f"unknown settings: {', '.join(sorted(unknown))}"
    updates: dict[str, Any] = {}
    for key, value in payload.items():
        try:
            updates[key] = _VALIDATORS[key](value)
        except ValueError as exc:
            return {}, f"{key} {exc}"
    return updates, None


@lane_bp.route("/api/lanes/settings", methods=["PUT"])
def put_lane_settings() -> ResponseReturnValue:
    _, error = require_admin("change cloud lane settings")
    if error:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "JSON object required"}), 400
    updates, problem = _validate(payload)
    if problem:
        return jsonify({"error": problem}), 400
    if updates:
        result = writer_client.action("update_cloud_lane_settings", updates, wait=True)
        if not result or not result.success:
            return jsonify({"error": "Failed to save settings"}), 500
        db.session.expire_all()
    return get_lane_settings()
