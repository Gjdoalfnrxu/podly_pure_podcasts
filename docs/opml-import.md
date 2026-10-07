# OPML import

`POST /api/feeds/import-opml` subscribes the current user to every feed in an
OPML export. Same auth as `POST /feed` (session login when auth is enabled).

Input: multipart field `file`, or the raw XML as the request body. Max 2 MB,
max 500 unique feeds. DOCTYPE/entity declarations are rejected (no XXE, no
entity expansion). The file is validated synchronously (400/413 on bad input);
the import itself runs in a background thread and the POST returns `202` with
the job state. Poll `GET /api/feeds/import-opml/<import_id>` until `status` is
`done` or `error`. One import per user at a time: a second POST gets `409`
with the running job under `running`, and the UI resumes polling it. Job status
is in memory: a restart loses it, not the subscriptions already made.

Each feed fetch on the import path has a hard wall-clock limit of 30 s that
covers DNS, connect, TLS, redirects (at most 5), headers and body, plus a
64 MB cap. The fetch runs in a worker thread; on expiry the import moves on
and a watchdog shuts down the connections the fetch opened (HTTP, HTTPS, and
HTTP(S) proxies from `HTTP_PROXY`/`HTTPS_PROXY`), so the worker exits. Behind
a SOCKS proxy the import is still bounded, but the worker can outlive the
limit until its socket times out. The plain
`feedparser.parse(url)` used elsewhere is unchanged. A running import that
makes no progress for 5 minutes is treated as dead and no longer blocks a new
one. If the importing user is deleted mid-import, the job stops with an error.

Every `<outline>` with an `xmlUrl` is used, including ones nested in
categories. URLs are normalised the same way as the single add, then deduped.
Feeds the user already follows are skipped, including feeds stored under
their post-redirect URL. The rest go through `subscribe_to_feed`
(`app/routes/feed_subscribe.py`), the same function `POST /feed` uses. One bad
feed does not stop the rest.

Job state:

```json
{"import_id": "", "status": "running|done|error", "total": 0, "processed": 0,
 "added": [], "skipped_existing": [], "failed": [{"url": "", "error": ""}],
 "process_latest": true, "error": null}
```

## process_latest

Form field or query param. API default `true` (same as `POST /feed`); the UI
checkbox defaults to unticked for bulk imports.

- `true`: same as adding each feed by hand. With auto-whitelist on, each new
  feed's latest episode (and up to "episodes to whitelist from archive") is
  queued, so N feeds queue about N episodes.
- `false`: feeds that are new to the server store their whole backlog
  un-whitelisted, in the same writer call that would otherwise create the
  jobs, so nothing is queued for them and there is no race with the job
  worker. Feeds that already exist on the server (added by another user) are
  refreshed as a normal add would, and that refresh can whitelist newly
  released episodes per the auto-whitelist settings. Episodes released later
  follow the normal settings either way.

## Server threads

waitress serves with `SERVER_THREADS` (default 1). The import no longer holds
a request thread, but podcast-app polling, the UI and audio downloads still
share the pool; 4 is a reasonable value for a small instance.
