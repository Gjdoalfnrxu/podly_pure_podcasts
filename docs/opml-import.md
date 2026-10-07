# OPML import

`POST /api/feeds/import-opml` subscribes the current user to every feed in an
OPML export. Same auth as `POST /feed` (session login when auth is enabled).

Input: multipart field `file`, or the raw XML as the request body. Max 2 MB,
max 500 unique feeds. DOCTYPE/entity declarations are rejected (no XXE, no
entity expansion).

Every `<outline>` with an `xmlUrl` is used, including ones nested in
categories. URLs are normalised the same way as the single add, then deduped.
Feeds the user already follows are skipped. The rest go through
`subscribe_to_feed` (`app/routes/feed_subscribe.py`), the same function
`POST /feed` uses. One bad feed does not stop the rest.

Response:

```json
{"added": [], "skipped_existing": [], "failed": [{"url": "", "error": ""}], "process_latest": true}
```

## process_latest

Form field or query param, default `true`.

- `true`: same as adding each feed by hand. With auto-whitelist on, each new
  feed's latest episode (and up to "episodes to whitelist from archive") is
  queued, so N feeds queue about N episodes.
- `false`: nothing is queued. A feed created by the import stores its whole
  backlog un-whitelisted, in the same writer call that would otherwise create
  the jobs, so there is no race with the job worker. Episodes released later
  follow the normal auto-whitelist settings. Feeds that already existed on the
  server (shared with other users) are refreshed as normal and not changed.

The UI has this as the "Process the latest episode of each feed" checkbox in
Add Feed > Import OPML.
