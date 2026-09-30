"""Comparable ad-time metrics for production-like vs scout±confirm paths.

Time-based precision/recall/F1 use merged, non-overlapping spans. Residual
cue rate is leftover CueDetector strong hits after proposed cuts. Duration
stubs reuse the AudioProcessor fade formula.
"""

from __future__ import annotations

from typing import Any

from podcast_processor.experiments.bow_scout import overlap_seconds
from podcast_processor.experiments.removal_verifier import (
    ads_to_ms,
    expected_output_duration_ms,
    residual_cue_scan,
)
from podcast_processor.experiments.types import LabeledAd, ScoutSegment, ScoutWindow
from shared import defaults as DEFAULTS

Span = tuple[float, float]

# Minimum overlap (seconds) to count a labeled ad block as "hit".
AD_HIT_OVERLAP_SECONDS = 0.5


def merge_spans(spans: list[Span]) -> list[Span]:
    """Merge overlapping/adjacent spans. Adjacent means touching (gap == 0)."""
    cleaned = sorted(
        (float(start), float(end)) for start, end in spans if float(end) > float(start)
    )
    if not cleaned:
        return []
    merged: list[Span] = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def span_duration(spans: list[Span]) -> float:
    return float(sum(end - start for start, end in merge_spans(spans)))


def windows_to_spans(windows: list[ScoutWindow]) -> list[Span]:
    return merge_spans([(window.start_time, window.end_time) for window in windows])


def ads_to_spans(ads: list[LabeledAd]) -> list[Span]:
    return merge_spans([(ad.start, ad.end) for ad in ads])


def overlap_span_seconds(left: list[Span], right: list[Span]) -> float:
    a = merge_spans(left)
    b = merge_spans(right)
    total = 0.0
    for a_start, a_end in a:
        for b_start, b_end in b:
            total += overlap_seconds(a_start, a_end, b_start, b_end)
    return total


def time_prf(labeled: list[Span], predicted: list[Span]) -> dict[str, float]:
    """Time-based precision/recall/F1 over merged spans.

    Empty labeled + empty predicted is a perfect score (ad-free episode).
    """
    lab = merge_spans(labeled)
    pred = merge_spans(predicted)
    tp = overlap_span_seconds(lab, pred)
    pred_dur = span_duration(pred)
    lab_dur = span_duration(lab)
    fp = max(0.0, pred_dur - tp)
    fn = max(0.0, lab_dur - tp)
    precision = tp / (tp + fp) if (tp + fp) > 0 else 1.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 1.0
    if precision + recall <= 0:
        f1 = 0.0
    else:
        f1 = 2.0 * precision * recall / (precision + recall)
    return {
        "tp_seconds": tp,
        "fp_seconds": fp,
        "fn_seconds": fn,
        "labeled_seconds": lab_dur,
        "predicted_seconds": pred_dur,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def ad_hit_rate(ads: list[LabeledAd], predicted: list[Span]) -> float:
    if not ads:
        return 1.0
    pred = merge_spans(predicted)
    hits = 0
    for ad in ads:
        if any(
            overlap_seconds(ad.start, ad.end, start, end) > AD_HIT_OVERLAP_SECONDS
            for start, end in pred
        ):
            hits += 1
    return float(hits) / float(len(ads))


def missed_ads(ads: list[LabeledAd], predicted: list[Span]) -> list[dict[str, Any]]:
    pred = merge_spans(predicted)
    missed: list[dict[str, Any]] = []
    for ad in ads:
        if not any(
            overlap_seconds(ad.start, ad.end, start, end) > AD_HIT_OVERLAP_SECONDS
            for start, end in pred
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


def residual_cue_rate(
    segments: list[ScoutSegment],
    removed: list[Span],
) -> dict[str, float | int]:
    ads = [LabeledAd(start, end, "predicted") for start, end in merge_spans(removed)]
    leftovers = residual_cue_scan(segments, ads)
    n = len(segments) or 1
    return {
        "residual_strong_cues": len(leftovers),
        "residual_strong_cue_rate": float(len(leftovers)) / float(n),
        "n_segments": len(segments),
    }


def duration_stub(
    source_seconds: float,
    removed: list[Span],
    fade_ms: int = DEFAULTS.OUTPUT_FADE_MS,
) -> dict[str, int]:
    ads = [LabeledAd(start, end) for start, end in merge_spans(removed)]
    account = expected_output_duration_ms(
        source_ms=int(source_seconds * 1000),
        ad_segments_ms=ads_to_ms(ads),
        fade_ms=fade_ms,
        complex_filter=True,
    )
    return {
        "source_ms": account.source_ms,
        "removed_ms": account.removed_ms,
        "fade_added_ms": account.fade_added_ms,
        "expected_output_ms": account.expected_output_ms,
        "n_cuts": account.n_cuts,
    }


def path_quality_metrics(
    *,
    labeled_ads: list[LabeledAd],
    predicted: list[Span],
    segments: list[ScoutSegment],
    source_seconds: float,
) -> dict[str, Any]:
    prf = time_prf(ads_to_spans(labeled_ads), predicted)
    residuals = residual_cue_rate(segments, predicted)
    hit = ad_hit_rate(labeled_ads, predicted)
    return {
        "ad_hit_rate": hit,
        "false_negative_rate": 1.0 - hit,
        "time_precision": prf["precision"],
        "time_recall": prf["recall"],
        "time_f1": prf["f1"],
        "tp_seconds": prf["tp_seconds"],
        "fp_seconds": prf["fp_seconds"],
        "fn_seconds": prf["fn_seconds"],
        "labeled_seconds": prf["labeled_seconds"],
        "predicted_seconds": prf["predicted_seconds"],
        "missed_ads": missed_ads(labeled_ads, predicted),
        "residual_strong_cues": residuals["residual_strong_cues"],
        "residual_strong_cue_rate": residuals["residual_strong_cue_rate"],
        "duration": duration_stub(source_seconds, predicted),
    }
