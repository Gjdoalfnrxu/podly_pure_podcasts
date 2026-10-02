# Auto gold baseline (`general_podcast_ads`)

Fully automated gold labels for podcast **ad-removal eval**. Sampling unit is
the **show**. Locked family: **`general_podcast_ads`**.

This is **not** a two-show finance sample. `shows.json` covers news, comedy,
true crime, tech, sports, and finance (Planet Money + Marketplace are two of
twelve rows, different publishers — not Planet Money + The Indicator).

Production `AdClassifier` is not used. `enable_bow_scout_gemini_confirm`
stays **false**.

## Pipeline

1. **Sample.** One recent episode per curated show (RSS).
2. **Candidates (high recall, metadata/audio only).**
   - Always **0–90s** preroll.
   - Publisher chapter markers as **positives only** (missing markers ≠ ad-free).
   - Optional fingerprint near-dupe of the preroll (ffmpeg energy histogram).
   - Optional DSP: ffmpeg `silencedetect` (easy DAI/break gaps).
   - Optional DAI-host midroll probes at 33%/66% duration when the enclosure
     host looks like Megaphone/Art19/Podtrac/etc.
   - Merge with pad **±1–2s**.
3. **Whisper on those chunks only.** Local `openai-whisper` when importable;
   otherwise a **stub** (Cloud VMs without torch). Cake run: `CAKE_RUN.md`.
4. **Judge LLM** on the chunk transcript. Gemini via `GEMINI_API_KEY` /
   `GEMINI_KEY` / `GOOGLE_API_KEY` / `GOOGLE_GENERATIVE_AI_API_KEY`.
   **`GROQ_KEY` is unused.** Missing key → dry-run skip.
5. **Report.** Fills `BASELINE_REPORT.md` section markers.

## Circularity (do not treat this gold as independent truth)

1. **Whisper-shaped gold.** Only proposed chunks are transcribed. Ads outside
   the candidate union never enter gold. Whisper timing/wording (and ASR
   hallucinations such as a fake “use code”) become the judge’s input.
2. **Same-family judge.** Gold uses Gemini. The scout±confirm experiment uses
   the same model family; production `AdClassifier` is also an LLM classifier.
   Agreement between gold Gemini and confirm Gemini **overstates** true
   accuracy.

Human review is required before promoting labels into corpus v1.

## Run

```bash
# Offline: catalog + stubs, no network (CI / this harness smoke)
PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py --offline

# RSS-only (1 latest episode metadata per show; no audio, Whisper stub, judge dry-run)
PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py

# Full (Cake / a box with local Whisper + Gemini key)
PYTHONPATH=src uv run python scripts/experiments/run_auto_gold_baseline.py \
  --download --enable-dsp --enable-fingerprint \
  --whisper-mode local --judge-mode auto
```

Outputs (gitignored): `docs/experiments/auto_gold/runs/<utc-date>/`
`BASELINE_REPORT.md` and `metrics.json`.

On a Cursor cloud VM, expect: RSS probe works if egress allows; Whisper stubs
(no torch); judge dry-runs unless a Gemini/Google key is in the environment;
`GROQ_KEY` may be present and is ignored.

## Files

| Path | Role |
| --- | --- |
| `shows.json` | Curated RSS catalog (locked family + genres) |
| `BASELINE_REPORT.md` | Template; runner fills `AUTO_GOLD_*` markers |
| `CAKE_RUN.md` | LAN worker with local Whisper + `.env.local` |
| `src/podcast_processor/experiments/auto_gold/` | Library |
| `scripts/experiments/run_auto_gold_baseline.py` | CLI |
