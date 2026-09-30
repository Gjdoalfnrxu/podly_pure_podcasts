"""Secret-safe ingest of real labeled transcripts into golden staging.

Never calls Groq/Gemini. Never writes API keys. Promotion into corpus v1 is
a separate, explicit `--write-corpus --update-baseline` step after review.

The Daily / Soft Skills *style* means: news-briefing or interview episode
with preroll/midroll host-reads, human-labeled ad intervals, Whisper-like
segment JSON. Do not paste copyrighted episode text into the repo.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from podcast_processor.experiments.fixtures import (
    episode_from_json,
    fixture_to_json,
    sha256_file,
)
from podcast_processor.experiments.types import EpisodeFixture

SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"gsk_[A-Za-z0-9]{8,}"),
    re.compile(r"AIza[0-9A-Za-z_\-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"(?:GEMINI|GROQ|OPENAI|LLM)_API_KEY\s*[:=]\s*\S+", re.I),
    re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.I),
    re.compile(r"(?:api[_-]?key|authorization)\s*[:=]\s*\S+", re.I),
)

FORBIDDEN_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "secret",
        "password",
        "token",
        "access_token",
        "groq_api_key",
        "gemini_api_key",
        "openai_api_key",
        "llm_api_key",
    }
)

REQUIRED_FIELDS = ("id", "title", "podcast_title", "duration_seconds", "segments")


class GoldenIngestError(ValueError):
    """Invalid or unsafe golden payload."""


@dataclass(frozen=True)
class GoldenIngestReport:
    fixture_id: str
    n_segments: int
    n_labeled_ads: int
    sha256: str
    output_path: Path
    source_style: str


def find_secrets(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in SECRET_PATTERNS:
        for match in pattern.finditer(text):
            hits.append(match.group(0)[:24] + "…")
    return hits


def _walk_forbidden_keys(obj: object, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            key_l = str(key).lower()
            path = f"{prefix}.{key}" if prefix else str(key)
            if key_l in FORBIDDEN_KEYS:
                found.append(path)
            found.extend(_walk_forbidden_keys(value, path))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            found.extend(_walk_forbidden_keys(value, f"{prefix}[{index}]"))
    return found


def validate_golden_payload(payload: dict[str, Any]) -> EpisodeFixture:
    missing = [field for field in REQUIRED_FIELDS if field not in payload]
    if missing:
        raise GoldenIngestError(f"golden JSON missing fields: {missing}")
    raw = json.dumps(payload, ensure_ascii=False)
    secrets = find_secrets(raw)
    if secrets:
        raise GoldenIngestError(
            "refusing to ingest: secret-like strings found "
            f"({len(secrets)} hit(s)). Strip API keys before retrying."
        )
    forbidden = _walk_forbidden_keys(payload)
    if forbidden:
        raise GoldenIngestError(
            f"refusing to ingest: forbidden key names {forbidden}. "
            "Never store credentials next to transcripts."
        )
    if "labeled_ads" not in payload:
        raise GoldenIngestError(
            "labeled_ads is required (use [] for confirmed ad-free episodes)"
        )
    try:
        episode = episode_from_json(payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise GoldenIngestError(
            f"golden JSON failed EpisodeFixture schema: {exc}"
        ) from exc
    if not episode.segments:
        raise GoldenIngestError("golden fixture has no segments")
    return episode


def ingest_golden(
    source: Path,
    staging_dir: Path,
    *,
    source_style: str = "unspecified",
) -> GoldenIngestReport:
    """Validate a labeled transcript JSON and write a sanitized staging copy."""
    if source.name.startswith(".env") or source.suffix in {".pem", ".key"}:
        raise GoldenIngestError(f"refusing to ingest credential file {source}")
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GoldenIngestError("golden file must be a JSON object")
    # Drop operator metadata that often carries env dumps.
    for key in list(payload.keys()):
        if str(key).lower() in FORBIDDEN_KEYS or str(key).lower().endswith("_api_key"):
            raise GoldenIngestError(
                f"refusing to ingest: field {key!r} looks like a secret"
            )
    episode = validate_golden_payload(payload)
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / f"{episode.fixture_id}.json"
    sanitized = fixture_to_json(episode)
    sanitized["source_style"] = source_style
    sanitized["notes"] = (
        f"{episode.notes} [ingested style={source_style}; secrets stripped; "
        "not yet in corpus v1]"
    ).strip()
    dest.write_text(json.dumps(sanitized, indent=2) + "\n", encoding="utf-8")
    return GoldenIngestReport(
        fixture_id=episode.fixture_id,
        n_segments=len(episode.segments),
        n_labeled_ads=len(episode.labeled_ads),
        sha256=sha256_file(dest),
        output_path=dest,
        source_style=source_style,
    )


def example_soft_skills_style_payload() -> dict[str, Any]:
    """Synthetic interview-style skeleton. Not a real Soft Skills episode."""
    segments = [
        {
            "sequence_num": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "text": "Welcome back to the workplace interview.",
        },
        {
            "sequence_num": 1,
            "start_time": 5.0,
            "end_time": 10.0,
            "text": "Go to example-sponsor.test/show and use code SOFT20.",
        },
        {
            "sequence_num": 2,
            "start_time": 10.0,
            "end_time": 15.0,
            "text": "The guest described a one-on-one that went sideways.",
        },
    ]
    return {
        "id": "soft_skills_style_template",
        "title": "Synthetic interview-style golden template",
        "podcast_title": "Example Workplace Show",
        "podcast_topic": "management interviews",
        "duration_seconds": 15.0,
        "notes": "Template only. Replace with a human-labeled Whisper export.",
        "labeled_ads": [
            {
                "start": 5.0,
                "end": 10.0,
                "kind": "preroll",
                "notes": "synthetic sponsor; not a real show",
            }
        ],
        "segments": segments,
    }


def example_the_daily_style_payload() -> dict[str, Any]:
    """Synthetic news-briefing skeleton. Not The Daily; no NYT text."""
    segments = [
        {
            "sequence_num": 0,
            "start_time": 0.0,
            "end_time": 5.0,
            "text": "From the example newsroom, this is the morning briefing.",
        },
        {
            "sequence_num": 1,
            "start_time": 5.0,
            "end_time": 12.0,
            "text": "This episode is sponsored by Example Bank. Use code BRIEF.",
        },
        {
            "sequence_num": 2,
            "start_time": 12.0,
            "end_time": 20.0,
            "text": "Today the team walks through a public policy hearing.",
        },
    ]
    return {
        "id": "news_briefing_style_template",
        "title": "Synthetic news-briefing golden template",
        "podcast_title": "Example Morning Briefing",
        "podcast_topic": "news",
        "duration_seconds": 20.0,
        "notes": "Template only. Human-label real Whisper JSON offline.",
        "labeled_ads": [
            {
                "start": 5.0,
                "end": 12.0,
                "kind": "preroll",
                "notes": "synthetic sponsor",
            }
        ],
        "segments": segments,
    }
