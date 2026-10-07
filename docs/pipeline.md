# Stage pipeline: transcription and ad detection run at the same time

Podly 2.5.0 processed one episode at a time: download, Whisper, LLM ad
detection, audio cut, then the next episode. The CPU was idle while the LLM
worked, and the LLM was idle while Whisper worked.

Now each job runs in two **stages**, and each stage has its own worker threads:

| Stage | Work | Workers | Setting |
| --- | --- | --- | --- |
| `transcribe` | download, Whisper | 1 local (+2 cloud lane) | `PODLY_TRANSCRIBE_WORKERS` |
| `llm` | ad detection, audio cut, chapters | 4 | `PODLY_LLM_WORKERS` |

When episode A's transcript is stored, A goes back in the queue for the `llm`
stage and the transcriber starts episode B straight away. Up to 4 episodes are
in ad detection at once. Inside one episode the LLM chunks still run one after
another (each chunk overlaps the previous one), as before.

The code: `src/app/pipeline.py` (settings, stage names, priorities, pool size),
`src/app/pipeline_workers.py` (worker threads), `dequeue_job` /
`advance_job_stage` / `route_job` / `requeue_interrupted_jobs` in
`src/app/writer/actions/jobs.py`, and the `stage` argument of
`PodcastProcessor.process`.

## Which stage a job starts in

Decided when the job is queued (`_initial_stage` in `src/app/jobs_manager.py`):

- The post has a reusable transcript (same rule as "Reprocess (keep
  transcript)"): `llm`. So keep-transcript reprocesses skip Whisper and run in
  parallel with whatever Whisper is doing.
- Feed strategy `chapter` (no Whisper, no LLM): `llm`.
- Feed strategy `chapter_insert` (may need Whisper): `transcribe`, and the whole
  job runs there.
- Otherwise: `transcribe`. Jobs created by a feed refresh start here too; if a
  transcript already exists, the transcribe stage reuses it and hands the job on
  at once.

If the LLM stage finds no stored transcript, it sends the job back to
`transcribe` (it never runs Whisper itself). An episode whose transcript is empty
is finished in the transcribe stage, so it cannot bounce between stages.

## Order and guards

- **Priority:** interactive requests (process / reprocess buttons, a new feed's
  latest episode) run before podcast-app download requests, which run before the
  feed-refresh backlog. Equal priority runs oldest first. A later, lower-priority
  request never lowers a queued job's priority. (In 2.5.0 the priority was only
  written into the status text; the queue was oldest first.)
- **One post at a time:** `dequeue_job` never claims a post that has a running
  job, or that a worker thread here is still busy with (for example a job that
  was cancelled but whose thread has not finished yet).
- **Limits:** a stage with N threads runs at most N jobs. `dequeue_job` also
  refuses to go over N running jobs of that stage.
- **Cancel:** a job cancelled during transcription is not handed to the LLM
  stage.

## Status shown in the UI

| What the job is doing | Status | Step | Progress | Text |
| --- | --- | --- | --- | --- |
| waiting for Whisper | pending | 0 | 0% | Queued for processing (priority=…) |
| downloading | running | 1 | 25% | Downloading episode |
| Whisper | running | 2 | 50% | Transcribing audio |
| waiting for the LLM stage | pending | 2 | 50% | Transcribed; waiting for ad detection |
| LLM stage started | running | 2 | 50% | Starting ad detection |
| ad detection | running | 3 | 75% | Identifying ads |
| audio cut | running | 4 | 90% | Processing audio |

The Jobs page shows each active job's stage and a count of running and waiting
jobs per stage. The API (`/api/jobs/active`, `/api/jobs/all`) has a `stage`
field.

## Restarts

On startup, jobs that were running go back to pending in the same stage, and
pending jobs stay queued. Jobs that were in the LLM stage keep their transcript
and skip Whisper. (2.5.0 deleted pending and running jobs, and nothing came back
until the next feed refresh.)

## Audio cut

Cutting re-encodes the whole episode with ffmpeg. Measured in the upstream 2.5.0
image (ffmpeg 7.1) on a 60 min stereo 128 kbit/s MP3 with 4 ad breaks: about 30 s
wall time and 47 s CPU time per episode (3 runs: 32.0/48.0, 30.1/47.3,
29.0/47.0 s). That is about 1.5 cores, so cuts run one at a time
(`PODLY_AUDIO_CUT_CONCURRENCY`, default 1) and leave the CPU to Whisper.

## LLM calls

`LLM_MAX_CONCURRENT_CALLS` is one limit shared by all LLM-stage workers. If it is
lower than `PODLY_LLM_WORKERS`, the extra workers wait for it and episodes do not
overlap in the LLM. Set it to at least the number of LLM workers (the server must
handle that many requests at once). Podly logs a warning at startup when it is
lower.

## Database connections

The web process holds at most one pooled connection per request thread and per
pipeline worker, plus the background feed-refresh executor (2), the scheduler
thread (1) and 2 spare for short-lived import threads. `required_db_pool_size`
in `src/app/pipeline.py` adds these up, and the app sets `pool_size` from it
(`max_overflow` stays 5). With `SERVER_THREADS=4` and the defaults:
4 + (1 + 2 + 4) + 2 + 1 + 2 = **16** (2.5.0 had a fixed `pool_size` of 5). Each worker removes
its DB session when a stage run ends.

## Upgrading and rolling back

Migration `d7e3f1a2b4c5` adds `processing_job.stage` and
`processing_job.priority` (both additive). To go back to an image without it,
run the downgrade inside this image first:

    docker exec -u appuser -w /app -e PYTHONPATH=/app/src \
      -e PODLY_RUN_STARTUP=false -e PODLY_DISABLE_SCHEDULER=true \
      podly /app/.venv/bin/flask --app "app:create_app" db downgrade c1a0de1a9e5f
