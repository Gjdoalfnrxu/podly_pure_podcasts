"""Curated representative show list. Sampling unit = show."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from podcast_processor.experiments.auto_gold.constants import (
    GOLD_FAMILY,
    MAX_FINANCE_FRACTION,
    MIN_SHOWS,
    REQUIRED_GENRES,
    SAMPLING_UNIT,
)
from podcast_processor.experiments.auto_gold.types import ShowSpec

SHOWS_JSON_NAME = "shows.json"


class ShowListError(ValueError):
    """Invalid or non-representative show catalog."""


def default_shows_path() -> Path:
    """docs/experiments/auto_gold/shows.json relative to the repo root."""
    return (
        Path(__file__).resolve().parents[4]
        / "docs"
        / "experiments"
        / "auto_gold"
        / SHOWS_JSON_NAME
    )


def load_show_catalog(path: Path | None = None) -> dict[str, Any]:
    target = path or default_shows_path()
    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ShowListError("shows.json must be a JSON object")
    return payload


def parse_shows(payload: dict[str, Any]) -> list[ShowSpec]:
    family = str(payload.get("gold_family") or "")
    if family != GOLD_FAMILY:
        raise ShowListError(
            f"gold_family must be locked to {GOLD_FAMILY!r}, got {family!r}"
        )
    unit = str(payload.get("sampling_unit") or "")
    if unit != SAMPLING_UNIT:
        raise ShowListError(f"sampling_unit must be {SAMPLING_UNIT!r}, got {unit!r}")
    raw_shows = payload.get("shows")
    if not isinstance(raw_shows, list) or not raw_shows:
        raise ShowListError("shows.json needs a non-empty shows array")
    shows: list[ShowSpec] = []
    for row in raw_shows:
        if not isinstance(row, dict):
            raise ShowListError("each show must be an object")
        genre = str(row.get("genre") or "").strip().lower()
        show = ShowSpec(
            show_id=str(row["id"]).strip(),
            title=str(row["title"]).strip(),
            publisher=str(row.get("publisher") or "").strip(),
            genre=genre,
            ad_mechanism=str(row.get("ad_mechanism") or "").strip(),
            rss_url=str(row["rss_url"]).strip(),
            notes=str(row.get("notes") or "").strip(),
        )
        if not show.show_id or not show.title or not show.rss_url:
            raise ShowListError(f"show missing id/title/rss_url: {row!r}")
        shows.append(show)
    validate_representative_sample(shows)
    return shows


def load_shows(path: Path | None = None) -> list[ShowSpec]:
    return parse_shows(load_show_catalog(path))


def validate_representative_sample(shows: list[ShowSpec]) -> None:
    """Refuse finance-only or two-show samples. Sampling unit is the show."""
    if len(shows) < MIN_SHOWS:
        raise ShowListError(
            f"need at least {MIN_SHOWS} shows (one per required genre); got {len(shows)}"
        )
    finance = [show for show in shows if show.genre == "finance"]
    if shows and len(finance) == len(shows):
        raise ShowListError(
            "refusing finance-only sample; general_podcast_ads is multi-genre"
        )
    genres = {show.genre for show in shows}
    missing = REQUIRED_GENRES - genres
    if missing:
        raise ShowListError(
            "sample is not representative; missing genres "
            f"{sorted(missing)}. Required: {sorted(REQUIRED_GENRES)}"
        )
    if len(finance) / len(shows) - 1e-12 > MAX_FINANCE_FRACTION:
        raise ShowListError(
            "finance shows dominate the sample "
            f"({len(finance)}/{len(shows)}); keep finance <= "
            f"{MAX_FINANCE_FRACTION:.0%} because sampling unit is show"
        )
    ids = [show.show_id for show in shows]
    if len(ids) != len(set(ids)):
        raise ShowListError("duplicate show ids")


def filter_shows(
    shows: list[ShowSpec],
    *,
    genres: set[str] | None = None,
    show_ids: set[str] | None = None,
    require_representative: bool = True,
) -> list[ShowSpec]:
    selected = shows
    if genres:
        wanted = {g.strip().lower() for g in genres}
        selected = [show for show in selected if show.genre in wanted]
    if show_ids:
        wanted_ids = {s.strip() for s in show_ids}
        selected = [show for show in selected if show.show_id in wanted_ids]
    if require_representative:
        validate_representative_sample(selected)
    elif not selected:
        raise ShowListError("no shows left after filters")
    return selected
