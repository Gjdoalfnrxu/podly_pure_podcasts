"""Fill BASELINE_REPORT.md template sections from a pipeline result."""

from __future__ import annotations

import re
from pathlib import Path

from podcast_processor.experiments.auto_gold.constants import GOLD_FAMILY
from podcast_processor.experiments.auto_gold.types import PipelineResult

BEGIN = "<!-- BEGIN:AUTO_GOLD_{name} -->"
END = "<!-- END:AUTO_GOLD_{name} -->"


def default_template_path() -> Path:
    return (
        Path(__file__).resolve().parents[4]
        / "docs"
        / "experiments"
        / "auto_gold"
        / "BASELINE_REPORT.md"
    )


def fill_section(template: str, name: str, body: str) -> str:
    begin = BEGIN.format(name=name)
    end = END.format(name=name)
    pattern = re.compile(
        re.escape(begin) + r".*?" + re.escape(end),
        re.S,
    )
    replacement = f"{begin}\n{body.rstrip()}\n{end}"
    if not pattern.search(template):
        raise ValueError(f"template missing section markers for {name}")
    return pattern.sub(replacement, template, count=1)


def _status_body(result: PipelineResult) -> str:
    blocked = ", ".join(result.blocked_steps) if result.blocked_steps else "none"
    payload = result.as_dict()
    return "\n".join(
        [
            f"- Gold family (locked): `{result.gold_family}`",
            f"- Sampling unit: `{result.sampling_unit}`",
            f"- Shows in this run: `{len(result.shows)}`",
            f"- Chunks proposed: `{payload['n_chunks']}`",
            f"- Whisper transcribed: `{payload['n_transcribed']}`",
            f"- Judge is_ad: `{payload['n_is_ad']}` / not_ad `{payload['n_not_ad']}` / "
            f"skipped `{payload['n_judge_skipped']}`",
            f"- Whisper backend: `{result.whisper_backend}`",
            f"- Judge mode: `{result.judge_mode}`",
            f"- Gemini spend: `${result.judge_spend_usd:.4f}` "
            f"(cap `${result.judge_budget_usd:.2f}`, calls `{result.judge_calls}`)",
            f"- Gemini/Google key present: `{result.gemini_key_present}`",
            f"- Canonical judge env var: `{result.judge_env_needed}`",
            f"- GROQ_KEY present but unused: `{result.groq_key_present_but_unused}`",
            f"- Production `enable_bow_scout_gemini_confirm`: "
            f"`{result.production_flag_enable_bow_scout_gemini_confirm}` "
            "(must stay false)",
            f"- Blocked steps: {blocked}",
        ]
    )


def _sample_body(result: PipelineResult) -> str:
    lines = [
        "| show_id | genre | episode | RSS | chunks | whispered | is_ad |",
        "| --- | --- | --- | --- | ---: | ---: | ---: |",
    ]
    for row in result.shows:
        ep = row.episode.episode_title if row.episode else ""
        rss = "ok" if row.episode and row.episode.rss_ok else "fail"
        n_wh = sum(1 for t in row.transcripts if not t.skipped and t.text.strip())
        n_ad = sum(1 for lab in row.labels if not lab.skipped and lab.is_ad)
        lines.append(
            f"| `{row.show.show_id}` | {row.show.genre} | "
            f"{ep or '—'} | {rss} | {len(row.candidates)} | {n_wh} | {n_ad} |"
        )
    genres = sorted({row.show.genre for row in result.shows})
    finance = sum(1 for row in result.shows if row.show.genre == "finance")
    lines.append("")
    lines.append(
        f"Genres present: {', '.join(genres) or 'none'}. "
        f"Finance shows: {finance}/{len(result.shows)} "
        "(must not be the whole sample)."
    )
    return "\n".join(lines)


def _candidates_body(result: PipelineResult) -> str:
    counts: dict[str, int] = {}
    for row in result.shows:
        for chunk in row.candidates:
            for source in chunk.sources:
                counts[source] = counts.get(source, 0) + 1
    if not counts:
        return "No candidates in this run (RSS/download likely skipped or failed)."
    lines = ["| source | chunks containing source |", "| --- | ---: |"]
    for key in sorted(counts):
        lines.append(f"| `{key}` | {counts[key]} |")
    lines.append("")
    lines.append(
        "Publisher markers are **positives only**. "
        "Missing chapters never count as ad-free gold."
    )
    return "\n".join(lines)


def _whisper_body(result: PipelineResult) -> str:
    skipped = 0
    done = 0
    chars = 0
    for row in result.shows:
        for item in row.transcripts:
            if item.skipped:
                skipped += 1
            else:
                done += 1
                chars += len(item.text)
    reason = ""
    for row in result.shows:
        for item in row.transcripts:
            if item.skip_reason:
                reason = item.skip_reason
                break
        if reason:
            break
    lines = [
        f"- Backend: `{result.whisper_backend}`",
        f"- Transcribed chunks: `{done}`",
        f"- Skipped chunks: `{skipped}`",
        f"- Transcript characters: `{chars}`",
        f"- Skip reason (first): {reason or 'n/a'}",
        "- Whisper runs on **candidate chunks only**, never the full episode.",
        "",
        "| show | start-end | sources | chars | excerpt |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for row in result.shows:
        for item in row.transcripts:
            excerpt = (item.text or "").replace("|", "/").replace("\n", " ")
            if len(excerpt) > 160:
                excerpt = excerpt[:157] + "..."
            if item.skipped:
                excerpt = f"_skipped: {item.skip_reason or 'n/a'}_"
            chunk = item.chunk
            lines.append(
                f"| `{row.show.show_id}` | {chunk.start:.0f}-{chunk.end:.0f}s | "
                f"{','.join(chunk.sources)} | {len(item.text)} | {excerpt or '—'} |"
            )
    return "\n".join(lines)


def _judge_body(result: PipelineResult) -> str:
    skipped = 0
    labeled = 0
    ads = 0
    for row in result.shows:
        for lab in row.labels:
            if lab.skipped:
                skipped += 1
            else:
                labeled += 1
                if lab.is_ad:
                    ads += 1
    reason = ""
    for row in result.shows:
        for lab in row.labels:
            if lab.skip_reason:
                reason = lab.skip_reason
                break
        if reason:
            break
    return "\n".join(
        [
            f"- Mode: `{result.judge_mode}`",
            f"- Labeled chunks: `{labeled}` (is_ad={ads})",
            f"- Skipped: `{skipped}`",
            f"- Spend: `${result.judge_spend_usd:.4f}` / cap `${result.judge_budget_usd:.2f}` "
            f"({result.judge_calls} live calls)",
            f"- Canonical env var needed for live judge: `{result.judge_env_needed}` "
            "(aliases: `GEMINI_KEY`, `GOOGLE_API_KEY`, `GOOGLE_GENERATIVE_AI_API_KEY`). "
            "`GROQ_KEY` is never used.",
            f"- Skip reason (first): {reason or 'n/a'}",
        ]
    )


def _spend_body(result: PipelineResult) -> str:
    return "\n".join(
        [
            f"- Live Gemini calls: `{result.judge_calls}`",
            f"- Estimated spend: `${result.judge_spend_usd:.4f}`",
            f"- Budget cap: `${result.judge_budget_usd:.2f}`",
            f"- Key present: `{result.gemini_key_present}`",
            f"- Set `{result.judge_env_needed}` to enable the judge. Do not use `GROQ_KEY`.",
        ]
    )


def _blocked_body(result: PipelineResult) -> str:
    if not result.blocked_steps:
        return "No blocked steps in this run."
    lines = [f"- {item}" for item in result.blocked_steps]
    for note in result.notes:
        lines.append(f"- note: {note}")
    return "\n".join(lines)


def _flags_body(result: PipelineResult) -> str:
    flag = result.production_flag_enable_bow_scout_gemini_confirm
    return "\n".join(
        [
            f"- `enable_bow_scout_gemini_confirm` default: `{flag}` "
            "(must remain false; this harness does not touch Feed/PodcastProcessor).",
            f"- Locked gold family: `{GOLD_FAMILY}`.",
            "- Production `AdClassifier` is not imported by this pipeline.",
        ]
    )


def render_report(template: str, result: PipelineResult) -> str:
    filled = template
    filled = fill_section(filled, "STATUS", _status_body(result))
    filled = fill_section(filled, "SAMPLE", _sample_body(result))
    filled = fill_section(filled, "CANDIDATES", _candidates_body(result))
    filled = fill_section(filled, "WHISPER", _whisper_body(result))
    filled = fill_section(filled, "JUDGE", _judge_body(result))
    filled = fill_section(filled, "SPEND", _spend_body(result))
    filled = fill_section(filled, "BLOCKED", _blocked_body(result))
    filled = fill_section(filled, "PRODUCTION_FLAGS", _flags_body(result))
    return filled


def write_report(
    result: PipelineResult,
    dest: Path,
    template_path: Path | None = None,
) -> Path:
    template = (template_path or default_template_path()).read_text(encoding="utf-8")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_report(template, result), encoding="utf-8")
    return dest
