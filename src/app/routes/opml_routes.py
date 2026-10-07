"""OPML subscription import.

``POST /api/feeds/import-opml`` accepts a multipart upload (field ``file``) or
a raw XML body, validates and parses it synchronously, then runs the import in
a background job and returns 202 with the job state. Poll
``GET /api/feeds/import-opml/<import_id>`` until ``status`` is not "running".
Each feed goes through the same ``subscribe_to_feed`` path as ``POST /feed``.

Options (form field or query string):
  process_latest=true|false  default true, same as a normal add (the latest
      episode of each newly-joined feed is queued). false: feeds new to the
      server are stored with no episodes whitelisted and nothing is queued
      for them. Feeds that already existed are refreshed as usual, which can
      whitelist newly released episodes per the auto-whitelist settings.
"""

from typing import IO

from flask import Blueprint, current_app, jsonify, request
from flask.typing import ResponseReturnValue

from app.opml import OpmlParseError, parse_opml
from app.opml_import import ImportAlreadyRunningError, get_import, start_import
from app.routes.feed_routes import _require_user_or_error
from app.routes.feed_utils import fix_url

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

    try:
        job = start_import(urls, getattr(user, "id", None), process_latest)
    except ImportAlreadyRunningError as exc:
        # Include the running job so the UI can resume polling it.
        return jsonify({"error": str(exc), "running": exc.job.to_dict()}), 409
    return jsonify(job.to_dict()), 202


@opml_bp.route("/api/feeds/import-opml/<string:import_id>", methods=["GET"])
def import_opml_status(import_id: str) -> ResponseReturnValue:
    settings = current_app.config.get("AUTH_SETTINGS")
    user = None
    if settings and settings.require_auth:
        user, error = _require_user_or_error()
        if error:
            return error

    job = get_import(import_id)
    if job is None or job.user_id != getattr(user, "id", None):
        return jsonify({"error": "Import not found."}), 404
    return jsonify(job.to_dict())
