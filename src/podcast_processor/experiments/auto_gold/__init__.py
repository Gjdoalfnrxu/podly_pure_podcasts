"""Automated gold baseline for podcast ad-removal eval.

Experiment-only. Does **not** import or call production ``AdClassifier``.
Does **not** flip ``enable_bow_scout_gemini_confirm``.

Locked gold family: ``general_podcast_ads``. Sampling unit: show.
"""

from __future__ import annotations

from podcast_processor.experiments.auto_gold.constants import (
    GOLD_FAMILY,
    REQUIRED_GENRES,
    SAMPLING_UNIT,
)

__all__ = ["GOLD_FAMILY", "REQUIRED_GENRES", "SAMPLING_UNIT"]
