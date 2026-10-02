# Agent eval contract: bow-scout + Gemini confirm

Offline, deterministic, **no API keys**. Use this before changing CueDetector,
AdClassifier prompts, AudioProcessor fade math, or the experiment package.
Do not flip production defaults.

## What must not change in production

| Knob | Required default |
| --- | --- |
| `Feed.ad_detection_strategy` | `llm` |
| `Config.enable_bow_scout_gemini_confirm` / `DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM` | `False` |
| `CueDetector()` | `include_scout_extras=False`; `analyze()` keys stay `CORE_ANALYZE_KEYS` |
| `PodcastProcessor._classify_ad_segments` | Always calls production `AdClassifier.classify` (even if the experimental flag is on, it currently logs and falls back) |

The scout+confirm path is eval-only until a later change explicitly wires it
behind the flag **and** this contract stays green on real transcripts.

## How to run the baseline

From repo root (same as CI; `./scripts/ci.sh` already runs pytest, which
includes the gates):

```bash
# Full lint + unit tests, including eval gates (no API keys)
./scripts/ci.sh

# Same gates plus a printable harness log + daily-loop --check
./scripts/ci.sh --eval

# Harness only
PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py --check-baseline
```

Live Gemini is **opt-in** and never required:

```bash
# Do not use in CI. Spends money.
export GEMINI_API_KEY=...
export PODLY_GEMINI_CONFIRM_LIVE=true
PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py
```

## Frozen corpus and snapshot

| Artifact | Path |
| --- | --- |
| Labeled transcript JSON + SHA-256 MANIFEST | `src/podcast_processor/experiments/corpus/v1/` |
| Baseline metrics snapshot | `docs/experiments/bow_scout_gemini_confirm/baseline/v1/snapshot.json` |
| Gate tolerances | `docs/experiments/bow_scout_gemini_confirm/baseline/v1/gates.json` |
| Human-readable table | `docs/experiments/bow_scout_gemini_confirm/RESULTS.md` |
| Hypothesis ledger | `docs/experiments/hypotheses/hypotheses.json` |
| Ranking function | `docs/experiments/SCORING.md` |
| Daily run artifacts | `docs/experiments/runs/YYYY-MM-DD/` |

Eval loads the corpus JSON (not the Python builders). Hash mismatches fail
tests on purpose.

## Numbers the scout path must beat (or not lose)

Compared to `snapshot.json` macro, CI fails if any of these fire:

| Gate | Meaning | Slack (ε) |
| --- | --- | --- |
| `scout_confirm_mean_time_recall` | Labeled-ad seconds kept after scout+confirm | must not drop > **0.02** |
| `scout_mean_ad_hit_rate` | Fraction of labeled ad *blocks* overlapping a scout window | must not drop > **0.02** |
| `scout_confirm_mean_time_f1` | Harmonic mean of confirm time P/R | must not drop > **0.03** |
| `scout_confirm_mean_time_precision` | Confirm spans vs labels | must not drop > **0.05** |
| `scout_mean_false_negative_rate` | `1 - ad_hit_rate` | must not rise > **0.02** |
| `scout_confirm_mean_residual_strong_cue_rate` | CueDetector strong hits left after confirm cuts / n_segments | must not rise > **0.01** |
| `mean_token_reduction_pct` | vs full AdClassifier walk | must not drop > **2.0** percentage points |
| `sum_scout_input_tokens` | Scout+confirm prompt tokens (chars/4) | must not rise > **10%** relative |
| `production_mean_time_recall` | Oracle production-like path | must not drop at all (ε = **0**) |
| `sum_full_input_tokens` | Full-walk prompt tokens | must not rise > **10%** relative |

Production CueDetector / AdClassifier / AudioProcessor unit suites
(`test_cue_detector.py`, `test_ad_classifier.py`, `test_audio_processor.py`,
`test_process_audio.py`) also run in `./scripts/ci.sh` / pytest. Deleting or
emptying those modules fails a dedicated existence gate.

Residual-cue rate is leftover **strong CueDetector hits**, not leftover
labeled ads. Cue-sparse host-reads (no URL/CTA/phone/sponsor phrase) show up
as false negatives, not residual cues.

## How to update the golden snapshot intentionally

Only after the quality/cost change is deliberate and documented in RESULTS:

```bash
PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py \
  --write-corpus --update-baseline --check-baseline
```

`--write-corpus` regenerates `corpus/v1/*.json` and `MANIFEST.json` from
`builder_fixtures()`. `--update-baseline` rewrites `snapshot.json` and
`gates.json`. Commit corpus + snapshot + RESULTS together.

Do **not** loosen `gates.json` tolerances to hide a regression.

## Daily loop contract (agents)

Absolute priorities: **confidence** (no regressions) → **detection**
(F1/recall) → **cost** (token reduction). Ranking math:
`docs/experiments/SCORING.md`.

### Pick H

1. Load `docs/experiments/hypotheses/hypotheses.json`.
2. Take `status=open` rows, sort by `metric_primary` in
   `(confidence, detection, cost)` then `id`.
3. Seeded ids after 2026-09-30 live run: `H001` measured (live
   confidence win 26→15 windows; offline corpus `no_win`), `H002`
   rejected (scout-token ε), `H003` measured/`no_win` (keep recommended
   pad), `H004` measured/`process_ok` (ingest process; no corpus
   promote). `H005` accepted — Soft Skills-style synthetic golden promoted
   to corpus v1. `H006` accepted 2026-10-01 afternoon — news-briefing-style
   synthetic golden (`news_briefing_style_code_cta`) promoted to corpus v1
   (n=11); TightPromo stays experiment-only. `H007` accepted 2026-10-01
   afternoon — duration-gated midroll probe folded into eval
   `DEFAULT_WINDOW_POSTPROCESS` (cue-sparse block hit 0→1; time recall
   0.961). `H008` accepted 2026-10-01 afternoon — recommended pad 12s/2
   segments (ties detection, scout tokens 5698→5337). Next open: `H010`
   (TightPromo as eval detector), then `H009` (wider duration-gated probe).

### Run

```bash
# Offline, no API keys. Updates last_result pointers in the ledger.
PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py

# One hypothesis
PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --hypothesis H001

# CI / --eval: validate + gates, do not rewrite hypotheses.json
PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --check

# Optional live Groq/Gemini (spends money). Hard cap default $0.50/day.
export PODLY_DAILY_BUDGET=0.50
export PODLY_EXPERIMENT_CACHE_DIR=docs/experiments/runs/.cache
export GEMINI_API_KEY=...          # and/or GROQ_API_KEY
# Cloud Agents live runs: GROQ_KEY is accepted as an alias of GROQ_API_KEY
# (canonical GROQ_API_KEY wins if both are set).
export GROQ_KEY=...                # optional alias for GROQ_API_KEY
export PODLY_GEMINI_CONFIRM_LIVE=true   # and/or PODLY_GROQ_CONFIRM_LIVE=true
PYTHONPATH=src uv run python scripts/experiments/run_daily_loop.py --live
```

Default path is **oracle-mock confirm** vs corpus v1 / frozen snapshot.
Live calls require both a key and a live flag, remaining budget, and the
content-hash disk cache (repeats are $0). If the cap would be exceeded,
the loop skips live and records the skip in `spend.json`.

Dated artifacts: `docs/experiments/runs/YYYY-MM-DD/` (`summary.json`,
per-hypothesis JSON, `spend.json`). Cache and golden staging are
gitignored.

### Gate, then fold

1. `compare_to_snapshot` must pass on the frozen recommended config
   before any hypothesis experiment runs. If it fails, stop and fix the
   regression. Do not loosen ε.
2. Score candidates with `scorer.rank_candidates`: reject gate failures;
   among survivors maximize confirm F1, then recall, then hit rate; then
   maximize token reduction.
3. **Fold** only a survivor that improved the hypothesis primary metric:
   update experiment-package code (`RECOMMENDED_CONFIG` or a candidate
   detector used by the eval), keep `enable_bow_scout_gemini_confirm=False`,
   keep `Feed.ad_detection_strategy=llm`, re-run `./scripts/ci.sh --eval`.
4. Mark the hypothesis `accepted` (fold-eligible) or `rejected` /
   `measured` via the runner. Production `AdClassifier.classify` stays
   on the hot path.

### When baselines may update

Allowed **only** as an explicit, documented step — never from the daily
loop automatically:

```bash
PYTHONPATH=src uv run python scripts/experiments/run_bow_scout_eval.py \
  --write-corpus --update-baseline --check-baseline
```

`--write-corpus` regenerates `corpus/v1/*.json` and `MANIFEST.json` from
`builder_fixtures()`. `--update-baseline` rewrites `snapshot.json` and
`gates.json`. Commit corpus + snapshot + RESULTS together.

All of these must be true:

- The candidate already passed frozen ε (a raise of quality/cost, not a hide).
- `gates.json` tolerances are unchanged (never widened).
- Corpus JSON + `MANIFEST.json` + `snapshot.json` + RESULTS notes are
  committed together.
- Production CueDetector constructor, `CORE_ANALYZE_KEYS`, and the
  scout feature flag are still the required defaults above.
- Real-transcript goldens went through `docs/experiments/golden/README.md`
  (secret scan, no keys in git).

If recall/F1/tokens would miss the old snapshot, **do not** update the
snapshot to match a worse run.


## Paths the harness compares

1. **Production-like (mocked)** — full AdClassifier chunk plan for tokens;
   labels = fixture ads (oracle LLM) + production `CueDetector` neighbor
   expansion (`window=5`, extras off).
2. **Scout** — `BowScout` windows at the recommended config.
3. **Scout + confirm** — Gemini confirm over those windows. Default mock is
   `oracle` (keeps labeled overlap inside windows). Optional live Gemini
   behind env flags.

Duration stubs use `output_ms ≈ source_ms − Σ ad_ms + 2 × fade_ms × n_cuts`
(`DEFAULTS.OUTPUT_FADE_MS`). ffmpeg mux jitter (~56ms in
`test_process_audio.py`) is not part of the stub.

## Adding a labeled fixture

1. Add a builder in `src/podcast_processor/experiments/fixtures.py`.
2. Run `--write-corpus --update-baseline`.
3. Explain the new row in RESULTS / this file.
4. Keep labels honest: known ad intervals only.
5. Real Whisper goldens: `docs/experiments/golden/README.md` (secret-safe
   ingest). Hypothesis `H004`. Automated multi-show gold (not finance-only):
   `docs/experiments/auto_gold/README.md` — does not flip
   `enable_bow_scout_gemini_confirm`.

