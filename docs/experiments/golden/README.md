# Real-transcript golden inclusion (no API keys)

Offline process for adding labeled Whisper-style transcripts to the scout
eval corpus. **Never** copy `.env` / `.env.local` or paste `GROQ_API_KEY` /
`GEMINI_API_KEY` into JSON, notes, or commit messages.

The Daily / Soft Skills **style** means structure (news briefing preroll +
midroll host-read, or interview with a sponsor read), not copyrighted
episode text. Do not check in NYT/Soft Skills transcripts.

## Flow

1. Export segments (`sequence_num`, `start_time`, `end_time`, `text`) from
   a local Whisper/Groq run. Keep keys in the environment, not the file.
2. Human-label `labeled_ads` as `[start, end, kind, notes]` seconds. Use
   `[]` only when the episode is confirmed ad-free.
3. Validate and stage (no network):

   ```bash
   PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py \
     --ingest path/to/labeled.json \
     --source-style news_briefing \
     --staging-dir docs/experiments/golden/staging
   ```

   Ingest **refuses** secret-like strings (`gsk_`, `AIza`, `sk-`,
   `Bearer …`, `GROQ_API_KEY=…`) and forbidden key names (`api_key`,
   `authorization`, …). Staging is gitignored.
4. Review the staged JSON. If it is honest and hash-stable, add a builder
   in `src/podcast_processor/experiments/fixtures.py` (or copy the JSON
   into corpus generation) and run:

   ```bash
   PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py \
     --write-corpus --update-baseline --check-baseline
   ```

   Commit corpus + snapshot + RESULTS + hypothesis last_result together.
   Do **not** loosen `gates.json`.
5. Production `AdClassifier` and `enable_bow_scout_gemini_confirm` stay
   unchanged.

## Templates

Synthetic skeletons (not real shows) are produced by
`example_the_daily_style_payload()` and
`example_soft_skills_style_payload()` in
`src/podcast_processor/experiments/golden_ingest.py`. Hypothesis `H004`
runs those validators on every daily loop. `H005` / `H006` expand the
same skeletons into `soft_skills_style_interview()` and
`news_briefing_style_code_cta()` in `fixtures.py` (still synthetic).
`H005` promoted `soft_skills_style_interview` into corpus v1 on
2026-10-01. `H006` remains experiment-only until that hypothesis runs.
