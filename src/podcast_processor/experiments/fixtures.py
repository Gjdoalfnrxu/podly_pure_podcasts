"""Synthetic labeled transcripts for offline scout eval.

Existing tests/data only has an unlabeled MP3 (count_0_99.mp3). Production
fixtures do not include ad labels, so these transcripts encode known ad spans
plus the false-positive / false-negative cases CueDetector is likely to hit.

The frozen golden set lives under corpus/v1/*.json (MANIFEST hashes). Eval
loads that corpus; Python builders here are the generator used by
`--write-corpus`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, cast

from podcast_processor.experiments.types import EpisodeFixture, LabeledAd, ScoutSegment

DATA_DIR = Path(__file__).resolve().parent / "data"
CORPUS_VERSION = "v1"
CORPUS_ROOT = Path(__file__).resolve().parent / "corpus"


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


def ad_free_interview() -> EpisodeFixture:
    """Ten-minute conversation with no ads (false-positive / empty-label check)."""
    segments = _segments_for_duration(10 * 60)
    return EpisodeFixture(
        fixture_id="ad_free_interview",
        title="Ad-free interview (no sponsor reads)",
        podcast_title="Clean Room",
        podcast_topic="systems interviews",
        duration_seconds=10 * 60,
        segments=segments,
        labeled_ads=[],
        notes="Content lines avoid CueDetector core + scout-extra patterns.",
    )


def stacked_midrolls() -> EpisodeFixture:
    """Two adjacent mid-rolls that should merge under recommended padding/gap."""
    segments = _segments_for_duration(18 * 60)
    first = [
        "We'll be right back after a short break.",
        "Visit stripe.com/atlas and use code ATLAS20.",
        "That's stripe.com/atlas.",
    ]
    second = [
        "Our sponsor today is Linear.",
        "Go to linear.app/podcast and start now.",
        "Use promo LINEAR for the extended trial.",
        "Now back to the conversation.",
    ]
    _overlay(segments, 8 * 60, first)
    # 5s of content between the two blocks (one 5s segment) — merge_gap is 8s
    # and pad_seconds is 15s, so recommended scout should stitch one window.
    _overlay(segments, 8 * 60 + 20.0, second)
    return EpisodeFixture(
        fixture_id="stacked_midrolls",
        title="Stacked adjacent mid-roll sponsors",
        podcast_title="Ship It",
        podcast_topic="product engineering",
        duration_seconds=18 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(480.0, 495.0, "midroll", "Stripe URL + promo"),
            LabeledAd(500.0, 520.0, "midroll", "Linear URL + CTA + promo"),
        ],
        notes="Two labeled ads 5s apart; scout padding/merge should cover both.",
    )


def chapter_style_ad_break() -> EpisodeFixture:
    """Sponsor language that production CueDetector misses without scout extras."""
    segments = _segments_for_duration(9 * 60)
    ad = [
        "And now a short advertisement.",
        "After these messages we continue the interview.",
        "This episode is sponsored by Athletic Greens.",
        "The host has used their daily drink on long flights for years.",
        "Thanks for listening through that.",
    ]
    _overlay(segments, 4 * 60, ad)
    return EpisodeFixture(
        fixture_id="chapter_style_ad_break",
        title="Chapter-style ad break without URL/CTA/phone",
        podcast_title="Long Haul",
        podcast_topic="travel and health",
        duration_seconds=9 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(
                240.0,
                265.0,
                "midroll",
                "advertisement / after these messages / sponsored by",
            ),
        ],
        notes=(
            "Needs scout extras (ad_break + sponsor). Production CueDetector "
            "has none of URL/CTA/promo/phone/transition in the ad body."
        ),
    )


def soft_skills_style_interview() -> EpisodeFixture:
    """Workplace-interview golden: labeled `use code` + unlabeled `code <word>`.

    Extends the H004 `example_soft_skills_style_payload` skeleton into a
    12-minute synthetic episode. Not a real Soft Skills transcript.
    """
    segments = _segments_for_duration(12 * 60)
    preroll = [
        "Welcome back to the workplace interview.",
        "Go to examplesponsor.com/show and use code SOFT20.",
        "That is examplesponsor.com/show for ten percent off.",
    ]
    tech_speech = [
        "The guest described a one-on-one that went sideways.",
        "During the code review the team found a race.",
        "The code path for retries is too hot in production.",
        "We walked through a code sample on the whiteboard.",
        "I want to pause on that idea of backpressure.",
    ]
    _overlay(segments, 0.0, preroll)
    _overlay(segments, 6 * 60, tech_speech)
    return EpisodeFixture(
        fixture_id="soft_skills_style_interview",
        title="Synthetic interview-style golden (tech-speech FPs)",
        podcast_title="Example Workplace Show",
        podcast_topic="management interviews",
        duration_seconds=12 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(
                5.0,
                15.0,
                "preroll",
                "synthetic sponsor; use code SOFT20",
            )
        ],
        notes=(
            "Soft Skills-style synthetic interview from H004 templates. "
            "Labeled preroll uses `use code SOFT20`. Unlabeled mid-episode "
            "`code review` / `code path` / `code sample` are tech-speech "
            "promo FPs for TightPromo vs production CueDetector. Not a "
            "real show; not copyrighted episode text."
        ),
    )


def news_briefing_style_code_cta() -> EpisodeFixture:
    """News-briefing golden: unlabeled `code <word>` beside labeled `use code SAVE`.

    Extends the H004 `example_the_daily_style_payload` skeleton. Not The Daily.
    """
    segments = _segments_for_duration(10 * 60)
    intro = [
        "From the example newsroom, this is the morning briefing.",
        "Today the team walks through a public policy hearing.",
    ]
    tech_speech = [
        "Reporters described a code review of the leaked draft.",
        "The code path in the filing system was the bottleneck.",
    ]
    sponsor = [
        "This episode is sponsored by Example Bank.",
        "Visit examplebank.com/brief and use code SAVE50.",
        "Promo SAVE50 works through Friday.",
    ]
    _overlay(segments, 0.0, intro)
    _overlay(segments, 3 * 60, tech_speech)
    _overlay(segments, 7 * 60, sponsor)
    return EpisodeFixture(
        fixture_id="news_briefing_style_code_cta",
        title="Synthetic news-briefing golden (code speech vs SAVE CTA)",
        podcast_title="Example Morning Briefing",
        podcast_topic="news",
        duration_seconds=10 * 60,
        segments=segments,
        labeled_ads=[
            LabeledAd(
                420.0,
                435.0,
                "midroll",
                "synthetic sponsor; use code SAVE50",
            )
        ],
        notes=(
            "News-briefing-style synthetic from H004 templates. Unlabeled "
            "`code review` / `code path` sit beside a labeled `use code "
            "SAVE50` midroll so TightPromo can be scored offline. Not NYT "
            "text; not a real show."
        ),
    )


def style_golden_fixtures(names: list[str] | None = None) -> list[EpisodeFixture]:
    """Soft Skills / news-briefing style goldens used by H005/H006.

    Not in builder_fixtures() until an explicit corpus promotion.
    """
    rows = [
        soft_skills_style_interview(),
        news_briefing_style_code_cta(),
    ]
    if names:
        wanted = set(names)
        selected = [episode for episode in rows if episode.fixture_id in wanted]
        missing = wanted - {episode.fixture_id for episode in selected}
        if missing:
            raise KeyError(f"unknown style golden fixture ids: {sorted(missing)}")
        return selected
    return rows


def builder_fixtures() -> list[EpisodeFixture]:
    """Python generators for the golden corpus (used by --write-corpus)."""
    return [
        classic_host_reads(),
        cue_sparse_storytelling(),
        false_positive_content(),
        self_promo_vs_sponsor(),
        short_preroll_only(),
        wildcard_midroll_example(),
        ad_free_interview(),
        stacked_midrolls(),
        chapter_style_ad_break(),
        # H005 2026-10-01: Soft Skills-style synthetic (not copyrighted
        # episode text). TightPromo vs recommended dropped 2→1 scout
        # windows on unlabeled `code <word>` with labeled-ad recall 1.0.
        soft_skills_style_interview(),
        # H006 2026-10-01: news-briefing-style synthetic (not The Daily /
        # not NYT text). TightPromo vs recommended dropped 2→1 scout
        # windows on unlabeled `code review` / `code path` with labeled
        # `use code SAVE50` recall 1.0.
        news_briefing_style_code_cta(),
    ]


def corpus_dir(version: str = CORPUS_VERSION) -> Path:
    return CORPUS_ROOT / version


def corpus_manifest_path(version: str = CORPUS_VERSION) -> Path:
    return corpus_dir(version) / "MANIFEST.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def write_corpus(
    version: str = CORPUS_VERSION,
    episodes: list[EpisodeFixture] | None = None,
) -> Path:
    """Dump builder fixtures to versioned JSON + MANIFEST hashes."""
    rows = episodes or builder_fixtures()
    target = corpus_dir(version)
    target.mkdir(parents=True, exist_ok=True)
    manifest_fixtures: list[dict[str, Any]] = []
    for episode in rows:
        path = write_fixture_json(episode, target / f"{episode.fixture_id}.json")
        manifest_fixtures.append(
            {
                "id": episode.fixture_id,
                "sha256": sha256_file(path),
                "n_segments": len(episode.segments),
                "n_labeled_ads": len(episode.labeled_ads),
                "duration_seconds": episode.duration_seconds,
                "labeled_ads": [
                    {
                        "start": ad.start,
                        "end": ad.end,
                        "kind": ad.kind,
                        "notes": ad.notes,
                    }
                    for ad in episode.labeled_ads
                ],
            }
        )
    manifest = {
        "version": version,
        "corpus_id": "bow_scout_gemini_confirm",
        "n_fixtures": len(manifest_fixtures),
        "fixtures": manifest_fixtures,
    }
    corpus_manifest_path(version).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return corpus_manifest_path(version)


def load_corpus(version: str = CORPUS_VERSION) -> list[EpisodeFixture]:
    """Load the frozen golden set; fail if a fixture hash drifts."""
    manifest_path = corpus_manifest_path(version)
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"Golden corpus manifest missing: {manifest_path}. "
            "Run scripts/experiments/run_bow_scout_eval.py --write-corpus"
        )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    raw_list = payload.get("fixtures")
    if not isinstance(raw_list, list):
        raise TypeError("corpus MANIFEST fixtures must be a list")
    episodes: list[EpisodeFixture] = []
    for raw_item in raw_list:
        if not isinstance(raw_item, dict):
            raise TypeError("corpus MANIFEST fixture rows must be objects")
        item = cast(dict[str, Any], raw_item)
        fixture_id = str(item["id"])
        path = corpus_dir(version) / f"{fixture_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Golden corpus fixture missing: {path}")
        expected = str(item["sha256"])
        actual = sha256_file(path)
        if actual != expected:
            raise ValueError(
                f"Golden corpus hash mismatch for {fixture_id}: "
                f"manifest={expected} file={actual}. "
                "Regenerate with --write-corpus only if the change is intentional."
            )
        episodes.append(episode_from_json(json.loads(path.read_text(encoding="utf-8"))))
    return episodes


def all_fixtures() -> list[EpisodeFixture]:
    """Eval corpus: frozen JSON when present, else in-memory builders."""
    if corpus_manifest_path().exists():
        return load_corpus()
    return builder_fixtures()


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
