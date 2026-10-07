# Processing lanes: free slow lane, paid fast lane

Podly normally transcribes with local Whisper on the CPU, one episode at a time.
That is free but slow. The **cloud fast lane** sends the audio of jobs that
someone asked for by hand to an OpenAI-compatible transcription API (Groq by
default) instead. It costs a little, with a monthly cap you set. Ad detection
uses your normal LLM settings in both lanes.

## Which lane a job uses

The rules are in `src/app/lanes.py::decide_lane`. The first match wins:

1. **Automatic jobs go local.** These are new episodes from feed refreshes, the
   latest episode of a newly added feed, and processing that a podcast app
   triggers by downloading.
2. **Manual jobs go to the cloud lane** if all of these hold. Manual means the
   Process, Reprocess and Reprocess (keep transcript) buttons, and whitelisting
   an episode with "process now".
   - The lane is enabled. It is **off by default**.
   - An API key is set.
   - The monthly cap is above $0.
   - The episode is not longer than the optional "max episode length".
   - The episode's estimated cost fits in what is left of this month's cap. The
     estimate uses the feed's episode length. If the length is unknown, the job
     still goes to cloud and the check happens before upload (point 3).
   Otherwise the job goes local. The reason is stored on the job and shown on
   the Jobs page.
3. **Checked again before upload.** The cloud worker downloads the episode,
   measures the real length, and reserves the estimated cost. The reservation is
   a single writer action, so two cloud jobs cannot both squeeze under the cap.
   If the reservation is refused, or the API call fails or times out (10 min per
   request), the job is **put back in the local queue**. It is never dropped.
4. If a manual request comes in for an episode that is already queued locally,
   the job moves to the cloud lane. An automatic trigger never moves a cloud job
   back to local.

Not built, on purpose: "use local for manual jobs when the local queue is empty
or it is off-peak". Local Whisper on this CPU is slow even when idle, a manual
job means someone is waiting, the cost per episode is small (about $0.04 per
audio hour), and the cap already bounds spend.

## Concurrency

- **Local lane:** one job at a time (`_global_processing_lock`), as before.
- **Cloud lane:** two worker threads, so up to 2 cloud jobs run alongside the
  local one. Each cloud job gets a fresh processor, so nothing is shared
  between them. Download, ffmpeg cutting and LLM calls still run on this box.

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
downgrade inside the new image:

    flask --app main db downgrade 3e5eebc6b3b1

An older image's startup `upgrade()` stops with "Can't locate revision" if the
database is still at `c1a0de1a9e5f`.
