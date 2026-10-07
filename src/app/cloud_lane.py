"""Cloud fast lane runtime: lane choice for new jobs, the budgeted cloud
transcriber, and the per-job processor used by the cloud workers."""

from __future__ import annotations

import logging
import math
import os
from typing import Any, NoReturn

from openai import APIConnectionError, OpenAI
from openai.types.audio.transcription_segment import TranscriptionSegment

from app.extensions import db
from app.lane_store import load_lane_settings, month_spent_usd
from app.lanes import (
    LaneDecision,
    LaneSettings,
    billed_seconds_for_chunks,
    cloud_unavailable_reason,
    decide_lane,
    estimate_billed_seconds,
)
from app.models import ProcessingJob
from app.writer.client import writer_client
from podcast_processor.audio import get_audio_duration_ms
from podcast_processor.transcribe import OpenAIWhisperTranscriber, Segment
from shared import defaults as DEFAULTS
from shared.config import RemoteWhisperConfig

logger = logging.getLogger("global_logger")


class CloudLaneFallback(RuntimeError):
    """The cloud lane could not (or must not) transcribe; run the job locally."""


def choose_lane(
    *, manual: bool, audio_seconds: float | None, needs_transcription: bool = True
) -> LaneDecision:
    settings = load_lane_settings()
    spent = month_spent_usd() if manual else 0.0
    return decide_lane(
        manual=manual,
        settings=settings,
        spent_usd=spent,
        audio_seconds=audio_seconds,
        needs_transcription=needs_transcription,
    )


class BudgetedCloudTranscriber(OpenAIWhisperTranscriber):
    """OpenAI-compatible cloud Whisper with a spend reservation around the call.

    Before uploading, reserves the estimated cost (writer action, atomic with the
    monthly-cap check). After the call, settles the reservation with the billed
    seconds of the chunks that succeeded. Any refusal or error sets
    ``fallback_reason`` and raises CloudLaneFallback.
    """

    def __init__(
        self,
        settings: LaneSettings,
        *,
        job_id: str | None,
        post_guid: str,
        logger_obj: logging.Logger | None = None,
    ) -> None:
        super().__init__(
            logger_obj or logger,
            RemoteWhisperConfig(
                base_url=settings.base_url,
                api_key=settings.api_key or "",
                model=settings.model,
                language=settings.language,
                timeout_sec=DEFAULTS.CLOUD_LANE_TIMEOUT_SEC,
                chunksize_mb=DEFAULTS.CLOUD_LANE_CHUNKSIZE_MB,
            ),
        )
        # One attempt, short timeout: the SDK's default 2 retries x 600 s could
        # stall a worker for half an hour and bill requests we never count.
        self.openai_client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "",
            timeout=DEFAULTS.CLOUD_LANE_TIMEOUT_SEC,
            max_retries=0,
        )
        self.settings = settings
        self.job_id = job_id
        self.post_guid = post_guid
        self.fallback_reason: str | None = None
        self._chunk_seconds: list[float] = []

    def _fallback(self, reason: str, cause: BaseException | None = None) -> NoReturn:
        self.fallback_reason = reason
        raise CloudLaneFallback(reason) from cause

    def get_segments_for_chunk(self, chunk_path: str) -> list[TranscriptionSegment]:
        try:
            segments = super().get_segments_for_chunk(chunk_path)
        except APIConnectionError:
            # Timeout or dropped connection: the provider may have processed
            # (and billed) the upload, so count the chunk. Error responses
            # (4xx/5xx status) are not counted.
            self._count_chunk(chunk_path)
            raise
        self._count_chunk(chunk_path)
        return segments

    def _count_chunk(self, chunk_path: str) -> None:
        duration_ms = get_audio_duration_ms(chunk_path)
        self._chunk_seconds.append((duration_ms or 0) / 1000.0)

    def _job_cancelled(self) -> bool:
        if not self.job_id:
            return False
        db.session.expire_all()
        job = db.session.get(ProcessingJob, self.job_id)
        return job is not None and job.status == "cancelled"

    def transcribe(self, audio_file_path: str) -> list[Segment]:
        unavailable = cloud_unavailable_reason(self.settings)
        if unavailable:
            self._fallback(unavailable)

        duration_ms = get_audio_duration_ms(audio_file_path)
        if duration_ms is None:
            self._fallback("could not read audio duration for the cost check")
        audio_seconds = (duration_ms or 0) / 1000.0
        max_minutes = self.settings.max_episode_minutes
        if max_minutes and audio_seconds > max_minutes * 60:
            self._fallback(f"episode longer than the {max_minutes} min cloud limit")

        chunk_bytes = DEFAULTS.CLOUD_LANE_CHUNKSIZE_MB * 1024 * 1024
        chunks = max(1, math.ceil(os.path.getsize(audio_file_path) / chunk_bytes))
        try:
            reservation = self._reserve(audio_seconds, chunks)
        except Exception as exc:  # noqa: BLE001 - e.g. writer timeout
            self._fallback(f"could not reserve cloud budget: {exc}", exc)
        if not reservation.get("reserved"):
            self._fallback("would exceed the cloud monthly cap")
        usage_id = reservation["usage_id"]

        try:
            segments = super().transcribe(audio_file_path)
        except Exception as exc:
            self._settle(usage_id, "failed", error=str(exc)[:500])
            if self._job_cancelled():
                # Cancelled while uploading: do not resurrect it locally.
                raise
            self._fallback(f"cloud transcription failed: {exc}", exc)
        self._settle(usage_id, "charged")
        return segments

    def _reserve(self, audio_seconds: float, chunks: int) -> dict[str, Any]:
        return _writer(
            "reserve_cloud_usage",
            {
                "job_id": self.job_id,
                "post_guid": self.post_guid,
                "model": self.settings.model,
                "audio_seconds": audio_seconds,
                "estimated_billed_seconds": estimate_billed_seconds(
                    audio_seconds, chunks
                ),
                "usd_per_hour": self.settings.usd_per_hour,
                "cap_usd": self.settings.monthly_cap_usd,
            },
        )

    def _settle(self, usage_id: int, status: str, error: str | None = None) -> None:
        try:
            _writer(
                "settle_cloud_usage",
                {
                    "usage_id": usage_id,
                    "status": status,
                    "billed_seconds": billed_seconds_for_chunks(self._chunk_seconds),
                    "error": error,
                },
            )
        except Exception as exc:  # noqa: BLE001
            # The reservation stays "reserved" and keeps counting its estimate.
            logger.error("Failed to settle cloud usage %s: %s", usage_id, exc)


def _writer(action: str, params: dict[str, Any]) -> dict[str, Any]:
    result = writer_client.action(action, params, wait=True)
    if not result or not result.success or not isinstance(result.data, dict):
        raise RuntimeError(getattr(result, "error", None) or f"{action} failed")
    return result.data


def build_cloud_processor(
    job_id: str, post_guid: str
) -> tuple[Any, BudgetedCloudTranscriber]:
    """A fresh processor per cloud job (no state shared between the 2 workers)."""
    from app.runtime_config import config
    from podcast_processor.podcast_processor import PodcastProcessor
    from podcast_processor.transcription_manager import TranscriptionManager

    transcriber = BudgetedCloudTranscriber(
        load_lane_settings(), job_id=job_id, post_guid=post_guid
    )
    processor = PodcastProcessor(
        config,
        transcription_manager=TranscriptionManager(
            logger, config, transcriber=transcriber
        ),
    )
    return processor, transcriber
