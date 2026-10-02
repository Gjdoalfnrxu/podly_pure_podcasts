"""Auto gold baseline: representative shows, candidates, stubs, dry-run judge."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from podcast_processor.experiments.auto_gold import GOLD_FAMILY, REQUIRED_GENRES
from podcast_processor.experiments.auto_gold.audio_ops import (
    extract_chunk_wav,
    ffmpeg_available,
    probe_duration_seconds,
)
from podcast_processor.experiments.auto_gold.candidates import (
    always_preroll,
    candidates_from_publisher_markers,
    merge_candidates,
    propose_candidates,
)
from podcast_processor.experiments.auto_gold.constants import PREROLL_END_SECONDS
from podcast_processor.experiments.auto_gold.downloader import (
    download_audio,
    enclosure_is_dai,
    episode_from_rss,
    fetch_show_episode,
    parse_clock_time,
    parse_publisher_markers_xml,
)
from podcast_processor.experiments.auto_gold.judge import (
    GoldJudge,
    groq_key_present,
    parse_judge_json,
)
from podcast_processor.experiments.auto_gold.pipeline import (
    AutoGoldConfig,
    run_auto_gold,
)
from podcast_processor.experiments.auto_gold.report import render_report
from podcast_processor.experiments.auto_gold.shows import (
    ShowListError,
    load_shows,
    parse_shows,
    prioritize_genre_coverage,
    validate_representative_sample,
)
from podcast_processor.experiments.auto_gold.types import (
    CandidateChunk,
    EpisodeRef,
    PublisherMarker,
    ShowSpec,
)
from podcast_processor.experiments.auto_gold.whisper_chunks import (
    ChunkWhisper,
    whisper_import_status,
)
from shared import defaults as DEFAULTS
from shared.env import gemini_api_key, groq_api_key

SAMPLE_RSS = b"""<?xml version="1.0"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:psc="http://podlove.org/simple-chapters">
  <channel>
    <title>Example News</title>
    <item>
      <title>Morning Brief</title>
      <guid>ep-1</guid>
      <enclosure url="https://megaphone.fm/ep1.mp3" type="audio/mpeg" length="1000"/>
      <itunes:duration>1800</itunes:duration>
      <psc:chapters version="1.2">
        <psc:chapter start="00:00:00.000" title="Cold open"/>
        <psc:chapter start="00:01:00.000" title="Sponsor message"/>
        <psc:chapter start="00:03:00.000" title="The story"/>
      </psc:chapters>
    </item>
  </channel>
</rss>
"""


def _show(
    show_id: str = "s",
    title: str = "T",
    publisher: str = "P",
    genre: str = "news",
    ad_mechanism: str = "x",
    rss_url: str = "https://example.com/rss",
) -> ShowSpec:
    return ShowSpec(
        show_id=show_id,
        title=title,
        publisher=publisher,
        genre=genre,
        ad_mechanism=ad_mechanism,
        rss_url=rss_url,
    )


def test_production_flag_stays_false() -> None:
    assert DEFAULTS.ENABLE_BOW_SCOUT_GEMINI_CONFIRM is False
    assert GOLD_FAMILY == "general_podcast_ads"


def test_auto_gold_package_does_not_import_adclassifier() -> None:
    root = (
        Path(__file__).resolve().parents[1]
        / "podcast_processor"
        / "experiments"
        / "auto_gold"
    )
    imported: list[str] = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.append(module)
                imported.extend(f"{module}.{alias.name}" for alias in node.names)
    blob = " ".join(imported)
    assert "ad_classifier" not in blob
    assert "AdClassifier" not in blob


def test_committed_shows_are_representative() -> None:
    shows = load_shows()
    genres = {show.genre for show in shows}
    assert genres == REQUIRED_GENRES or genres >= REQUIRED_GENRES
    finance = [show for show in shows if show.genre == "finance"]
    assert 0 < len(finance) < len(shows)
    assert len(shows) >= 6
    assert {show.show_id for show in finance} != {"planet_money", "the_indicator"}
    ids = {show.show_id for show in shows}
    assert "planet_money" in ids
    assert "marketplace" in ids
    assert "serial" in ids
    assert "crime_junkie" in ids
    assert "bill_simmons" in ids
    # Not a two-show catalog.
    assert len(shows) > 2


def test_prioritize_genre_coverage_front_loads_required_genres() -> None:
    shows = load_shows()
    ordered = prioritize_genre_coverage(shows)
    assert {s.show_id for s in ordered} == {s.show_id for s in shows}
    first_six_genres = [s.genre for s in ordered[:6]]
    assert set(first_six_genres) == REQUIRED_GENRES
    assert len(first_six_genres) == len(set(first_six_genres))


def test_rejects_finance_only_sample() -> None:
    shows = [
        _show(show_id="a", genre="finance"),
        _show(show_id="b", genre="finance"),
        _show(show_id="c", genre="finance"),
        _show(show_id="d", genre="finance"),
        _show(show_id="e", genre="finance"),
        _show(show_id="f", genre="finance"),
    ]
    with pytest.raises(ShowListError, match="finance-only"):
        validate_representative_sample(shows)


def test_rejects_wrong_gold_family() -> None:
    with pytest.raises(ShowListError, match="gold_family"):
        parse_shows({"gold_family": "other", "sampling_unit": "show", "shows": []})


def test_always_preroll_zero_to_ninety() -> None:
    chunk = always_preroll(3600.0)
    assert chunk.start == 0.0
    assert chunk.end == PREROLL_END_SECONDS
    assert "preroll_always" in chunk.sources
    short = always_preroll(45.0)
    assert short.end == 45.0


def test_publisher_markers_are_positives_only() -> None:
    markers = [
        PublisherMarker(0.0, 60.0, "Cold open", "psc"),
        PublisherMarker(60.0, 180.0, "Sponsor message", "psc"),
        PublisherMarker(180.0, 400.0, "The story", "psc"),
    ]
    found = candidates_from_publisher_markers(markers, 1800.0)
    assert len(found) == 1
    assert found[0].publisher_positive
    assert found[0].start == 60.0
    # Unmarked chapters are not emitted as negative / non-ad gold.
    titles = {c.notes for c in found}
    assert not any("Cold open" in t for t in titles)
    assert not any("The story" in t for t in titles)


def test_merge_pad_two_seconds() -> None:
    chunks = [
        CandidateChunk(10.0, 20.0, ["a"]),
        CandidateChunk(21.0, 30.0, ["b"]),
    ]
    merged = merge_candidates(chunks, pad_seconds=2.0, merge_gap_seconds=2.0)
    assert len(merged) == 1
    assert merged[0].start == pytest.approx(8.0)
    assert merged[0].end == pytest.approx(32.0)
    assert merged[0].sources == ["a", "b"]


def test_propose_includes_preroll_and_dai_probes() -> None:
    episode = EpisodeRef(
        show=_show(),
        episode_title="E",
        audio_url="https://megaphone.fm/x.mp3",
        duration_seconds=1800.0,
        guid="g",
        published=None,
        dai_likely=True,
        publisher_markers=[
            PublisherMarker(600.0, 660.0, "ad break", "psc"),
        ],
    )
    chunks = propose_candidates(episode)
    sources = {src for chunk in chunks for src in chunk.sources}
    assert "preroll_always" in sources
    assert "publisher_marker" in sources
    assert "dai_probe" in sources
    assert chunks[0].start == 0.0


def test_parse_psc_and_dai_from_rss() -> None:
    import feedparser

    markers = parse_publisher_markers_xml(SAMPLE_RSS)
    assert [m.title for m in markers] == ["Cold open", "Sponsor message", "The story"]
    assert parse_clock_time("00:01:00.000") == 60.0
    parsed = feedparser.parse(SAMPLE_RSS)
    show = _show(show_id="news1", genre="news")
    episode = episode_from_rss(show, SAMPLE_RSS, parsed)
    assert episode.rss_ok
    assert episode.dai_likely
    assert enclosure_is_dai(episode.audio_url)
    assert episode.duration_seconds == 1800.0
    positives = candidates_from_publisher_markers(
        episode.publisher_markers, episode.duration_seconds
    )
    assert any("Sponsor" in c.notes for c in positives)


def test_whisper_auto_stubs_when_unimportable_or_mocked() -> None:
    ok, detail = whisper_import_status()
    # Tests mock whisper/torch in conftest, so auto must stub — not claim local.
    assert ok is False
    backend = ChunkWhisper(mode="auto")
    assert backend.backend == "stub"
    chunk = always_preroll(90.0)
    result = backend.transcribe_chunk(chunk, Path("/tmp/missing.wav"))
    assert result.skipped
    assert result.text == ""
    assert (
        "stub" in (result.skip_reason or "").lower()
        or "whisper" in (result.skip_reason or "").lower()
    )
    del detail


def test_injected_whisper_transcribes_chunk_only(tmp_path: Path) -> None:
    wav = tmp_path / "c.wav"
    wav.write_bytes(b"fake")

    def fake_transcribe(path: str, language: str) -> list[dict[str, object]]:
        assert path == str(wav)
        assert language == "en"
        return [{"start": 0.0, "end": 2.0, "text": "use code SAVE"}]

    whisper = ChunkWhisper(mode="stub", transcribe_fn=fake_transcribe)
    chunk = CandidateChunk(10.0, 20.0, ["preroll_always"])
    out = whisper.transcribe_chunk(chunk, wav)
    assert not out.skipped
    assert out.segments[0].start == pytest.approx(10.0)
    assert "SAVE" in out.text


def test_judge_dry_run_ignores_groq_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_KEY", "gsk_should_not_be_used")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert groq_api_key()
    assert not gemini_api_key()
    assert groq_key_present()
    judge = GoldJudge(mode="auto")
    assert judge.mode == "dry-run"
    from podcast_processor.experiments.auto_gold.types import (
        ChunkTranscript,
        TranscriptSegment,
    )

    transcript = ChunkTranscript(
        chunk=always_preroll(90.0),
        audio_path=None,
        skipped=False,
        skip_reason=None,
        backend="injected",
        text="this episode is sponsored by example bank",
        segments=[
            TranscriptSegment(0.0, 5.0, "this episode is sponsored by example bank")
        ],
    )
    label = judge.judge(transcript)
    assert label.skipped
    assert "GROQ_KEY" in (label.skip_reason or "")
    assert "unused" in (label.skip_reason or "").lower()


def test_judge_injected_does_not_call_groq() -> None:
    seen: dict[str, object] = {}

    def completion(**kwargs: object) -> SimpleNamespace:
        seen["model"] = kwargs["model"]
        payload = json.dumps(
            {
                "is_ad": True,
                "ad_spans": [{"start": 0.0, "end": 20.0, "confidence": 0.9}],
                "content_type": "promotional_external",
                "confidence": 0.9,
            }
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
        )

    judge = GoldJudge(mode="dry-run", completion_fn=completion)
    from podcast_processor.experiments.auto_gold.types import ChunkTranscript

    transcript = ChunkTranscript(
        chunk=always_preroll(90.0),
        audio_path=None,
        skipped=False,
        skip_reason=None,
        backend="injected",
        text="sponsored by",
        segments=[],
    )
    label = judge.judge(transcript)
    assert label.is_ad
    assert seen["model"]
    assert "groq" not in str(seen["model"]).lower()


def test_parse_judge_json_empty() -> None:
    label = parse_judge_json("not json", model="x")
    assert label.is_ad is False
    assert label.ad_spans == []


def test_offline_pipeline_fills_report(tmp_path: Path) -> None:
    result = run_auto_gold(
        AutoGoldConfig(
            output_dir=tmp_path,
            offline=True,
            whisper_mode="stub",
            judge_mode="dry-run",
        )
    )
    assert result.gold_family == "general_podcast_ads"
    assert result.sampling_unit == "show"
    assert {row.show.genre for row in result.shows} >= REQUIRED_GENRES
    assert result.whisper_backend == "stub"
    assert result.judge_mode == "dry-run"
    assert result.production_flag_enable_bow_scout_gemini_confirm is False
    assert any("whisper" in step for step in result.blocked_steps)
    assert any("gemini_judge" in step for step in result.blocked_steps)
    report = (tmp_path / "BASELINE_REPORT.md").read_text(encoding="utf-8")
    assert "general_podcast_ads" in report
    assert "Whisper-shaped gold" in report
    assert "Same-family judge" in report
    assert "_Not yet run._" not in report
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["n_shows"] == len(result.shows)
    for row in result.shows:
        assert row.candidates
        assert row.candidates[0].start == 0.0
        assert row.transcripts[0].skipped
        assert row.labels[0].skipped


def test_report_template_has_locked_family() -> None:
    from podcast_processor.experiments.auto_gold.report import default_template_path

    text = default_template_path().read_text(encoding="utf-8")
    assert GOLD_FAMILY in text
    for name in (
        "STATUS",
        "SAMPLE",
        "CANDIDATES",
        "WHISPER",
        "JUDGE",
        "SPEND",
        "BLOCKED",
        "PRODUCTION_FLAGS",
    ):
        assert f"BEGIN:AUTO_GOLD_{name}" in text


def test_report_render_roundtrip(tmp_path: Path) -> None:
    from podcast_processor.experiments.auto_gold.report import default_template_path

    result = run_auto_gold(
        AutoGoldConfig(
            output_dir=tmp_path / "run",
            offline=True,
            whisper_mode="stub",
            judge_mode="dry-run",
        )
    )
    rendered = render_report(
        default_template_path().read_text(encoding="utf-8"), result
    )
    assert "enable_bow_scout_gemini_confirm" in rendered
    assert "`False`" in rendered or "`false`" in rendered.lower()


def test_download_audio_unlinks_partial_on_max_bytes(tmp_path: Path) -> None:
    dest = tmp_path / "ep.mp3"

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def iter_content(self, chunk_size: int = 8192) -> list[bytes]:
            return [b"x" * 100, b"y" * 100]

    def fake_get(url: str, **kwargs: object) -> _Resp:
        return _Resp()

    with pytest.raises(OSError, match="max_bytes"):
        download_audio(
            "https://example.com/ep.mp3",
            dest,
            get=fake_get,
            max_bytes=50,
        )
    assert not dest.exists()


def test_fetch_show_episode_parses_rss(tmp_path: Path) -> None:
    class _Resp:
        content = SAMPLE_RSS
        status_code = 200

        def raise_for_status(self) -> None:
            return None

    def fake_get(url: str, **kwargs: object) -> _Resp:
        assert "example.com" in url
        return _Resp()

    show = _show(show_id="news1", genre="news", rss_url="https://example.com/rss")
    episode, audio = fetch_show_episode(show, tmp_path, download=False, get=fake_get)
    assert audio is None
    assert episode.rss_ok
    assert episode.episode_title == "Morning Brief"
    assert episode.dai_likely
    assert (tmp_path / "rss" / "news1.xml").exists()


def test_gemini_api_key_does_not_use_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_GENERATIVE_AI_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_KEY", "gsk_nope")
    assert gemini_api_key() == ""
    monkeypatch.setenv("GOOGLE_API_KEY", "AIza-test-not-real")
    assert gemini_api_key() == "AIza-test-not-real"


def test_skips_short_feed_notes() -> None:
    import feedparser

    xml = b"""<?xml version="1.0"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>Example Comedy</title>
    <item>
      <title>A Message From The Host</title>
      <enclosure url="https://example.com/note.mp3" type="audio/mpeg"/>
      <itunes:duration>45</itunes:duration>
    </item>
    <item>
      <title>Real Episode</title>
      <enclosure url="https://example.com/ep.mp3" type="audio/mpeg"/>
      <itunes:duration>3600</itunes:duration>
    </item>
  </channel>
</rss>
"""
    episode = episode_from_rss(_show(genre="comedy"), xml, feedparser.parse(xml))
    assert episode.episode_title == "Real Episode"
    assert episode.duration_seconds == 3600.0


def test_ffmpeg_extracts_candidate_chunk(tmp_path: Path) -> None:
    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe required")
    src = tmp_path / "tone.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=30",
            "-ar",
            "16000",
            "-ac",
            "1",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    dest = tmp_path / "chunk.wav"
    extract_chunk_wav(src, dest, 0.0, 10.0)
    assert dest.stat().st_size > 0
    duration = probe_duration_seconds(dest)
    assert duration is not None
    assert 9.0 < duration < 11.0


def test_judge_budget_cap_skips_without_calling() -> None:
    called = {"n": 0}

    def completion(**kwargs: object) -> None:
        called["n"] += 1
        raise AssertionError("budget cap should skip the live call")

    judge = GoldJudge(mode="dry-run", completion_fn=completion, max_usd=0.0)
    from podcast_processor.experiments.auto_gold.types import ChunkTranscript

    transcript = ChunkTranscript(
        chunk=always_preroll(90.0),
        audio_path=None,
        skipped=False,
        skip_reason=None,
        backend="injected",
        text="sponsored by example",
        segments=[],
    )
    label = judge.judge(transcript)
    assert label.skipped
    assert "budget cap" in (label.skip_reason or "")
    assert called["n"] == 0
    assert judge.n_budget_skips == 1
    assert judge.spent_usd == 0.0
