# Daily loop live results — 2026-09-30

**Generated:** 2026-09-30 16:06:19 PT  
**Branch:** `cursor/bow-scout-gemini-confirm-15bb`  
**Priorities:** confidence → detection → spend  
**Models:** Whisper `whisper-large-v3-turbo` @ $0.04/hr; Confirm `gemini-3.1-flash-lite` @ $0.25/$1.50 per 1M  

Live session note from `/workspace/daily-loop-live`. Copyrighted episode transcripts were not committed.

## Spend

| Source | USD |
| --- | ---: |
| Groq Whisper (new: SSE 531 + Daily prediction markets) | $0.0416 |
| Gemini confirm (corpus + 4 real eps, cache-first) | $0.0095 |
| **Session total (billable)** | **$0.0511** |
| Litellm partial before crash (est., not in total) | ~$0.0020 |
| Budget / hard stop | $0.50 / $0.45 |

All API responses disk-cached under `cache/groq/` and `cache/gemini_confirm/`. `spend.log` appends after every call.
Reused prior transcripts from `/workspace/real-transcript-probe` (SSE 532 + Daily FBI) — no re-Whisper.

## Order executed

1. **Offline gates** via `run_daily_loop.py --offline` for H001→H004→H003→H002
2. **H001 live** on real transcripts (baseline CueDetector vs `TightPromoCueDetector`) + corpus window confirms
3. **H004** golden ingest process (templates + secret rejection)
4. **H003** offline pad sweep only (no live needed)
5. **H002 skipped** (failed ε offline)

## Hypotheses

### H001 — TightPromo `code <word>` FP tighten — **gate PASS, live confidence WIN**

| Path | Result |
| --- | --- |
| Offline corpus v1 | `measured` / `no_win` — macros identical to frozen recommended (F1=0.8889, tok↓=93.32%, scout_tok=3901). Synthetic fixtures lack Soft Skills tech-speech FPs. |
| Live real (4 eps) | Baseline windows **26** → TightPromo **15** (−42.3%). Soft Skills drops promo FPs; The Daily window counts unchanged. |
| Fold | Experiment-only: keep `TightPromoCueDetector`. **Do not** edit production `cue_detector.promo_pattern` yet. Mark `accepted` after Soft Skills goldens land. |

| Episode | Baseline wins | Tight wins | Dropped | Notes |
| --- | ---: | ---: | ---: | --- |
| `soft_skills_532` | 4 | 2 | 2 | cached transcript |
| `the_daily_fbi_hack` | 6 | 6 | 0 | cached transcript |
| `soft_skills_531` | 12 | 3 | 11 | new Whisper |
| `the_daily_prediction_markets` | 4 | 4 | 0 | new Whisper |

### H002 — Cue-sparse recovery — **REJECTED (ε)** — skipped live

- Both storytelling + midroll-probe variants exceed `sum_scout_input_tokens` +10% (4338 / 4707 vs baseline 3901, limit 4291).
- Do not fold.

### H003 — Pad/threshold sweep — **no_win**

- Survivor #1 remains frozen recommended: `extras=True t=0.5 pad=15/3`.
- `t=0.4` ties; does not strictly beat on detection then cost.
- `extras=False` fails recall/hit/F1 ε.

### H004 — Golden ingest process — **process_ok**

- Templates staged: Soft Skills-style + news-briefing-style under `golden_staging/`.
- Secret rejection works (`api_key` / AIza… refused).
- `promoted_to_corpus: false` (no copyrighted episode text committed).

## Corpus live Gemini vs labels

| TP | FP | FN (on scout windows) | Precision |
| ---: | ---: | ---: | ---: |
| 9 | 1 | 0 | 90.00% |

Single FP: `false_positive_content` non-ad-overlap window still marked promotional_external by Gemini.

## Baseline (frozen) — unchanged

| Metric | Value |
| --- | ---: |
| `scout_confirm_mean_time_f1` | 0.8889 |
| `scout_confirm_mean_time_recall` | 0.8889 |
| `scout_confirm_mean_time_precision` | 1.0000 |
| `scout_mean_ad_hit_rate` | 0.8889 |
| `mean_token_reduction_pct` | 93.32 |
| `sum_scout_input_tokens` | 3901.00 |
| `scout_confirm_mean_residual_strong_cue_rate` | 0.0039 |

## Folded onto this branch

Raw patches `0001-gemini-confirm-list-wrap` and `0002-daily-loop-offline-baseline` no longer apply on tip (hunk context moved). Equivalent behavior is already on the branch:

1. **`gemini_confirm._result_from_json`** — unwraps JSON list-wrap (`[{...}]`) and non-dict payloads.
2. **`daily_loop.run_daily_loop`** — `live_confirm_flags_cleared()` pops `PODLY_*_CONFIRM_LIVE` around oracle baseline (and other offline evals) so `--live` cannot spend on oracle or poison the confirm cache.
3. **H001 ledger** — `measured` with live confidence win (26→15 windows). Still experiment-package only; not `accepted` until Soft Skills-style goldens land.

Do **not** open a PR. Do **not** set `enable_bow_scout_gemini_confirm`. Do **not** loosen `gates.json`.

## Resume tomorrow

- Promote Soft Skills-style labeled goldens (from staged templates + human labels on 531/532 windows) via --write-corpus --update-baseline so H001 can show offline confidence win.
- Revisit H002 only with a recovery that stays within +10% scout tokens (current storytelling/midroll-probe fail ε).
- Optional: more The Daily episodes for sponsor/transition FP rates; reuse caches.
- Do not enable enable_bow_scout_gemini_confirm.
