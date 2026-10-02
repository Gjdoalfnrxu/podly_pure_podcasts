# Bag-of-words scout + Gemini confirm: offline results

Generated at `2026-10-02T15:57:58.290732+00:00` (UTC). No live Gemini calls were made; confirm is an **oracle mock** that keeps labeled ad overlap inside scout windows.

## 2026-10-02 folds

### H010 — TightPromo as eval recommended detector — **measured / no_win**

Proper offline daily loop (not `--check` alone). Gates green: confirm F1/recall/hit **0.9752 / 0.9610 / 1.0** (tied with frozen recommended). TightPromo scout tokens **4652 vs 5337**, but residual **0.0066** and confirm precision **1.0** are unchanged, so the confidence primary did not win. **Not accepted.** TightPromo stays experiment-only. Production `CueDetector.promo_pattern` and `enable_bow_scout_gemini_confirm` stay off.

### H009 — 70s duration-gated midroll probe — **folded**

Widen H007 `half_window` 20→35. Confirm F1/recall/hit **1.0** (was 0.9752 / 0.9610 / 1.0). Cue-sparse time coverage **57.1% → 100%**. Scout tokens **5337 → 5429** (limit ~5871). Folded into eval `DEFAULT_WINDOW_POSTPROCESS`; snapshot updated; `gates.json` ε **not** loosened. Production CueDetector extras stay off.

Recommended-config macros after H009 (n=11 + 70s probe + pad=12/2): confirm F1 **1.0**, recall **1.0**, hit **1.0**, precision **1.0**, residual **0.0066**, scout tokens **5429**, token↓ **91.95%**.

## 2026-10-01 folds

### H005 (morning) — corpus

Promoted `soft_skills_style_interview` (synthetic; **not** copyrighted episode text) into corpus v1. TightPromo 2→1 windows on unlabeled `code <word>` with `use code SOFT20` recall 1.0.

### H006 (afternoon) — corpus

Promoted `news_briefing_style_code_cta` (synthetic; **not** The Daily / NYT text) into corpus v1. TightPromo 2→1 windows on unlabeled `code review` / `code path` with `use code SAVE50` recall 1.0. Live (tip `ade73440e24f18bba300eb5a860910cb494f0936`): Gemini classified the dropped window as `technical_discussion`; real eps 26→15 (−42.3%). TightPromo stays experiment-only.

### H007 (afternoon) — experiment-package probe

Folded `duration_gated_midroll_probe` into eval `DEFAULT_WINDOW_POSTPROCESS`. Cue-sparse ad-block hit 0→1; skips 10-minute `ad_free_interview`. Confirm F1 0.9091→0.9752, hit 0.9091→1.0, scout tokens 5352→5698 (then within ε). Production CueDetector extras stay off.

### H008 (afternoon) — experiment-package pad

Folded recommended pad to **12s / 2 segments** (still extras on, t=0.5). Ties confirm F1/recall/hit vs 15/3 after H007's probe and cuts scout tokens **5698 → 5337** (token↓ 91.48% → 92.05%). Frozen `gates.json` ε **not** loosened. Production neighbor window unchanged.

## Hypotheses

1. `CueDetector.analyze` / `highlight_cues` can localize most true ads enough that Gemini only needs those windows + small context, not the full 60-segment AdClassifier walk.
2. Token/cost of scout+confirm is much smaller than the full AdClassifier walk at comparable recall on available fixtures.
3. Caching by content hash makes repeat episodes nearly free.

## Recommended scout config (offline)

- threshold: `0.5` (flag if CueDetector score ≥ this)
- pad_seconds: `12.0`
- pad_segments: `2`
- include_scout_extras: `True` (optional `sponsored by` / `brought to you by` / `ad break` patterns; off in production `CueDetector()`)
- include_self_promo: `false` (matches AdClassifier demotion)
- window_postprocess: `wider_duration_gated_midroll_probe` (H009; 70s midroll window on empty-scout episodes ≥900s)
- production flag `enable_bow_scout_gemini_confirm`: `False` (Feed/PodcastProcessor stay on the LLM AdClassifier path)

## Production-like vs scout±confirm (before / after)

Production-like is an **offline oracle** of the current AdClassifier walk: labeled ads (perfect LLM) plus CueDetector neighbor expansion (extras off, window=5). Scout±confirm is CueDetector windows plus oracle Gemini confirm. Live Gemini is not used in default CI.

Frozen corpus `v1` (11 fixtures). Agent contract: `docs/experiments/AGENT_EVAL.md`.

| Path | Time recall | Time precision | Time F1 | Ad-block hit | FN rate | Residual cue rate | Input tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Production-like (oracle LLM + neighbor expand) | 100.0% | 57.6% | 72.0% | 100.0% | 0.0% | 0.0066 | 75866 |
| Scout windows (pre-confirm) | 100.0% | 44.6% | 58.1% | 100.0% | 0.0% | — | — |
| Scout + oracle confirm | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0066 | 5429 |

## Headline metrics (recommended config)

| Metric | Value |
| --- | ---: |
| Scout ad-block hit rate | 100.0% |
| Scout labeled-ad time coverage | 100.0% |
| Scout window precision (pre-confirm) | 44.6% |
| Scout+confirm time F1 | 100.0% |
| False-negative risk (missed ad blocks) | 0.0% |
| Residual strong-cue rate (after confirm cuts) | 0.0066 |
| Mean token reduction vs full AdClassifier | 92.0% |
| Mean USD reduction (est., similar $/M) | 79.6% |
| Full-walk input tokens (sum) | 75866 |
| Scout+confirm input tokens (sum) | 5429 |
| Repeat-episode scout input tokens (cached) | 0 |

Hit rate is the fraction of **labeled ad blocks** that overlap a scout window. Coverage is the fraction of **labeled ad seconds** inside those windows. Precision is labeled-ad seconds / predicted seconds. F1 is the harmonic mean of time precision and recall after spans are merged.

## Per-fixture (recommended config)

| Fixture | Segs | Ad blocks | Hit | Coverage | Precision | Windows | Full tok | Scout tok | Reduction | Missed |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| classic_host_reads | 540 | 3 | 100.0% | 100.0% | 56.7% | 3 | 20503 | 1098 | 94.6% | — |
| cue_sparse_storytelling | 240 | 1 | 100.0% | 100.0% | 50.0% | 1 | 8849 | 438 | 95.1% | — |
| false_positive_content | 144 | 1 | 100.0% | 100.0% | 11.8% | 2 | 5964 | 699 | 88.3% | — |
| self_promo_vs_sponsor | 180 | 1 | 100.0% | 100.0% | 40.0% | 1 | 6505 | 371 | 94.3% | — |
| short_preroll_only | 96 | 1 | 100.0% | 100.0% | 50.0% | 1 | 3812 | 269 | 92.9% | — |
| wildcard_midroll_example | 72 | 1 | 100.0% | 100.0% | 46.2% | 1 | 3488 | 440 | 87.4% | — |
| ad_free_interview | 120 | 0 | 100.0% | 100.0% | 100.0% | 0 | 4177 | 0 | 100.0% | — |
| stacked_midrolls | 216 | 2 | 100.0% | 100.0% | 50.0% | 1 | 8438 | 423 | 95.0% | — |
| chapter_style_ad_break | 108 | 1 | 100.0% | 100.0% | 55.6% | 1 | 3974 | 347 | 91.3% | — |
| soft_skills_style_interview | 144 | 1 | 100.0% | 100.0% | 13.3% | 2 | 5965 | 659 | 89.0% | — |
| news_briefing_style_code_cta | 120 | 1 | 100.0% | 100.0% | 17.6% | 2 | 4191 | 685 | 83.7% | — |

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
- `news_briefing_style_code_cta`: News-briefing-style synthetic from H004 templates. Unlabeled `code review` / `code path` sit beside a labeled `use code SAVE50` midroll so TightPromo can be scored offline. Not NYT text; not a real show.

## False-negative risk

Production `CueDetector` (no extras) is a **neighbor-expansion** helper after the LLM already found ads. It does **not** match `sponsor`, `brought to you by`, `advertisement`, or `ad break` — those live in chapter-filter strings, not the regexes. Scout extras add a subset of those phrases without changing default `CueDetector()` behavior.

Ads the scout still misses at the recommended config:

- None on this fixture set.

Cue-sparse host-reads (brand story, no URL/CTA/phone/sponsor phrase) are a **block hit** with full time coverage after H009's 70s duration-gated probe (H007's 40s window only covered 57.1%). Production `CueDetector()` extras stay off. Do not loosen ε.

## Threshold / padding sweep

| extras | thresh | pad_s | pad_seg | mean hit | mean cov | mean prec | mean tok↓ |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| False | 0.5 | 15.0 | 3 | 90.9% | 90.9% | 44.7% | 92.3% |
| False | 0.8 | 15.0 | 3 | 81.8% | 81.8% | 51.9% | 93.6% |
| False | 1.0 | 15.0 | 3 | 81.8% | 81.8% | 52.2% | 93.7% |
| True | 0.5 | 15.0 | 3 | 100.0% | 100.0% | 39.7% | 91.4% |
| True | 0.8 | 0.0 | 0 | 90.9% | 87.3% | 73.2% | 94.2% |
| True | 0.8 | 15.0 | 3 | 90.9% | 90.9% | 47.5% | 92.8% |
| True | 0.8 | 30.0 | 5 | 90.9% | 90.9% | 38.8% | 91.3% |
| True | 1.5 | 15.0 | 3 | 78.8% | 77.6% | 61.7% | 96.0% |

Raising padding increases coverage of ads whose cues sit in the middle of the block (Gemini then sees the intro). It also lowers precision and spends more confirm tokens. After H008, threshold 0.5 with extras and ±12s/±2 segments ties confirm F1/recall/hit vs ±15s/±3 on corpus v1 and spends fewer scout tokens. It still includes transition bumpers (`after the break`) used by the prompt.py Wildcard example (score 0.5) while self-promo-only lines (weight 0.4) stay below the cut. Threshold 0.8 drops those transition-only ads. H007/H009's duration-gated probe recovers cue-sparse host-reads as a block hit (H009: full time coverage) on every sweep config that would otherwise return empty windows on long episodes; short ad-free interviews stay unprobed.

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

