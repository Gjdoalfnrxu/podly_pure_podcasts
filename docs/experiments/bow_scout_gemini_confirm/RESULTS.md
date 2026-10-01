# Bag-of-words scout + Gemini confirm: offline results

Generated at `2026-10-01T16:00:38.507366+00:00` (UTC). No live Gemini calls were made; confirm is an **oracle mock** that keeps labeled ad overlap inside scout windows.

## 2026-10-01 corpus promotion (H005)

Promoted `soft_skills_style_interview` (synthetic Soft Skills-*style* interview from H004 templates; **not** copyrighted episode text) into corpus v1 after TightPromo vs recommended dropped scout windows **2 → 1** on unlabeled `code review` / `code path` / `code sample`, with labeled `use code SOFT20` recall **1.0**. Frozen `gates.json` ε **not** loosened. Production `CueDetector.promo_pattern` and `enable_bow_scout_gemini_confirm` stay unchanged.

Recommended-config macros after promotion (n=10): confirm F1/recall/hit **0.9000**, precision **1.0**, residual **0.0056**, scout tokens **4606**, token↓ **92.80%**. Cue-sparse storytelling remains the only labeled-ad miss.

## Hypotheses

1. `CueDetector.analyze` / `highlight_cues` can localize most true ads enough that Gemini only needs those windows + small context, not the full 60-segment AdClassifier walk.
2. Token/cost of scout+confirm is much smaller than the full AdClassifier walk at comparable recall on available fixtures.
3. Caching by content hash makes repeat episodes nearly free.

## Recommended scout config (offline)

- threshold: `0.5` (flag if CueDetector score ≥ this)
- pad_seconds: `15.0`
- pad_segments: `3`
- include_scout_extras: `True` (optional `sponsored by` / `brought to you by` / `ad break` patterns; off in production `CueDetector()`)
- include_self_promo: `false` (matches AdClassifier demotion)
- production flag `enable_bow_scout_gemini_confirm`: `False` (Feed/PodcastProcessor stay on the LLM AdClassifier path)

## Production-like vs scout±confirm (before / after)

Production-like is an **offline oracle** of the current AdClassifier walk: labeled ads (perfect LLM) plus CueDetector neighbor expansion (extras off, window=5). Scout±confirm is CueDetector windows plus oracle Gemini confirm. Live Gemini is not used in default CI.

Frozen corpus `v1` (10 fixtures). Agent contract: `docs/experiments/AGENT_EVAL.md`.

| Path | Time recall | Time precision | Time F1 | Ad-block hit | FN rate | Residual cue rate | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Production-like (oracle LLM + neighbor expand) | 100.0% | 59.0% | 73.2% | 100.0% | 0.0% | 0.0056 | 71675 |
| Scout windows (pre-confirm) | 90.0% | 47.2% | 49.1% | 90.0% | 10.0% | — | — |
| Scout + oracle confirm | 90.0% | 100.0% | 90.0% | 90.0% | 10.0% | 0.0056 | 4606 |

## Headline metrics (recommended config)

| Metric | Value |
| --- | ---: |
| Scout ad-block hit rate | 90.0% |
| Scout labeled-ad time coverage | 90.0% |
| Scout window precision (pre-confirm) | 47.2% |
| Scout+confirm time F1 | 90.0% |
| False-negative risk (missed ad blocks) | 10.0% |
| Residual strong-cue rate (after confirm cuts) | 0.0056 |
| Mean token reduction vs full AdClassifier | 92.8% |
| Mean USD reduction (est., similar $/M) | 82.7% |
| Full-walk input tokens (sum) | 71675 |
| Scout+confirm input tokens (sum) | 4606 |
| Repeat-episode scout input tokens (cached) | 0 |

Hit rate is the fraction of **labeled ad blocks** that overlap a scout window. Coverage is the fraction of **labeled ad seconds** inside those windows. Precision is labeled-ad seconds / predicted seconds. F1 is the harmonic mean of time precision and recall after spans are merged.

## Per-fixture (recommended config)

| Fixture | Segs | Ad blocks | Hit | Coverage | Precision | Windows | Full tok | Scout tok | Reduction | Missed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| classic_host_reads | 540 | 3 | 100.0% | 100.0% | 48.6% | 3 | 20503 | 1172 | 94.3% | — |
| cue_sparse_storytelling | 240 | 1 | 0.0% | 0.0% | 100.0% | 0 | 8849 | 0 | 100.0% | midroll (Away brand read, no CueDetector tokens) |
| false_positive_content | 144 | 1 | 100.0% | 100.0% | 10.0% | 2 | 5964 | 742 | 87.6% | — |
| self_promo_vs_sponsor | 180 | 1 | 100.0% | 100.0% | 33.3% | 1 | 6505 | 400 | 93.9% | — |
| short_preroll_only | 96 | 1 | 100.0% | 100.0% | 40.0% | 1 | 3812 | 284 | 92.5% | — |
| wildcard_midroll_example | 72 | 1 | 100.0% | 100.0% | 40.0% | 1 | 3488 | 471 | 86.5% | — |
| ad_free_interview | 120 | 0 | 100.0% | 100.0% | 100.0% | 0 | 4177 | 0 | 100.0% | — |
| stacked_midrolls | 216 | 2 | 100.0% | 100.0% | 43.8% | 1 | 8438 | 455 | 94.6% | — |
| chapter_style_ad_break | 108 | 1 | 100.0% | 100.0% | 45.5% | 1 | 3974 | 377 | 90.5% | — |
| soft_skills_style_interview | 144 | 1 | 100.0% | 100.0% | 11.1% | 2 | 5965 | 705 | 88.2% | — |

### Fixture notes

- `classic_host_reads`: Production CueDetector hits URL/CTA/promo/phone plus mid-roll transition. Opening 'brought to you by' needs scout extras.
- `cue_sparse_storytelling`: Expected scout miss even with extras: no sponsor/CTA/URL/phone.
- `false_positive_content`: Shopify.com, check out, visit, my newsletter, sign up, deal.
- `self_promo_vs_sponsor`: Self-promo should not be flagged at default scout config.
- `short_preroll_only`: Small episode to show full-walk overhead vs one Gemini window.
- `wildcard_midroll_example`: Transition 'after the break' is the only production cue; body has none.
- `ad_free_interview`: Content lines avoid CueDetector core + scout-extra patterns.
- `stacked_midrolls`: Two labeled ads 5s apart; scout padding/merge should cover both.
- `chapter_style_ad_break`: Needs scout extras (ad_break + sponsor). Production CueDetector has none of URL/CTA/promo/phone/transition in the ad body.
- `soft_skills_style_interview`: Soft Skills-style synthetic interview from H004 templates. Labeled preroll uses `use code SOFT20`. Unlabeled mid-episode `code review` / `code path` / `code sample` are tech-speech promo FPs for TightPromo vs production CueDetector. Not a real show; not copyrighted episode text.

## False-negative risk

Production `CueDetector` (no extras) is a **neighbor-expansion** helper after the LLM already found ads. It does **not** match `sponsor`, `brought to you by`, `advertisement`, or `ad break` — those live in chapter-filter strings, not the regexes. Scout extras add a subset of those phrases without changing default `CueDetector()` behavior.

Ads the scout still misses at the recommended config:

- `cue_sparse_storytelling` midroll 480–515s: Away brand read, no CueDetector tokens

Cue-sparse host-reads (brand story, no URL/CTA/phone/sponsor phrase) are the main residual risk. Padding cannot recover an ad the scout never flags. A live Gemini full-walk would still catch these; a scout-first path will not unless extras grow or a cheap fallback full pass is kept.

## Threshold / padding sweep

| extras | thresh | pad_s | pad_seg | mean hit | mean cov | mean prec | mean tok↓ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| False | 0.5 | 15.0 | 3 | 80.0% | 80.0% | 52.7% | 93.8% |
| False | 0.8 | 15.0 | 3 | 70.0% | 70.0% | 60.6% | 95.2% |
| False | 1.0 | 15.0 | 3 | 70.0% | 70.0% | 60.9% | 95.3% |
| True | 0.5 | 15.0 | 3 | 90.0% | 90.0% | 47.2% | 92.8% |
| True | 0.8 | 0.0 | 0 | 80.0% | 76.0% | 82.2% | 95.5% |
| True | 0.8 | 15.0 | 3 | 80.0% | 80.0% | 55.8% | 94.3% |
| True | 0.8 | 30.0 | 5 | 80.0% | 80.0% | 46.8% | 93.2% |
| True | 1.5 | 15.0 | 3 | 66.7% | 65.4% | 69.6% | 97.0% |

Raising padding increases coverage of ads whose cues sit in the middle of the block (Gemini then sees the intro). It also lowers precision and spends more confirm tokens. Threshold 0.5 with extras and ±15s/±3 segments is the best recall/token trade-off on this set: it includes transition bumpers (`after the break`) used by the prompt.py Wildcard example (score 0.5) while self-promo-only lines (weight 0.4) stay below the cut. Threshold 0.8 drops those transition-only ads. Cue-sparse brand reads still miss at every threshold.

## Caching

Confirm prompts are keyed by `sha256(model + messages)`. A second pass over the same scout windows is **0 input tokens** in the cost model (`cached_repeat_input_tokens` above). Production `ModelCall` upserts by `(post_id, model_name, first_seq, last_seq)` and does **not** hash prompt text; a follow-up PR should add content-hash caching beside or instead of that key if scout windows are reused across posts with identical audio.

## Removal verifier (fade formula)

When `clip_segments_with_fade`'s complex filter succeeds:

`output_ms ≈ source_ms − Σ ad_ms + 2 × fade_ms × n_cuts`

Feed default `fade_ms` is `3000`. Simple-concat fallback adds no fades. ffmpeg mux jitter of ~56ms is documented in `src/tests/test_process_audio.py` and is **not** included in the formula.

## What a live Gemini run ($0.50/day, cache-first) should measure

- Run corpus v1 plus 5–10 real Whisper transcripts with human or current-AdClassifier labels; budget Gemini 2.5 Flash at ≤ $0.50/day and enable the content-hash cache directory first.
- Measure live confirm precision/recall vs oracle: does Gemini drop false-positive windows (Shopify.com technical mentions, 'check out this paper') and tighten boundaries inside padded windows?
- Measure live false negatives on cue-sparse host-reads; decide whether a periodic full AdClassifier pass, extra scout phrases, or chapter metadata should cover that tail.
- Compare boundary error (start/end vs labels) against production BoundaryRefiner / WordBoundaryRefiner — Gemini confirm is intended to replace some of that second LLM pass, not add a third.
- Record actual litellm usage tokens vs the chars/4 estimate; adjust cost_model prices to the Gemini SKU you actually call.
- Do not set enable_bow_scout_gemini_confirm or change Feed defaults until live recall on real episodes is within the AGENT_EVAL band of the full AdClassifier walk.

## Ready to PR (later — this branch does not open one)

- [x] Frozen golden corpus under `src/podcast_processor/experiments/corpus/v1/` with MANIFEST hashes.
- [x] Committed baseline snapshot + gates under `docs/experiments/bow_scout_gemini_confirm/baseline/v1/`.
- [x] Dual-path harness: production-like AdClassifier mock vs scout±confirm (time P/R/F1, tokens, residual cues, duration stubs).
- [x] Pytest/CI gates fail on recall drop, FN/residual rise, or token blow-up vs snapshot (see docs/experiments/AGENT_EVAL.md).
- [x] Experimental package isolated under `src/podcast_processor/experiments/` (PodcastProcessor does not swap classifiers).
- [x] CueDetector default constructor / `analyze` keys unchanged for production.
- [x] Gemini client mocks by default; live path requires GEMINI_API_KEY + PODLY_GEMINI_CONFIRM_LIVE=true.
- [x] `enable_bow_scout_gemini_confirm` defaults False; Feed strategy stays `llm`.
- [ ] Human review of scout extras (`brought to you by`, etc.) before enabling them in production neighbor expansion.
- [ ] Live Gemini confirm on real transcripts ($0.50/day, cache-first).
- [ ] Decision on cue-sparse fallback before wiring into PodcastProcessor.
- [ ] Alembic not required (no model changes).
- [x] Daily hypothesis→experiment→gate loop: `scripts/experiments/run_daily_loop.py` (offline by default).

## Files

- Agent contract: `docs/experiments/AGENT_EVAL.md`
- Frozen corpus: `src/podcast_processor/experiments/corpus/v1/`
- Frozen snapshot/gates: `docs/experiments/bow_scout_gemini_confirm/baseline/v1/`
- Experiment package: `src/podcast_processor/experiments/`
- CueDetector extras/score: `src/podcast_processor/cue_detector.py` (default constructor unchanged)
- Harness: `scripts/experiments/run_bow_scout_eval.py`
- Metrics dump: `metrics.json` (this directory)

Production `Feed.ad_detection_strategy` stays `llm` and `enable_bow_scout_gemini_confirm` defaults **false**. PodcastProcessor does not swap in the experimental classifier.

