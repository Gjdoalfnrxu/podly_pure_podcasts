"""OPML subscription import: ``POST /api/feeds/import-opml``.

Accepts a multipart upload (field ``file``) or a raw XML request body. Each
feed goes through the same ``subscribe_to_feed`` path as ``POST /feed``.

Options (form field or query string):
  process_latest=true|false  default true (same as a normal add: the latest
      episode of each newly-joined feed is queued for processing). false adds
      the feeds without queueing anything and un-whitelists the backlog of
      feeds created by this import; future episodes follow normal settings.
"""

import logging
from typing import IO, Any

from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from app.extensions import db
from app.models import User
from app.opml import OpmlParseError, parse_opml
from app.routes.feed_routes import _require_user_or_error
from app.routes.feed_subscribe import (
    is_subscribed,
    start_enqueue_pending_jobs,
    subscribe_to_feed,
)
from app.routes.feed_utils import fix_url

logger = logging.getLogger("global_logger")

opml_bp = Blueprint("opml", __name__)

MAX_OPML_BYTES = 2 * 1024 * 1024
MAX_OPML_FEEDS = 500


def _read_limited(stream: IO[bytes]) -> bytes | None:
    data = stream.read(MAX_OPML_BYTES + 1)
    return None if len(data) > MAX_OPML_BYTES else data


def _too_large() -> ResponseReturnValue:
    return (
        jsonify({"error": f"OPML file exceeds {MAX_OPML_BYTES // (1024 * 1024)} MB"}),
        413,
    )


def _parse_bool(raw: str | None, default: bool) -> bool | None:
    if raw is None or raw == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return None


def _read_payload() -> tuple[bytes | None, ResponseReturnValue | None]:
    if request.content_length is not None and request.content_length > (
        MAX_OPML_BYTES + 64 * 1024  # multipart framing overhead
    ):
        return None, _too_large()

    upload = request.files.get("file")
    if upload is not None:
        data = _read_limited(upload.stream)
    elif request.mimetype in (
        "multipart/form-data",
        "application/x-www-form-urlencoded",
    ):
        data = b""
    else:
        data = _read_limited(request.stream)
    if data is None:
        return None, _too_large()
    if not data.strip():
        return None, (
            jsonify({"error": "No OPML file provided (field 'file')."}),
            400,
        )
    return data, None


def _unique_urls(data: bytes) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for entry in parse_opml(data):
        url = fix_url(entry.url)
        if url not in seen:
            seen.add(url)
            urls.append(url)
    return urls


def _import_urls(
    urls: list[str], user: User | None, process_latest: bool
) -> dict[str, Any]:
    added: list[str] = []
    skipped_existing: list[str] = []
    failed: list[dict[str, str]] = []
    for url in urls:
        if is_subscribed(url, user):
            skipped_existing.append(url)
            continue
        try:
            subscribe_to_feed(url, user, process_latest=process_latest)
            added.append(url)
        except Exception as exc:  # noqa: BLE001
            db.session.rollback()
            logger.warning("OPML import: failed to add %s: %s", url, exc)
            failed.append({"url": url, "error": str(exc) or exc.__class__.__name__})
    return {"added": added, "skipped_existing": skipped_existing, "failed": failed}


@opml_bp.route("/api/feeds/import-opml", methods=["POST"])
def import_opml() -> ResponseReturnValue:
    settings = current_app.config.get("AUTH_SETTINGS")
    user = None
    if settings and settings.require_auth:
        user, error = _require_user_or_error()
        if error:
            return error

    data, error = _read_payload()
    if error is not None or data is None:
        return error or _too_large()

    process_latest = _parse_bool(
        request.form.get("process_latest", request.args.get("process_latest")),
        default=True,
    )
    if process_latest is None:
        return jsonify({"error": "process_latest must be true or false."}), 400

    try:
        urls = _unique_urls(data)
    except OpmlParseError as exc:
        return jsonify({"error": str(exc)}), 400

    if len(urls) > MAX_OPML_FEEDS:
        return (
            jsonify(
                {"error": f"OPML lists {len(urls)} feeds; limit is {MAX_OPML_FEEDS}."}
            ),
            400,
        )

    summary = _import_urls(urls, user, process_latest)
    if summary["added"] and process_latest:
        start_enqueue_pending_jobs()

    logger.info(
        "OPML import: %d added, %d already subscribed, %d failed",
        len(summary["added"]),
        len(summary["skipped_existing"]),
        len(summary["failed"]),
    )
    return jsonify({**summary, "process_latest": process_latest})
