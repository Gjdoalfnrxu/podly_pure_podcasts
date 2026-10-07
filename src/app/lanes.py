"""Processing lanes: a free local (CPU Whisper) slow lane and a paid cloud fast lane.

Routing rule, in order (first match wins):
  1. Automatic jobs (feed refresh, new-feed latest episode, podcast-app download
     triggers) always use the local lane.
  2. Manual jobs (someone clicked process/reprocess) use the cloud lane only if
     the lane is enabled, has an API key, has a monthly cap above $0, the episode
     is within the optional length limit, and its estimated cost fits in what is
     left of this month's cap. Otherwise local.
The cap is enforced again right before the cloud call using the downloaded
file's real duration; anything that fails there or in the call falls back to
the local lane, so a job is never lost.

This module is pure (no DB, no Flask) so the rules are easy to test and read.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime

from shared import defaults as DEFAULTS

LANE_LOCAL = "local"
LANE_CLOUD = "cloud"


@dataclass(frozen=True)
class LaneSettings:
    enabled: bool
    api_key: str | None
    base_url: str
    model: str
    language: str
    usd_per_hour: float
    monthly_cap_usd: float
    max_episode_minutes: int | None


@dataclass(frozen=True)
class LaneDecision:
    lane: str
    reason: str


def cost_usd(billed_seconds: float, usd_per_hour: float) -> float:
    return billed_seconds / 3600.0 * usd_per_hour


def billed_seconds_for_chunks(chunk_seconds: list[float]) -> float:
    """Provider bills each request with a minimum length (Groq: 10 s)."""
    floor = DEFAULTS.CLOUD_LANE_MIN_BILLED_SECONDS
    return sum(max(s, floor) for s in chunk_seconds)


def estimate_billed_seconds(audio_seconds: float, chunk_count: int = 1) -> float:
    return max(audio_seconds, DEFAULTS.CLOUD_LANE_MIN_BILLED_SECONDS * chunk_count)


def month_start(now: datetime | None = None) -> datetime:
    """Start of the current calendar month, UTC, naive (matches DB timestamps)."""
    now = now or datetime.now(UTC)
    if now.tzinfo is not None:
        now = now.astimezone(UTC).replace(tzinfo=None)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def cloud_unavailable_reason(settings: LaneSettings) -> str | None:
    if not settings.enabled:
        return "cloud lane disabled"
    if not (settings.api_key or "").strip():
        return "cloud lane has no API key"
    if settings.monthly_cap_usd <= 0:
        return "cloud monthly cap is $0"
    if settings.usd_per_hour < 0 or math.isnan(settings.usd_per_hour):
        return "cloud price per hour is invalid"
    return None


def fits_budget(
    settings: LaneSettings, spent_usd: float, audio_seconds: float
) -> tuple[bool, float]:
    estimate = cost_usd(estimate_billed_seconds(audio_seconds), settings.usd_per_hour)
    return spent_usd + estimate <= settings.monthly_cap_usd + 1e-9, estimate


def decide_lane(
    *,
    manual: bool,
    settings: LaneSettings,
    spent_usd: float,
    audio_seconds: float | None,
) -> LaneDecision:
    if not manual:
        return LaneDecision(LANE_LOCAL, "automatic job")
    unavailable = cloud_unavailable_reason(settings)
    if unavailable:
        return LaneDecision(LANE_LOCAL, unavailable)
    if audio_seconds is not None and settings.max_episode_minutes:
        if audio_seconds > settings.max_episode_minutes * 60:
            return LaneDecision(
                LANE_LOCAL,
                f"episode longer than the {settings.max_episode_minutes} min "
                "cloud limit",
            )
    if spent_usd >= settings.monthly_cap_usd:
        return LaneDecision(LANE_LOCAL, "cloud monthly cap reached")
    if audio_seconds is None:
        return LaneDecision(
            LANE_CLOUD, "manual; length unknown, cap checked before upload"
        )
    ok, estimate = fits_budget(settings, spent_usd, audio_seconds)
    if not ok:
        return LaneDecision(LANE_LOCAL, "would exceed the cloud monthly cap")
    return LaneDecision(LANE_CLOUD, f"manual; est. ${estimate:.4f}")
