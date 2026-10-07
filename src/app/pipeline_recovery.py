"""Writes that move a job out of "running" between pipeline stages, and what
happens when they fail.

A job between stages is still ``running`` until its hand-off write lands, and
while it is, it holds a slot of its worker pool (the local transcriber has one)
and blocks its post. So a hand-off write is retried, then the job is failed;
if even that cannot be written, the worker pool sweeps it (see
``requeue_orphans``) before its next claim.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from app.writer.client import writer_client

logger = logging.getLogger("global_logger")

HANDOFF_ATTEMPTS = 3
HANDOFF_RETRY_SECONDS = 1.0


def write_with_retry(
    app_context: Callable[[], Any], action: str, params: dict[str, Any], job_id: str
) -> tuple[dict[str, Any] | None, str]:
    """(result data, "") on success; (None, last error) after all attempts."""
    error = ""
    for attempt in range(1, HANDOFF_ATTEMPTS + 1):
        try:
            with app_context():
                result = writer_client.action(action, params, wait=True)
            if result and result.success:
                return result.data or {}, ""
            error = str(getattr(result, "error", None) or "no result")
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__
        logger.warning(
            "[PIPELINE] %s for job %s failed (attempt %d/%d): %s",
            action,
            job_id,
            attempt,
            HANDOFF_ATTEMPTS,
            error,
        )
        if attempt < HANDOFF_ATTEMPTS:
            time.sleep(HANDOFF_RETRY_SECONDS * attempt)
    return None, error


def hand_off(
    app_context: Callable[[], Any], action: str, params: dict[str, Any], job_id: str
) -> tuple[dict[str, Any] | None, bool]:
    """Run a hand-off write with retries; if it never lands, fail the job.

    Returns (data, stuck): data of the hand-off write (None if it failed), and
    stuck=True if the job could not be failed either and may still be running
    with no thread (the caller must have its pool swept).
    """
    data, error = write_with_retry(app_context, action, params, job_id)
    if data is not None:
        return data, False
    logger.error(
        "[PIPELINE] %s for job %s failed after %d attempts (%s); failing the job",
        action,
        job_id,
        HANDOFF_ATTEMPTS,
        error,
    )
    failed, fail_error = write_with_retry(
        app_context,
        "fail_job_if_running",
        {"job_id": job_id, "error_message": f"Pipeline hand-off failed: {error}"[:500]},
        job_id,
    )
    if failed is None:
        logger.error(
            "[PIPELINE] could not fail job %s either (%s); re-queueing it on the "
            "next claim of its pool",
            job_id,
            fail_error,
        )
        return None, True
    return None, False


def requeue_orphans(pool_name: str, stage: str, lane: str, owned: set[str]) -> bool:
    """Re-queue running jobs of one pool whose post is not in ``owned``.

    The caller holds the claim lock, so ``owned`` is every post this process
    really runs. The writer runs commands in order, so this lands after any
    dequeue whose reply was lost. False if it could not be written.
    """
    try:
        result = writer_client.action(
            "requeue_orphaned_jobs",
            {"stage": stage, "lane": lane, "owned_post_guids": sorted(owned)},
            wait=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Orphan sweep for %s failed: %s", pool_name, exc)
        return False
    if not (result and result.success):
        logger.error(
            "Orphan sweep for %s failed: %s", pool_name, getattr(result, "error", None)
        )
        return False
    requeued = (result.data or {}).get("job_ids") or []
    if requeued:
        logger.error(
            "[PIPELINE] pool=%s re-queued %d job(s) no worker owned: %s",
            pool_name,
            len(requeued),
            requeued,
        )
    return True
