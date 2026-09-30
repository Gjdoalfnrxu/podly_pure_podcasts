"""Secret-safe golden ingest (no API keys, no network)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from podcast_processor.experiments.golden_ingest import (
    GoldenIngestError,
    example_soft_skills_style_payload,
    example_the_daily_style_payload,
    ingest_golden,
    validate_golden_payload,
)


def test_templates_validate_without_secrets() -> None:
    daily = validate_golden_payload(example_the_daily_style_payload())
    soft = validate_golden_payload(example_soft_skills_style_payload())
    assert daily.fixture_id == "news_briefing_style_template"
    assert soft.fixture_id == "soft_skills_style_template"
    assert daily.labeled_ads
    assert soft.labeled_ads


def test_rejects_api_key_material(tmp_path: Path) -> None:
    payload = example_soft_skills_style_payload()
    payload["notes"] = "GROQ_API_KEY=gsk_abcdefghijklmnop"
    src = tmp_path / "leaky.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GoldenIngestError, match="secret-like"):
        ingest_golden(src, tmp_path / "staging")


def test_rejects_api_key_field_name(tmp_path: Path) -> None:
    payload = example_the_daily_style_payload()
    payload["api_key"] = "not-a-real-key-but-forbidden"
    src = tmp_path / "fields.json"
    src.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(GoldenIngestError, match="secret"):
        ingest_golden(src, tmp_path / "staging")


def test_ingest_writes_sanitized_staging_copy(tmp_path: Path) -> None:
    src = tmp_path / "ok.json"
    src.write_text(json.dumps(example_soft_skills_style_payload()), encoding="utf-8")
    staging = tmp_path / "staging"
    report = ingest_golden(src, staging, source_style="interview")
    assert report.output_path.exists()
    written = json.loads(report.output_path.read_text(encoding="utf-8"))
    assert "api_key" not in written
    assert written["source_style"] == "interview"
    assert report.sha256
    assert report.n_segments == 3
