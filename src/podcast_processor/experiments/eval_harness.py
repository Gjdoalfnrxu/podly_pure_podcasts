"""Offline eval: scout recall/precision, token reduction, cache, residual cues."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from podcast_processor.experiments.bow_scout import (
    BowScout,
    ScoutConfig,
    overlap_seconds,
)
from podcast_processor.experiments.cost_model import (
    DEFAULT_CLASSIFIER_PRICES,
    DEFAULT_GEMINI_PRICES,
    estimate_adclassifier_tokens,
    estimate_scout_confirm_tokens,
    token_reduction_pct,
)
from podcast_processor.experiments.fixtures import all_fixtures, write_fixture_json
from podcast_processor.experiments.gemini_confirm import GeminiConfirmClient
from podcast_processor.experiments.removal_verifier import (
    ads_to_ms,
    expected_output_duration_ms,
    residual_cue_scan,
)
from podcast_processor.experiments.types import (
    ConfirmResult,
    EpisodeFixture,
    LabeledAd,
    ScoutWindow,
)
from shared import defaults as DEFAULTS

DEFAULT_SWEEP: list[ScoutConfig] = [
    ScoutConfig(
        threshold=0.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=1.0,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=False,
    ),
    ScoutConfig(
        threshold=0.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=0.0,
        pad_segments=0,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=0.8,
        pad_seconds=30.0,
        pad_segments=5,
        include_scout_extras=True,
    ),
    ScoutConfig(
        threshold=1.5,
        pad_seconds=15.0,
        pad_segments=3,
        include_scout_extras=True,
    ),
]

RECOMMENDED_CONFIG = ScoutConfig(
    threshold=0.5,
    pad_seconds=15.0,
    pad_segments=3,
    include_scout_extras=True,
)


def labeled_ad_duration(ads: list[LabeledAd]) -> float:
    return float(sum(ad.duration() for ad in ads))


def window_duration(windows: list[ScoutWindow]) -> float:
    return float(sum(window.duration() for window in windows))


def ad_hit_rate(ads: list[LabeledAd], windows: list[ScoutWindow]) -> float:
    if not ads:
        return 1.0
    hits = 0
    for ad in ads:
        if any(
            overlap_seconds(ad.start, ad.end, window.start_time, window.end_time) > 0.5
            for window in windows
        ):
            hits += 1
    return float(hits) / float(len(ads))


def ad_coverage(ads: list[LabeledAd], windows: list[ScoutWindow]) -> float:
    total = labeled_ad_duration(ads)
    if total <= 0:
        return 1.0
    covered = 0.0
    for ad in ads:
        covered += sum(
            overlap_seconds(ad.start, ad.end, window.start_time, window.end_time)
            for window in windows
        )
    return min(1.0, covered / total)


def scout_precision(ads: list[LabeledAd], windows: list[ScoutWindow]) -> float:
    total = window_duration(windows)
    if total <= 0:
        return 1.0
    overlapping = 0.0
    for window in windows:
        overlapping += sum(
            overlap_seconds(window.start_time, window.end_time, ad.start, ad.end)
            for ad in ads
        )
    return min(1.0, overlapping / total)


def missed_ads(
    ads: list[LabeledAd], windows: list[ScoutWindow]
) -> list[dict[str, Any]]:
    missed: list[dict[str, Any]] = []
    for ad in ads:
        if not any(
            overlap_seconds(ad.start, ad.end, window.start_time, window.end_time) > 0.5
            for window in windows
        ):
            missed.append(
                {
                    "start": ad.start,
                    "end": ad.end,
                    "kind": ad.kind,
                    "notes": ad.notes,
                }
            )
    return missed


def evaluate_episode(
    episode: EpisodeFixture,
    config: ScoutConfig,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    scout = BowScout(config=config)
    windows = scout.scout(episode.segments)
    client = GeminiConfirmClient(
        mock_mode="oracle",
        cache_dir=cache_dir,
        labeled_ads=episode.labeled_ads,
    )
    confirms = client.confirm_windows(
        windows, episode.podcast_title, episode.podcast_topic
    )
    confirmed_spans = _confirmed_spans(confirms)

    full_tokens = estimate_adclassifier_tokens(
        episode.segments, episode.podcast_title, episode.podcast_topic
    )
    scout_tokens = estimate_scout_confirm_tokens(
        windows, episode.podcast_title, episode.podcast_topic
    )
    repeat_tokens = estimate_scout_confirm_tokens(
        windows,
        episode.podcast_title,
        episode.podcast_topic,
        cached_hashes=_hashes_from_confirms(confirms),
    )

    oracle_hit = ad_hit_rate(episode.labeled_ads, _spans_as_windows(confirmed_spans))
    residuals = residual_cue_scan(
        episode.segments,
        [
            LabeledAd(span["start"], span["end"], "confirmed")
            for span in confirmed_spans
        ],
    )
    duration = expected_output_duration_ms(
        source_ms=int(episode.duration_seconds * 1000),
        ad_segments_ms=ads_to_ms(episode.labeled_ads),
        fade_ms=DEFAULTS.OUTPUT_FADE_MS,
        complex_filter=True,
    )

    return {
        "fixture_id": episode.fixture_id,
        "title": episode.title,
        "n_segments": len(episode.segments),
        "duration_seconds": episode.duration_seconds,
        "n_labeled_ads": len(episode.labeled_ads),
        "labeled_ad_seconds": labeled_ad_duration(episode.labeled_ads),
        "n_windows": len(windows),
        "window_seconds": window_duration(windows),
        "ad_hit_rate": ad_hit_rate(episode.labeled_ads, windows),
        "ad_coverage": ad_coverage(episode.labeled_ads, windows),
        "scout_precision": scout_precision(episode.labeled_ads, windows),
        "false_negative_risk": 1.0 - ad_hit_rate(episode.labeled_ads, windows),
        "missed_ads": missed_ads(episode.labeled_ads, windows),
        "oracle_confirm_hit_rate": oracle_hit,
        "residual_strong_cues": len(residuals),
        "full_classifier": {
            "calls": full_tokens.calls,
            "input_tokens": full_tokens.input_tokens,
            "output_tokens": full_tokens.output_tokens,
            "usd": full_tokens.usd,
            "model": DEFAULT_CLASSIFIER_PRICES.name,
        },
        "scout_confirm": {
            "calls": scout_tokens.calls,
            "input_tokens": scout_tokens.input_tokens,
            "output_tokens": scout_tokens.output_tokens,
            "usd": scout_tokens.usd,
            "model": DEFAULT_GEMINI_PRICES.name,
            "cached_repeat_input_tokens": repeat_tokens.input_tokens,
            "cached_repeat_usd": repeat_tokens.usd,
        },
        "token_reduction_pct": token_reduction_pct(full_tokens, scout_tokens),
        "usd_reduction_pct": (
            100.0 * (1.0 - scout_tokens.usd / full_tokens.usd)
            if full_tokens.usd
            else 0.0
        ),
        "removal": {
            "source_ms": duration.source_ms,
            "removed_ms": duration.removed_ms,
            "fade_added_ms": duration.fade_added_ms,
            "expected_output_ms": duration.expected_output_ms,
            "n_cuts": duration.n_cuts,
        },
        "config": _config_dict(config),
        "notes": episode.notes,
    }


def evaluate_all(
    config: ScoutConfig | None = None,
    sweep: list[ScoutConfig] | None = None,
    cache_dir: Path | None = None,
) -> dict[str, Any]:
    recommended = config or RECOMMENDED_CONFIG
    configs = sweep or DEFAULT_SWEEP
    episodes = all_fixtures()
    recommended_rows = [
        evaluate_episode(episode, recommended, cache_dir=cache_dir)
        for episode in episodes
    ]
    sweep_rows: list[dict[str, Any]] = []
    for cfg in configs:
        rows = [
            evaluate_episode(episode, cfg, cache_dir=cache_dir) for episode in episodes
        ]
        sweep_rows.append(
            {
                "config": _config_dict(cfg),
                "macro": _macro_summary(rows),
                "per_fixture": [
                    {
                        "fixture_id": row["fixture_id"],
                        "ad_hit_rate": row["ad_hit_rate"],
                        "ad_coverage": row["ad_coverage"],
                        "scout_precision": row["scout_precision"],
                        "token_reduction_pct": row["token_reduction_pct"],
                        "n_windows": row["n_windows"],
                    }
                    for row in rows
                ],
            }
        )
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "live_gemini": False,
        "assumptions": {
            "token_rule": "len(text)//4, matching AdClassifier/TokenRateLimiter",
            "adclassifier_overlap": "no-ad walk: ceil(chunk/2) capped at 30",
            "chunk_size": DEFAULTS.PROCESSING_NUM_SEGMENTS_TO_INPUT_TO_PROMPT,
            "fade_ms": DEFAULTS.OUTPUT_FADE_MS,
            "gemini_mocked": True,
            "oracle_confirm": (
                "Gemini quality upper bound: confirm keeps labeled overlap in "
                "scout windows; misses are scout false negatives."
            ),
            "prices_usd_per_million": {
                "classifier_input": DEFAULT_CLASSIFIER_PRICES.input_usd_per_million,
                "classifier_output": DEFAULT_CLASSIFIER_PRICES.output_usd_per_million,
                "gemini_input": DEFAULT_GEMINI_PRICES.input_usd_per_million,
                "gemini_output": DEFAULT_GEMINI_PRICES.output_usd_per_million,
                "classifier_model": DEFAULT_CLASSIFIER_PRICES.name,
                "gemini_model": DEFAULT_GEMINI_PRICES.name,
                "note": "Estimates for relative comparison, not invoices.",
            },
        },
        "recommended_config": _config_dict(recommended),
        "recommended": {
            "macro": _macro_summary(recommended_rows),
            "per_fixture": recommended_rows,
        },
        "sweep": sweep_rows,
        "follow_up_live_gemini": _follow_up_notes(),
        "ready_to_pr_checklist": _ready_checklist(),
    }


def write_artifacts(
    results: dict[str, Any],
    output_dir: Path,
    write_fixture_json_files: bool = True,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    if write_fixture_json_files:
        fixture_dir = output_dir / "fixtures"
        for episode in all_fixtures():
            write_fixture_json(episode, fixture_dir / f"{episode.fixture_id}.json")
    metrics_path = output_dir / "metrics.json"
    results_path = output_dir / "RESULTS.md"
    metrics_path.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    results_path.write_text(render_results_markdown(results), encoding="utf-8")
    return results_path, metrics_path


def render_results_markdown(results: dict[str, Any]) -> str:
    rec = results["recommended"]
    macro = rec["macro"]
    cfg = results["recommended_config"]
    lines: list[str] = [
        "# Bag-of-words scout + Gemini confirm: offline results",
        "",
        f"Generated at `{results['generated_at']}` (UTC). "
        "No live Gemini calls were made; confirm is an **oracle mock** that "
        "keeps labeled ad overlap inside scout windows.",
        "",
        "## Hypotheses",
        "",
        "1. `CueDetector.analyze` / `highlight_cues` can localize most true ads "
        "enough that Gemini only needs those windows + small context, not the "
        "full 60-segment AdClassifier walk.",
        "2. Token/cost of scout+confirm is much smaller than the full "
        "AdClassifier walk at comparable recall on available fixtures.",
        "3. Caching by content hash makes repeat episodes nearly free.",
        "",
        "## Recommended scout config (offline)",
        "",
        f"- threshold: `{cfg['threshold']}` (flag if CueDetector score ≥ this)",
        f"- pad_seconds: `{cfg['pad_seconds']}`",
        f"- pad_segments: `{cfg['pad_segments']}`",
        f"- include_scout_extras: `{cfg['include_scout_extras']}` "
        "(optional `sponsored by` / `brought to you by` / `ad break` patterns; "
        "off in production `CueDetector()`)",
        "- include_self_promo: `false` (matches AdClassifier demotion)",
        "",
        "## Headline metrics (recommended config, macro over 6 fixtures)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Scout ad-block hit rate | {_pct(macro['mean_ad_hit_rate'])} |",
        f"| Scout labeled-ad time coverage | {_pct(macro['mean_ad_coverage'])} |",
        f"| Scout window precision (pre-confirm) | {_pct(macro['mean_scout_precision'])} |",
        f"| False-negative risk (missed ad blocks) | {_pct(macro['mean_false_negative_risk'])} |",
        f"| Mean token reduction vs full AdClassifier | {_pct(macro['mean_token_reduction_pct'] / 100.0)} |",
        f"| Mean USD reduction (est., similar $/M) | {_pct(macro['mean_usd_reduction_pct'] / 100.0)} |",
        f"| Full-walk input tokens (sum) | {macro['sum_full_input_tokens']} |",
        f"| Scout+confirm input tokens (sum) | {macro['sum_scout_input_tokens']} |",
        f"| Repeat-episode scout input tokens (cached) | {macro['sum_cached_repeat_input_tokens']} |",
        "",
        "Hit rate is the fraction of **labeled ad blocks** that overlap a scout "
        "window. Coverage is the fraction of **labeled ad seconds** inside those "
        "windows. Precision is labeled-ad seconds / scout-window seconds before "
        "Gemini trims false-positive padding and content traps.",
        "",
        "## Per-fixture (recommended config)",
        "",
        "| Fixture | Segs | Ad blocks | Hit | Coverage | Precision | Windows | Full tok | Scout tok | Reduction | Missed |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rec["per_fixture"]:
        missed = (
            "; ".join(f"{item['kind']} ({item['notes']})" for item in row["missed_ads"])
            or "—"
        )
        lines.append(
            "| {id} | {n} | {ads} | {hit} | {cov} | {prec} | {win} | {full} | {scout} | {red} | {miss} |".format(
                id=row["fixture_id"],
                n=row["n_segments"],
                ads=row["n_labeled_ads"],
                hit=_pct(row["ad_hit_rate"]),
                cov=_pct(row["ad_coverage"]),
                prec=_pct(row["scout_precision"]),
                win=row["n_windows"],
                full=row["full_classifier"]["input_tokens"],
                scout=row["scout_confirm"]["input_tokens"],
                red=f"{row['token_reduction_pct']:.1f}%",
                miss=missed,
            )
        )
    lines.extend(
        [
            "",
            "### Fixture notes",
            "",
        ]
    )
    for row in rec["per_fixture"]:
        lines.append(f"- `{row['fixture_id']}`: {row['notes']}")
    lines.extend(
        [
            "",
            "## False-negative risk",
            "",
            "Production `CueDetector` (no extras) is a **neighbor-expansion** helper "
            "after the LLM already found ads. It does **not** match `sponsor`, "
            "`brought to you by`, `advertisement`, or `ad break` — those live in "
            "chapter-filter strings, not the regexes. Scout extras add a subset of "
            "those phrases without changing default `CueDetector()` behavior.",
            "",
            "Ads the scout still misses at the recommended config:",
            "",
        ]
    )
    any_miss = False
    for row in rec["per_fixture"]:
        for item in row["missed_ads"]:
            any_miss = True
            lines.append(
                f"- `{row['fixture_id']}` {item['kind']} {item['start']:.0f}–{item['end']:.0f}s: {item['notes']}"
            )
    if not any_miss:
        lines.append("- None on this fixture set.")
    lines.extend(
        [
            "",
            "Cue-sparse host-reads (brand story, no URL/CTA/phone/sponsor phrase) "
            "are the main residual risk. Padding cannot recover an ad the scout "
            "never flags. A live Gemini full-walk would still catch these; a "
            "scout-first path will not unless extras grow or a cheap fallback "
            "full pass is kept.",
            "",
            "## Threshold / padding sweep",
            "",
            "| extras | thresh | pad_s | pad_seg | mean hit | mean cov | mean prec | mean tok↓ |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in results["sweep"]:
        c = item["config"]
        m = item["macro"]
        lines.append(
            "| {ex} | {th} | {ps} | {pg} | {hit} | {cov} | {prec} | {red} |".format(
                ex=c["include_scout_extras"],
                th=c["threshold"],
                ps=c["pad_seconds"],
                pg=c["pad_segments"],
                hit=_pct(m["mean_ad_hit_rate"]),
                cov=_pct(m["mean_ad_coverage"]),
                prec=_pct(m["mean_scout_precision"]),
                red=f"{m['mean_token_reduction_pct']:.1f}%",
            )
        )
    lines.extend(
        [
            "",
            "Raising padding increases coverage of ads whose cues sit in the "
            "middle of the block (Gemini then sees the intro). It also lowers "
            "precision and spends more confirm tokens. Threshold 0.5 with extras "
            "and ±15s/±3 segments is the best recall/token trade-off on this set: "
            "it includes transition bumpers (`after the break`) used by the "
            "prompt.py Wildcard example (score 0.5) while self-promo-only lines "
            "(weight 0.4) stay below the cut. Threshold 0.8 drops those "
            "transition-only ads. Cue-sparse brand reads still miss at every "
            "threshold.",
            "",
            "## Caching",
            "",
            "Confirm prompts are keyed by `sha256(model + messages)`. A second "
            "pass over the same scout windows is **0 input tokens** in the cost "
            "model (`cached_repeat_input_tokens` above). Production `ModelCall` "
            "upserts by `(post_id, model_name, first_seq, last_seq)` and does "
            "**not** hash prompt text; a follow-up PR should add content-hash "
            "caching beside or instead of that key if scout windows are reused "
            "across posts with identical audio.",
            "",
            "## Removal verifier (fade formula)",
            "",
            "When `clip_segments_with_fade`'s complex filter succeeds:",
            "",
            "`output_ms ≈ source_ms − Σ ad_ms + 2 × fade_ms × n_cuts`",
            "",
            f"Feed default `fade_ms` is `{DEFAULTS.OUTPUT_FADE_MS}`. "
            "Simple-concat fallback adds no fades. ffmpeg mux jitter of ~56ms "
            "is documented in `src/tests/test_process_audio.py` and is **not** "
            "included in the formula.",
            "",
            "## What a live Gemini run ($0.50/day, cache-first) should measure",
            "",
        ]
    )
    for note in results["follow_up_live_gemini"]:
        lines.append(f"- {note}")
    lines.extend(
        [
            "",
            "## Ready to PR (later — this branch does not open one)",
            "",
        ]
    )
    for item in results["ready_to_pr_checklist"]:
        lines.append(f"- {item}")
    lines.extend(
        [
            "",
            "## Files",
            "",
            "- Experiment package: `src/podcast_processor/experiments/`",
            "- CueDetector extras/score: `src/podcast_processor/cue_detector.py` "
            "(default constructor unchanged)",
            "- Harness: `scripts/experiments/run_bow_scout_eval.py`",
            "- Metrics dump: `metrics.json` (this directory)",
            "",
            "Production `Feed` defaults and `PodcastProcessor` are not wired to "
            "this path.",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _confirmed_spans(confirms: list[ConfirmResult]) -> list[dict[str, float]]:
    spans: list[dict[str, float]] = []
    for result in confirms:
        if not result.is_ad:
            continue
        for span in result.ad_spans:
            spans.append({"start": span.start, "end": span.end})
    return spans


def _spans_as_windows(spans: list[dict[str, float]]) -> list[ScoutWindow]:
    windows: list[ScoutWindow] = []
    for i, span in enumerate(spans):
        windows.append(
            ScoutWindow(
                start_time=span["start"],
                end_time=span["end"],
                start_seq=i,
                end_seq=i,
                segment_indices=[i],
                peak_score=1.0,
                cue_types=[],
            )
        )
    return windows


def _hashes_from_confirms(confirms: list[ConfirmResult]) -> set[str]:
    return {result.prompt_hash for result in confirms if result.prompt_hash}


def _config_dict(config: ScoutConfig) -> dict[str, Any]:
    return asdict(config)


def _macro_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows) or 1
    return {
        "n_fixtures": len(rows),
        "mean_ad_hit_rate": sum(r["ad_hit_rate"] for r in rows) / n,
        "mean_ad_coverage": sum(r["ad_coverage"] for r in rows) / n,
        "mean_scout_precision": sum(r["scout_precision"] for r in rows) / n,
        "mean_false_negative_risk": sum(r["false_negative_risk"] for r in rows) / n,
        "mean_token_reduction_pct": sum(r["token_reduction_pct"] for r in rows) / n,
        "mean_usd_reduction_pct": sum(r["usd_reduction_pct"] for r in rows) / n,
        "sum_full_input_tokens": sum(
            r["full_classifier"]["input_tokens"] for r in rows
        ),
        "sum_scout_input_tokens": sum(r["scout_confirm"]["input_tokens"] for r in rows),
        "sum_cached_repeat_input_tokens": sum(
            r["scout_confirm"]["cached_repeat_input_tokens"] for r in rows
        ),
        "sum_full_usd": sum(r["full_classifier"]["usd"] for r in rows),
        "sum_scout_usd": sum(r["scout_confirm"]["usd"] for r in rows),
    }


def _follow_up_notes() -> list[str]:
    return [
        "Run the same 6 fixtures plus 5–10 real Whisper transcripts with human "
        "or current-AdClassifier labels; budget Gemini 2.5 Flash at ≤ $0.50/day "
        "and enable the content-hash cache directory first.",
        "Measure live confirm precision/recall vs oracle: does Gemini drop "
        "false-positive windows (Shopify.com technical mentions, 'check out this "
        "paper') and tighten boundaries inside padded windows?",
        "Measure live false negatives on cue-sparse host-reads; decide whether "
        "a periodic full AdClassifier pass, extra scout phrases, or chapter "
        "metadata should cover that tail.",
        "Compare boundary error (start/end vs labels) against production "
        "BoundaryRefiner / WordBoundaryRefiner — Gemini confirm is intended to "
        "replace some of that second LLM pass, not add a third.",
        "Record actual litellm usage tokens vs the chars/4 estimate; adjust "
        "cost_model prices to the Gemini SKU you actually call.",
        "Do not change Feed defaults until live recall on real episodes is "
        "within an agreed band of the full AdClassifier walk.",
    ]


def _ready_checklist() -> list[str]:
    return [
        "[x] Experimental package isolated under "
        "`src/podcast_processor/experiments/` (not imported by PodcastProcessor).",
        "[x] CueDetector default constructor / `analyze` keys unchanged for production.",
        "[x] Gemini client mocks by default; live path requires GEMINI_API_KEY + "
        "PODLY_GEMINI_CONFIRM_LIVE=true.",
        "[x] Offline tests without API keys.",
        "[x] RESULTS.md + metrics.json from the harness.",
        "[ ] Human review of scout extras (`brought to you by`, etc.) before "
        "enabling them in production neighbor expansion.",
        "[ ] Live Gemini confirm on real transcripts ($0.50/day, cache-first).",
        "[ ] Decision on cue-sparse fallback before wiring into PodcastProcessor.",
        "[ ] Alembic not required (no model changes).",
    ]


def _pct(value: float) -> str:
    return f"{100.0 * value:.1f}%"
