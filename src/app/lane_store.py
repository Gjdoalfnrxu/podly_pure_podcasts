"""DB reads for the processing lanes (shared by the app and the writer)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func

from app.extensions import db
from app.lanes import LaneSettings, month_start
from app.models import CloudLaneSettings, CloudLaneUsage
from shared import defaults as DEFAULTS

# Rows that cost (or may have cost) money: reserved = in flight or interrupted
# by a restart (counted in full, since the provider may have billed it).
COUNTED_STATUSES = ("reserved", "charged", "failed")


def load_lane_settings() -> LaneSettings:
    row = db.session.get(CloudLaneSettings, 1)
    if row is None:
        return LaneSettings(
            enabled=False,
            api_key=None,
            base_url=DEFAULTS.CLOUD_LANE_BASE_URL,
            model=DEFAULTS.CLOUD_LANE_MODEL,
            language=DEFAULTS.WHISPER_REMOTE_LANGUAGE,
            usd_per_hour=DEFAULTS.CLOUD_LANE_USD_PER_HOUR,
            monthly_cap_usd=0.0,
            max_episode_minutes=None,
        )
    return LaneSettings(
        enabled=bool(row.enabled),
        api_key=row.api_key,
        base_url=row.base_url,
        model=row.model,
        language=row.language,
        usd_per_hour=float(row.usd_per_hour),
        monthly_cap_usd=float(row.monthly_cap_usd),
        max_episode_minutes=row.max_episode_minutes,
    )


def month_spent_usd(now: datetime | None = None) -> float:
    """Cost this calendar month (UTC): settled cost, else the reserved estimate."""
    amount = func.coalesce(CloudLaneUsage.cost_usd, CloudLaneUsage.estimated_usd)
    total = (
        db.session.query(func.coalesce(func.sum(amount), 0.0))
        .filter(
            CloudLaneUsage.created_at >= month_start(now),
            CloudLaneUsage.status.in_(COUNTED_STATUSES),
        )
        .scalar()
    )
    return float(total or 0.0)
