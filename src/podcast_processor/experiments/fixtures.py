"""Synthetic labeled transcripts for offline scout eval.

Existing tests/data only has an unlabeled MP3 (count_0_99.mp3). Production
fixtures do not include ad labels, so these transcripts encode known ad spans
plus the false-positive / false-negative cases CueDetector is likely to hit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from podcast_processor.experiments.types import EpisodeFixture, LabeledAd, ScoutSegment

DATA_DIR = Path(__file__).resolve().parent / "data"


def _as_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TypeError(f"expected numeric value, got {type(value)!r}")
    return float(value)


def _as_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise TypeError(f"expected numeric value, got {type(value)!r}")
    return int(value)


# Content lines avoid CueDetector core + scout-extra patterns.
_CONTENT = [
    "Today we are talking about distributed systems and latency.",
    "The guest described their early career in hardware labs.",
    "I think that is a really interesting point about memory.",
    "The team spent months measuring tail latency in production.",
    "Listeners wrote in with questions about replication.",
    "We recorded this conversation on a Thursday morning.",
    "That story about the first prototype always lands well.",
    "The architecture shifted after the third outage.",
    "People forget how slow disks were even ten years ago.",
    "There is a lot of nuance in how retries interact.",
    "I want to pause on that idea of backpressure.",
    "The numbers they published still surprise me.",
    "Anyway, that is the backdrop for this interview.",
    "We will come back to the open source angle later.",
    "The second half of the show is a bit more personal.",
]


def _content_text(index: int) -> str:
    return _CONTENT[index % len(_CONTENT)]


def _segments_for_duration(
    duration_seconds: int | float, seg_len: int | float = 5.0
) -> list[ScoutSegment]:
    n = round(duration_seconds / seg_len)
    segments: list[ScoutSegment] = []
    for i in range(n):
        start = i * seg_len
        end = start + seg_len
        segments.append(
            ScoutSegment(
                sequence_num=i,
                start_time=start,
                end_time=end,
                text=_content_text(i),
            )
        )
    return segments


def _overlay(
    segments: list[ScoutSegment], start: int | float, texts: list[str]
) -> None:
    """Replace consecutive segments starting at `start` seconds with `texts`."""
    if not segments:
        return
    seg_len = segments[0].end_time - segments[0].start_time
    start_idx = round(start / seg_len)
    for offset, text in enumerate(texts):
        idx = start_idx + offset
        if 0 <= idx < len(segments):
            seg = segments[idx]
            segments[idx] = ScoutSegment(
                sequence_num=seg.sequence_num,
                start_time=seg.start_time,
                end_time=seg.end_time,
                text=text,
            )


def classic_host_reads() -> EpisodeFixture:
    """45-minute interview with classic pre/mid/post-roll host reads."""
    segments = _segments_for_duration(45 * 60)
    preroll = [
        "This episode is brought to you by Squarespace.",
        "If you need a website, Squarespace makes it simple.",
        "Just visit squarespace.com/podcast and get started.",
        "Use code PODCAST for ten percent off your first year.",
        "That's squarespace.com slash podcast.",
        "And we are back with a great conversation today.",
    ]
    midroll = [
        "We'll be right back after a short break.",
        "Our sponsor today is BetterHelp.",
        "Therapy can help you work through stress.",
        "Go to betterhelp.com/example to get started.",
        "You can sign up in a few minutes from your phone.",
        "They match you with a licensed therapist.",
        "Again that's betterhelp.com/example.",
        "And now back to the conversation with our guest.",
    ]
    postroll = [
        "Before we go, a word from our friends at Quip.",
        "Call 800-555-0199 to hear more about the plan.",
        "Mention discount HOMESHOW when you call this week.",
        "Thanks for listening, we will see you next time.",
    ]
    _overlay(segments, 0.0, preroll)
    _overlay(segments, 20 * 60, midroll)
    _overlay(segments, 44 * 60, postroll)
    return EpisodeFixture(
        fixture_id="classic_host_reads",
        title="Classic pre/mid/post-roll host reads",
        podcast_title="The Example Show",
        podcast_topic="technology interviews",
        duration_seconds=45 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(0.0, 30.0, "preroll", "Squarespace URL + promo code"),
            LabeledAd(1200.0, 1240.0, "midroll", "BetterHelp URL + CTA"),
            LabeledAd(2640.0, 2655.0, "postroll", "phone + discount"),
        ],
        notes=(
            "Production CueDetector hits URL/CTA/promo/phone plus mid-roll "
            "transition. Opening 'brought to you by' needs scout extras."
        ),
    )


def cue_sparse_storytelling() -> EpisodeFixture:
    """20-minute episode whose only ad is a cue-sparse brand read."""
    segments = _segments_for_duration(20 * 60)
    ad = [
        "This chapter of the show is a story from a company that makes luggage.",
        "They built the first bag the host took on a three week trip.",
        "The zipper never failed and the shell survived the carousel.",
        "The host still uses that same bag years later.",
        "Friends keep asking where it came from.",
        "The company is Away, and the host likes their carry on.",
        "That is the story. Here is the rest of the interview.",
    ]
    _overlay(segments, 8 * 60, ad)
    return EpisodeFixture(
        fixture_id="cue_sparse_storytelling",
        title="Cue-sparse host-read with no URL/CTA/phone",
        podcast_title="Travel Years",
        podcast_topic="long-form travel interviews",
        duration_seconds=20 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(
                480.0, 515.0, "midroll", "Away brand read, no CueDetector tokens"
            ),
        ],
        notes="Expected scout miss even with extras: no sponsor/CTA/URL/phone.",
    )


def false_positive_content() -> EpisodeFixture:
    """Technical discussion that trips CTA/URL/self-promo regexes."""
    segments = _segments_for_duration(12 * 60)
    traps = [
        "Shopify.com has supported this show in the past as an example.",
        "You should check out this paper on garbage collection.",
        "Engineers can visit the conference site for the slides.",
        "I write in my newsletter about typed languages sometimes.",
        "People sign up for the waitlist of the language itself.",
        "There is a great deal of nuance in how the runtime works.",
    ]
    _overlay(segments, 5 * 60, traps)
    # One real ad so precision is measurable, not just FPs on an ad-free show.
    real = [
        "Go to liner.com/show and use code LINER20.",
        "That is liner.com/show.",
    ]
    _overlay(segments, 0.0, real)
    return EpisodeFixture(
        fixture_id="false_positive_content",
        title="Technical mentions that look like cues",
        podcast_title="Runtime Radio",
        podcast_topic="programming languages",
        duration_seconds=12 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(0.0, 10.0, "preroll", "real Liner ad"),
        ],
        notes="Shopify.com, check out, visit, my newsletter, sign up, deal.",
    )


def self_promo_vs_sponsor() -> EpisodeFixture:
    segments = _segments_for_duration(15 * 60)
    self_promo = [
        "If you like the show, my newsletter has the notes.",
        "Our patreon has the extended interview this week.",
        "I talk about our course for new managers in the extras.",
    ]
    sponsor = [
        "We'll be right back.",
        "Visit notion.com/podcast and start today with the free trial.",
        "Use promo NOTION for a longer trial window.",
        "Back to the show after this.",
    ]
    _overlay(segments, 3 * 60, self_promo)
    _overlay(segments, 9 * 60, sponsor)
    return EpisodeFixture(
        fixture_id="self_promo_vs_sponsor",
        title="First-party promo versus external sponsor",
        podcast_title="Manager Minutes",
        podcast_topic="engineering management",
        duration_seconds=15 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(540.0, 560.0, "midroll", "Notion external sponsor"),
        ],
        notes="Self-promo should not be flagged at default scout config.",
    )


def short_preroll_only() -> EpisodeFixture:
    segments = _segments_for_duration(8 * 60)
    preroll = [
        "Go to hellofresh.com/pod and use code FRESH.",
        "Thanks, and on with the show.",
    ]
    _overlay(segments, 0.0, preroll)
    return EpisodeFixture(
        fixture_id="short_preroll_only",
        title="Short episode, single pre-roll",
        podcast_title="Kitchen Table",
        podcast_topic="home cooking",
        duration_seconds=8 * 60,
        segments=segments,
        labeled_ads=[LabeledAd(0.0, 10.0, "preroll", "HelloFresh")],
        notes="Small episode to show full-walk overhead vs one Gemini window.",
    )


def wildcard_midroll_example() -> EpisodeFixture:
    """The one-shot example from prompt.py, surrounded by content."""
    segments = _segments_for_duration(6 * 60)
    example = [
        "That's all coming after the break.",
        "On this week's episode of Wildcard, actor Chris Pine tells us, it's okay not to be perfect.",
        "My film got absolutely decimated when it premiered, which brings up for me one of my primary triggers or whatever it was like, not being liked.",
        "I'm Rachel Martin, Chris Pine on How to Find Joy in Imperfection.",
        "That's on the new podcast, Wildcard.",
        "The Game Where Cards control the conversation.",
        "And welcome back to the show, today we're talking to Professor Hopkins",
    ]
    _overlay(segments, 50.0, example)
    return EpisodeFixture(
        fixture_id="wildcard_midroll_example",
        title="prompt.py Wildcard cross-promo example",
        podcast_title="Interview Hour",
        podcast_topic="culture interviews",
        duration_seconds=6 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(55.0, 85.0, "midroll", "Wildcard promo from system prompt"),
        ],
        notes="Transition 'after the break' is the only production cue; body has none.",
    )


def all_fixtures() -> list[EpisodeFixture]:
    return [
        classic_host_reads(),
        cue_sparse_storytelling(),
        false_positive_content(),
        self_promo_vs_sponsor(),
        short_preroll_only(),
        wildcard_midroll_example(),
    ]


def fixture_to_json(episode: EpisodeFixture) -> dict[str, object]:
    return {
        "id": episode.fixture_id,
        "title": episode.title,
        "podcast_title": episode.podcast_title,
        "podcast_topic": episode.podcast_topic,
        "duration_seconds": episode.duration_seconds,
        "notes": episode.notes,
        "labeled_ads": [
            {
                "start": ad.start,
                "end": ad.end,
                "kind": ad.kind,
                "notes": ad.notes,
            }
            for ad in episode.labeled_ads
        ],
        "segments": [
            {
                "sequence_num": seg.sequence_num,
                "start_time": seg.start_time,
                "end_time": seg.end_time,
                "text": seg.text,
            }
            for seg in episode.segments
        ],
    }


def episode_from_json(payload: dict[str, object]) -> EpisodeFixture:
    raw_segments = payload["segments"]
    if not isinstance(raw_segments, list):
        raise TypeError("segments must be a list")
    segments: list[ScoutSegment] = []
    for raw_row in raw_segments:
        if not isinstance(raw_row, dict):
            raise TypeError("segment rows must be objects")
        row = cast(dict[str, Any], raw_row)
        segments.append(
            ScoutSegment(
                sequence_num=_as_int(row["sequence_num"]),
                start_time=_as_float(row["start_time"]),
                end_time=_as_float(row["end_time"]),
                text=str(row["text"]),
            )
        )
    raw_ads = payload["labeled_ads"]
    if not isinstance(raw_ads, list):
        raise TypeError("labeled_ads must be a list")
    ads: list[LabeledAd] = []
    for raw_row in raw_ads:
        if not isinstance(raw_row, dict):
            raise TypeError("labeled_ads rows must be objects")
        row = cast(dict[str, Any], raw_row)
        ads.append(
            LabeledAd(
                start=_as_float(row["start"]),
                end=_as_float(row["end"]),
                kind=str(row.get("kind", "unknown")),
                notes=str(row.get("notes", "")),
            )
        )
    return EpisodeFixture(
        fixture_id=str(payload["id"]),
        title=str(payload["title"]),
        podcast_title=str(payload.get("podcast_title", payload["title"])),
        podcast_topic=str(payload.get("podcast_topic", "")),
        duration_seconds=_as_float(payload["duration_seconds"]),
        segments=segments,
        labeled_ads=ads,
        notes=str(payload.get("notes", "")),
    )


def write_fixture_json(episode: EpisodeFixture, path: Path | None = None) -> Path:
    target = path or (DATA_DIR / f"{episode.fixture_id}.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(fixture_to_json(episode), indent=2) + "\n", encoding="utf-8"
    )
    return target
