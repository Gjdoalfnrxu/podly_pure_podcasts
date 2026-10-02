"""Locked constants for the general_podcast_ads gold family."""

from __future__ import annotations

from shared.defaults import CHAPTER_FILTER_DEFAULT_STRINGS

# Locked taxonomy. Do not mix with Daily/Soft-Skills style goldens or a
# finance-only sample. The runner refuses any other family string.
GOLD_FAMILY = "general_podcast_ads"
SAMPLING_UNIT = "show"

REQUIRED_GENRES: frozenset[str] = frozenset(
    {"news", "comedy", "true_crime", "tech", "sports", "finance"}
)

# High-recall preroll: always propose the first 90 seconds.
PREROLL_START_SECONDS = 0.0
PREROLL_END_SECONDS = 90.0

# Merge pad ±1–2s (default 2s) after unioning candidate sources.
DEFAULT_PAD_SECONDS = 2.0
DEFAULT_MERGE_GAP_SECONDS = 2.0

# Optional DAI probes (high recall, not gold labels).
DAI_PROBE_FRACTIONS: tuple[float, float] = (0.33, 0.66)
DAI_PROBE_HALF_WINDOW_SECONDS = 15.0
DAI_PROBE_MIN_DURATION_SECONDS = 600.0

PUBLISHER_MARKER_FILTERS: tuple[str, ...] = tuple(
    s.strip().lower()
    for s in CHAPTER_FILTER_DEFAULT_STRINGS.split(",")
    if s.strip()
)

DAI_HOST_MARKERS: tuple[str, ...] = (
    "megaphone.fm",
    "podtrac.com",
    "dts.podtrac.com",
    "art19.com",
    "adswizz",
    "tritondigital",
    "pdst.fm",
    "chrt.fm",
    "prefix.prod.adswizz",
    "ads.mc.advangelists.com",
)

DEFAULT_WHISPER_MODEL = "base.en"
DEFAULT_GEMINI_MODEL = "gemini/gemini-2.5-flash"

# Finance may be present but must not dominate the sample.
MAX_FINANCE_FRACTION = 1.0 / 3.0
MIN_SHOWS = len(REQUIRED_GENRES)
