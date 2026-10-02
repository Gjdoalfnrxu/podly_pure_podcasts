# Cake run: local Whisper + `.env.local`

Run the auto-gold harness on the LAN worker (Cake) where **local Whisper**
and a **Gemini/Google** key exist. Do not use `GROQ_KEY` for the judge.

## Once

```bash
cd /path/to/podly_pure_podcasts
# .env.local is gitignored. Copy from .env.local.example if needed.
# Required for the judge:
#   GEMINI_API_KEY=...          # or GEMINI_KEY / GOOGLE_API_KEY / GOOGLE_GENERATIVE_AI_API_KEY
# Whisper (local):
#   WHISPER_TYPE=local
#   WHISPER_LOCAL_MODEL=base.en   # or small.en
# GROQ_KEY / GROQ_API_KEY may exist for other tools; this script ignores them.
```

ffmpeg must be on `PATH`. `enable_bow_scout_gemini_confirm` stays false.

## Run

```bash
set -a && source .env.local && set +a
export PYTHONPATH=src
export WHISPER_TYPE=local
export WHISPER_LOCAL_MODEL="${WHISPER_LOCAL_MODEL:-base.en}"

uv run python scripts/experiments/run_auto_gold_baseline.py \
  --download \
  --enable-dsp \
  --enable-fingerprint \
  --whisper-mode local \
  --judge-mode auto \
  --output-dir "docs/experiments/auto_gold/runs/cake-$(date -u +%Y-%m-%d)"
```

Chunk WAVs and transcripts stay under that output dir (gitignored). Copy
`BASELINE_REPORT.md` + `metrics.json` off the box if you want them in git;
do **not** commit episode audio or `.env.local`.

If Whisper weights are missing, `whisper.load_model` will download them on
first use. If you must force the stub: `--whisper-mode stub`.

If the Gemini/Google key is missing, the runner dry-runs the judge and lists
`gemini_judge` under blocked steps. Do not point `--judge-mode gemini` at Groq.
