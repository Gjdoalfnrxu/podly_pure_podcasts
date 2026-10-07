"""Background OPML import jobs.

An import does one network fetch per feed, so it runs off the request thread
(waitress serves with SERVER_THREADS, default 1) and the UI polls its status.
Job state is in-memory: a restart drops status, not subscriptions.
"""

import logging
import uuid
from dataclasses import dataclass, field
from threading import Lock, Thread
from typing import Any, cast

from flask import Flask, current_app

from app.extensions import db
from app.models import User
from app.routes.feed_subscribe import (
    _enqueue_pending_jobs_async,
    is_subscribed,
    subscribe_to_feed,
)

logger = logging.getLogger("global_logger")

FEED_FETCH_TIMEOUT_SECONDS = 30.0
MAX_KEPT_JOBS = 20


class ImportAlreadyRunningError(RuntimeError):
    pass


@dataclass
class ImportJob:
    id: str
    user_id: int | None
    urls: list[str]
    process_latest: bool
    status: str = "running"  # running | done | error
    added: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        with _LOCK:
            return {
                "import_id": self.id,
                "status": self.status,
                "total": len(self.urls),
                "processed": len(self.added)
                + len(self.skipped_existing)
                + len(self.failed),
                "added": list(self.added),
                "skipped_existing": list(self.skipped_existing),
                "failed": [dict(f) for f in self.failed],
                "process_latest": self.process_latest,
                "error": self.error,
            }


_LOCK = Lock()
_JOBS: dict[str, ImportJob] = {}


def get_import(import_id: str) -> ImportJob | None:
    with _LOCK:
        return _JOBS.get(import_id)


def start_import(
    urls: list[str], user_id: int | None, process_latest: bool
) -> ImportJob:
    job = ImportJob(
        id=uuid.uuid4().hex,
        user_id=user_id,
        urls=list(urls),
        process_latest=process_latest,
    )
    with _LOCK:
        if any(j.status == "running" and j.user_id == user_id for j in _JOBS.values()):
            raise ImportAlreadyRunningError("An OPML import is already running.")
        _JOBS[job.id] = job
        finished = [j.id for j in _JOBS.values() if j.status != "running"]
        for old_id in finished[: max(0, len(_JOBS) - MAX_KEPT_JOBS)]:
            del _JOBS[old_id]

    app = cast(Any, current_app)._get_current_object()
    Thread(
        target=_run_import,
        args=(app, job),
        daemon=True,
        name=f"opml-import-{job.id[:8]}",
    ).start()
    return job


def _record(job: ImportJob, bucket: str, value: Any) -> None:
    with _LOCK:
        getattr(job, bucket).append(value)


def import_urls(job: ImportJob, user: User | None) -> None:
    for url in job.urls:
        if is_subscribed(url, user):
            _record(job, "skipped_existing", url)
            continue
        try:
            result = subscribe_to_feed(
                url,
                user,
                process_latest=job.process_latest,
                fetch_timeout=FEED_FETCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("OPML import: failed to add %s: %s", url, exc)
            _record(
                job,
                "failed",
                {"url": url, "error": str(exc) or exc.__class__.__name__},
            )
            continue
        _record(job, "skipped_existing" if result.already_subscribed else "added", url)


def _run_import(app: Flask, job: ImportJob) -> None:
    with app.app_context():
        try:
            user = db.session.get(User, job.user_id) if job.user_id else None
            import_urls(job, user)
            if job.added and job.process_latest:
                _enqueue_pending_jobs_async(app)
            status = "done"
        except Exception as exc:
            logger.exception("OPML import %s crashed", job.id)
            job.error = str(exc) or exc.__class__.__name__
            status = "error"
        finally:
            db.session.remove()
        with _LOCK:
            job.status = status
        logger.info(
            "OPML import %s: %d added, %d already subscribed, %d failed",
            job.id,
            len(job.added),
            len(job.skipped_existing),
            len(job.failed),
        )
