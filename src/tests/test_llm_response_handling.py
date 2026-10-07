"""LLM answers that are empty (thinking ate the budget) or corrupt JSON.

Live evidence (podly, qwen3.8-27b on vLLM, 7 Oct 2026): ModelCalls whose
content was None after ~100 s were stored failed_permanent with an empty error
(bare `assert`), and corrupt answers such as '{"{"ad_segments":[],...}' were
stored as success and silently treated as "no ads".
"""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

import litellm
import pytest
from flask import Flask

from app.config_store import _parse_json_object
from app.extensions import db
from app.models import ModelCall, Post, TranscriptSegment
from podcast_processor.ad_classifier import (
    UNPARSEABLE_RESPONSE_ATTEMPTS,
    AdClassifier,
    LLMEmptyResponseError,
)
from podcast_processor.model_output import clean_and_parse_model_output
from shared.config import Config

NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}
# Verbatim corrupt answers from the live model_call table.
LIVE_CORRUPT = [
    '{"{"ad_segments":[],"content_type":"technical_discussion","confidence":0.95}',
    '{"{"}{"\n  :[""]\n}',
    '{"{"ad_segments":[[]]}',
]


class _Stub:
    """OpenAI-compatible chat endpoint that records requests and replays answers."""

    def __init__(self, answers: list[dict[str, Any]]) -> None:
        self.answers = answers
        self.requests: list[dict[str, Any]] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                stub.requests.append(json.loads(body))
                answer = stub.answers[min(len(stub.requests), len(stub.answers)) - 1]
                payload = json.dumps(
                    {
                        "id": "x",
                        "object": "chat.completion",
                        "created": 0,
                        "model": "stub",
                        "choices": [{"index": 0, **answer}],
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def _answer(content: str | None, finish: str = "stop", reasoning: str | None = None):
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning_content"] = reasoning
    return {"message": message, "finish_reason": finish}


@pytest.fixture
def stub_llm(test_config: Config) -> Generator[Any, None, None]:
    stubs: list[_Stub] = []

    def make(answers: list[dict[str, Any]], extra_body: dict | None = None):
        stub = _Stub(answers)
        stubs.append(stub)
        test_config.llm_model = "openai/stub-model"
        test_config.llm_extra_body = extra_body
        test_config.llm_max_retry_attempts = 1
        litellm.api_base = stub.url
        litellm.api_key = "stub-not-a-key"
        return stub

    saved = (litellm.api_base, litellm.api_key)
    yield make
    litellm.api_base, litellm.api_key = saved
    for stub in stubs:
        stub.close()


def _model_call(app: Flask, prompt: str = "transcript") -> ModelCall:
    post = Post(feed_id=1, guid="g1", download_url="u", title="T")
    db.session.add(post)
    db.session.commit()
    call = ModelCall(
        post_id=post.id,
        model_name="openai/stub-model",
        prompt=prompt,
        first_segment_sequence_num=0,
        last_segment_sequence_num=1,
        status="pending",
    )
    db.session.add(call)
    db.session.commit()
    return call


# ------------------------------------------------------------- request body


def test_extra_body_reaches_the_server(app, test_config, stub_llm):
    stub = stub_llm([_answer('{"ad_segments": []}')], extra_body=NO_THINK)
    with app.app_context():
        call = _model_call(app)
        AdClassifier(config=test_config)._call_model(
            model_call_obj=call, system_prompt="sys"
        )
    sent = stub.requests[0]
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}
    assert sent["response_format"] == {"type": "json_object"}


def test_no_extra_body_by_default(app, test_config, stub_llm):
    stub = stub_llm([_answer('{"ad_segments": []}')])
    with app.app_context():
        AdClassifier(config=test_config)._call_model(
            model_call_obj=_model_call(app), system_prompt="sys"
        )
    assert "chat_template_kwargs" not in stub.requests[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"chat_template_kwargs": {"enable_thinking": false}}', NO_THINK),
        ("not json", None),
        ("[1, 2]", None),
        ("", None),
        (None, None),
    ],
)
def test_llm_extra_body_env_parsing(raw, expected):
    assert _parse_json_object(raw, env_name="LLM_EXTRA_BODY") == expected


# ------------------------------------------------------------- empty content


def test_empty_content_is_explained_and_left_retryable(app, test_config, stub_llm):
    stub_llm([_answer(None, finish="length", reasoning="Okay, let me think... " * 50)])
    with app.app_context():
        call = _model_call(app)
        with pytest.raises(LLMEmptyResponseError):
            AdClassifier(config=test_config)._call_model(
                model_call_obj=call, system_prompt="sys"
            )
        db.session.expire_all()
        stored = db.session.get(ModelCall, call.id)
        assert stored is not None
        # Live rows had status failed_permanent and error_message "".
        assert stored.status == "failed"
        assert "finish_reason=length" in (stored.error_message or "")
        assert "enable_thinking" in (stored.error_message or "")


# ------------------------------------------------------------- corrupt JSON


@pytest.mark.parametrize("raw", LIVE_CORRUPT)
def test_live_corrupt_answers_do_not_parse(raw):
    with pytest.raises(Exception):  # noqa: B017 - pydantic or assertion error
        clean_and_parse_model_output(raw)


def test_think_block_with_braces_is_ignored():
    raw = '<think>maybe {"ad_segments": [{"x"}]}?</think>\n{"ad_segments": []}'
    assert clean_and_parse_model_output(raw).ad_segments == []


def _segments() -> list[TranscriptSegment]:
    segments = [
        TranscriptSegment(
            post_id=1, sequence_num=i, start_time=float(i), end_time=i + 1.0, text="t"
        )
        for i in range(2)
    ]
    db.session.add_all(segments)
    db.session.commit()
    return segments


def _chunk(classifier: AdClassifier, call: ModelCall, segments) -> Any:
    with mock.patch.object(classifier, "_get_or_create_model_call", return_value=call):
        return classifier._process_chunk(
            chunk_segments=segments,
            system_prompt="sys",
            post=db.session.get(Post, call.post_id),
            user_prompt_str="prompt",
        )


def test_corrupt_answer_is_reasked_then_used(app, test_config, stub_llm):
    stub = stub_llm(
        [
            _answer(LIVE_CORRUPT[0]),
            _answer('{"ad_segments": [{"segment_offset": 0.0, "confidence": 0.9}]}'),
        ]
    )
    with app.app_context():
        call = _model_call(app)
        segments = _segments()
        classifier = AdClassifier(config=test_config)
        matched = _chunk(classifier, call, segments)

        assert len(stub.requests) == 2
        assert [s.sequence_num for s in matched] == [0]
        assert classifier.unclassified_ranges == []
        db.session.expire_all()
        stored = db.session.get(ModelCall, call.id)
        assert stored is not None
        assert stored.status == "success"


def test_persistently_corrupt_answer_is_reported_not_silent(app, test_config, stub_llm):
    stub = stub_llm([_answer(LIVE_CORRUPT[1])])
    with app.app_context():
        call = _model_call(app)
        classifier = AdClassifier(config=test_config)
        assert _chunk(classifier, call, _segments()) == []

        assert len(stub.requests) == UNPARSEABLE_RESPONSE_ATTEMPTS
        assert classifier.unclassified_ranges == [(0, 1)]
        db.session.expire_all()
        stored = db.session.get(ModelCall, call.id)
        assert stored is not None
        # Not "success": a later run asks again instead of reusing garbage.
        assert stored.status == "failed"
        assert (stored.error_message or "").startswith("Unparseable LLM response")


def test_stored_corrupt_success_from_before_the_fix_is_reasked(
    app, test_config, stub_llm
):
    """Rows already in the live DB as status=success with corrupt JSON."""
    stub = stub_llm([_answer('{"ad_segments": []}')])
    with app.app_context():
        call = _model_call(app)
        call.status = "success"
        call.response = LIVE_CORRUPT[2]
        db.session.commit()
        classifier = AdClassifier(config=test_config)
        assert _chunk(classifier, call, _segments()) == []
        assert len(stub.requests) == 1
        assert classifier.unclassified_ranges == []
