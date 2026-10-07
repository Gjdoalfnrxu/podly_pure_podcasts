# Processing lanes: free slow lane, paid fast lane

Podly normally transcribes with local Whisper on the CPU, one episode at a time.
That is free but slow. The **cloud fast lane** sends the audio of jobs that
someone asked for by hand to an OpenAI-compatible transcription API (Groq by
default) instead. It costs a little, with a monthly cap you set. Ad detection
uses your normal LLM settings in both lanes.

## Which lane a job uses

The rules are in `src/app/lanes.py::decide_lane`. The first match wins:

1. **Automatic jobs go local.** These are new episodes from feed refreshes, the
   latest episode of a newly added feed, processing that a podcast app
   triggers by downloading, and any re-queue done by housekeeping (for
   example the refresh-time "audio file missing" reset).
2. **Reprocess (keep transcript) goes local.** There is nothing to transcribe,
   so there is nothing to pay for.
3. **Manual jobs go to the cloud lane** if all of these hold. Manual means the
   Process and Reprocess buttons, and whitelisting an episode with "process
   now".
   - The lane is enabled. It is **off by default**.
   - An API key is set.
   - The monthly cap is above $0, and the price per hour is above $0 (a $0
     price would make every estimate $0 and switch the cap off).
   - The episode is not longer than the optional "max episode length".
   - The episode's estimated cost fits in what is left of this month's cap. The
     estimate uses the feed's episode length. If the length is unknown, the job
     still goes to cloud and the check happens before upload (point 3).
   Otherwise the job goes local. The reason is stored on the job and shown on
   the Jobs page. The lane is written in the same insert that queues the job,
   so a worker can never pick it up from the wrong queue; housekeeping
   re-queues reset it to local. Only a manual request can put a job in the
   cloud queue.
4. **Checked again before upload.** The cloud worker downloads the episode,
   measures the real length, and reserves the estimated cost. The reservation is
   a single writer action, so two cloud jobs cannot both squeeze under the cap.
   If the reservation is refused or cannot be made, the cloud processor cannot
   be set up, or the API call fails or times out (120 s per request, no
   automatic retries), the job is **put back in the local queue**. It is never
   dropped. A job cancelled during the upload is not re-queued.
5. If a manual request comes in for an episode that is already queued locally,
   the job moves to the cloud lane. An automatic trigger never moves a cloud job
   back to local.

Not built, on purpose: "use local for manual jobs when the local queue is empty
or it is off-peak". Local Whisper on this CPU is slow even when idle, a manual
job means someone is waiting, the cost per episode is small (about $0.04 per
audio hour), and the cap already bounds spend.

## Concurrency

Lanes only decide **who transcribes** a job. Jobs then run in two stages, each
with its own workers (see [pipeline.md](pipeline.md)):

- **Transcribe stage, local lane:** `PODLY_TRANSCRIBE_WORKERS` threads (default 1).
- **Transcribe stage, cloud lane:** two threads, alongside the local one. Each
  cloud job gets a fresh processor, so nothing is shared between them.
- **LLM stage** (ad detection and the audio cut, any lane): `PODLY_LLM_WORKERS`
  threads (default 4). Download, ffmpeg cutting and LLM calls run on this box.

## Money

Every cloud call creates a `cloud_lane_usage` row. It is `reserved` with the
estimate before upload, then settled to `charged` (or `failed`) with the billed
seconds.

- **Billed seconds:** the length of each chunk that was uploaded successfully,
  with the provider's 10 s minimum per request. Groq `whisper-large-v3-turbo`
  costs $0.04 per audio hour with a 10 s minimum per request
  (https://console.groq.com/docs/speech-to-text, checked 2026-10-07). The price
  per hour is a setting; set it to match your provider and model.
- **What counts toward the cap:** charged and failed rows count their computed
  cost. `reserved` rows (in flight, or interrupted by a restart) count their
  full estimate, because the provider may have billed them.
- **Calendar months are UTC.**
- **These figures are estimates** from audio length. They are not the
  provider's invoice; check provider billing for the real number.

## Settings

Configuration > **Fast lane** (admins): enable, API key (stored in the DB like
the other API keys, write-only in the API), base URL, model, language, price
per hour, monthly cap, and optional max episode length. The page also shows
this month's spend and both queues. The Jobs page shows each job's lane and
the reason it was given that lane.

API: `GET /api/lanes/status` (any logged-in user), `GET`/`PUT
/api/lanes/settings` (admin).

## Upgrading and rolling back

Migration `c1a0de1a9e5f` adds `processing_job.lane` and `processing_job.lane_reason`
and creates `cloud_lane_settings` and `cloud_lane_usage`. All changes are
additive. Before going back to an image without this migration, run the
downgrade inside the new image. The downgrade removes the two job columns and
**keeps both tables**, so this month's recorded spend (and the settings)
survive a downgrade followed by a later upgrade:

    docker exec -u appuser -w /app -e PYTHONPATH=/app/src \
      -e PODLY_RUN_STARTUP=false -e PODLY_DISABLE_SCHEDULER=true \
      podly /app/.venv/bin/flask --app "app:create_app" db downgrade 3e5eebc6b3b1

(Smoke-tested: this removes the columns, keeps the tables and sets the
revision back, and the previous image `podly-cain:2.5.0-opml-c294aa1` then
starts healthy on that database, extra tables and all. Without the downgrade,
the previous image did not become healthy.)

An older image's startup `upgrade()` stops with "Can't locate revision" if the
database is still at `c1a0de1a9e5f`.

## Known limits (honest list)

- **Transcripts are shared between lanes.** A transcript made by the cloud
  model is reused by the local lane and the other way round, as long as it came
  from the currently configured local model or the configured cloud model. A
  transcript from a model that is no longer configured is still treated as
  stale, as upstream does.

- **Episode length often unknown.** Many feeds don't give Podly an episode
  length, so the routing-time estimate is skipped ("length unknown") and the cap
  is only enforced at upload. Those jobs still respect the cap; they are just
  routed to cloud first and may then fall back.
- **Brief "failed" on fallback.** When the cloud call fails, the job shows as
  failed for a moment before it is re-queued locally.
- **What a failed request costs.** Each chunk is sent once (no SDK retries),
  with a 120 s timeout. If the request times out or the connection drops, the
  provider may still have processed it, so that chunk is **counted as billed**.
  If the provider answers with an error status (4xx/5xx), the chunk is counted
  as not billed.
- **Restarts.** On startup, jobs that were running are put back in their
  queue (at most twice per job, then it fails; see `docs/pipeline.md`);
  re-queued jobs go to the local lane (re-queues never use the cloud
  lane). Jobs that were still waiting keep their lane, so a manual cloud
  request that was waiting during a restart still goes to the cloud lane. A
  cloud call cut off by a restart stays `reserved` and keeps counting its
  estimate.
- **Spend is an estimate.** It comes from audio length times your price per
  hour. It is not read from the provider's invoice.
