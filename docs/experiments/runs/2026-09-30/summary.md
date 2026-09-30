# Daily experiment run 2026-09-30

Generated at `2026-09-30T23:06:19+00:00`. Mode: **live** (Whisper + Gemini confirm; cache-first). Session spend **$0.0511**.

Full live-session writeup: [`RESULTS.md`](RESULTS.md).

- Daily budget: `$0.50` (hard stop `$0.45`)
- Production flag `enable_bow_scout_gemini_confirm`: `false`
- Feed default strategy: `llm`
- Baseline gates: **passed** (frozen corpus v1 macros unchanged)
- `gates.json`: **not loosened**

## Spend

| Source | USD |
| --- | ---: |
| Groq Whisper (new: Soft Skills 531 + The Daily prediction markets) | $0.0416 |
| Gemini confirm (corpus + 4 real eps, cache-first) | $0.0095 |
| **Session total (billable)** | **$0.0511** |

Models: Whisper `whisper-large-v3-turbo` @ $0.04/hr; Confirm `gemini-3.1-flash-lite` @ $0.25/$1.50 per 1M. Reused prior transcripts for Soft Skills 532 + The Daily FBI (no re-Whisper).

## Hypotheses this run

| ID | Status | Verdict | Fold-eligible | Notes |
| --- | --- | --- | --- | --- |
| `H001` | measured | no_win (offline) / **live confidence win** | false | Corpus macros identical (F1=0.8889). Live real eps: **26 → 15** scout windows (−42.3%). TightPromo stays experiment-only. |
| `H002` | rejected | failed_gates | false | Storytelling 4338 / midroll-probe 4707 scout tokens vs 3901 (limit 4291). Live skipped. |
| `H003` | measured | no_win | false | Frozen recommended `extras=True t=0.5 pad=15/3` remains the survivor. |
| `H004` | measured | process_ok | false | Templates + secret rejection OK. `promoted_to_corpus: false`. |

## H001 live windows (4 real episodes)

| Episode | Baseline | TightPromo | Dropped |
| --- | ---: | ---: | ---: |
| soft_skills_532 | 4 | 2 | 2 |
| the_daily_fbi_hack | 6 | 6 | 0 |
| soft_skills_531 | 12 | 3 | 11 |
| the_daily_prediction_markets | 4 | 4 | 0 |
| **total** | **26** | **15** | **13** |

Soft Skills drops bare `code <word>` promo FPs. The Daily window counts unchanged (no ad-recall regression on scout). Production `CueDetector.promo_pattern` and `enable_bow_scout_gemini_confirm` stay unchanged.

## Corpus live Gemini vs labels

TP 9 / FP 1 / FN 0 on scout windows (precision 90%). Single FP: `false_positive_content` non-ad-overlap window still marked promotional_external.

## Frozen baseline (unchanged)

| Metric | Value |
| --- | ---: |
| `scout_confirm_mean_time_f1` | 0.8889 |
| `scout_confirm_mean_time_recall` | 0.8889 |
| `scout_confirm_mean_time_precision` | 1.0000 |
| `scout_mean_ad_hit_rate` | 0.8889 |
| `mean_token_reduction_pct` | 93.32 |
| `sum_scout_input_tokens` | 3901.00 |
| `scout_confirm_mean_residual_strong_cue_rate` | 0.0039 |

## Next open (ranked)

none

accepted = fold-eligible on the experiment package only. Do not set enable_bow_scout_gemini_confirm. Do not loosen gates.json. Snapshot updates require --update-baseline plus RESULTS notes.
