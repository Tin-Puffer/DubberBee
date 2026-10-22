"""Tests for Groq translator parsing/retry and AutoTranslator gap-filling."""
from __future__ import annotations

from typing import List
from unittest.mock import patch

import pytest

from dubber.core.translate import groq_translator as gt
from dubber.core.translate.base import TranslateResult, TranslateSegment
from dubber.core.translate.groq_translator import GroqTranslator, _parse_response
from dubber.core.translate.local_translator import AutoTranslator


def _segs(n: int) -> List[TranslateSegment]:
    return [TranslateSegment(id=i, zh=f"句子{i}", max_chars=40) for i in range(n)]


# ── _parse_response ───────────────────────────────────────────────────────────

def test_parse_plain_array():
    assert _parse_response('[{"id": 0, "vi": "Xin chào"}]') == {0: "Xin chào"}


def test_parse_strips_think_block():
    raw = '<think>Let me translate...</think>\n[{"id": 1, "vi": "Chào"}]'
    assert _parse_response(raw) == {1: "Chào"}


def test_parse_extracts_array_from_prose():
    raw = 'Here is the translation:\n[{"id": 2, "vi": "Tạm biệt"}]\nHope this helps.'
    assert _parse_response(raw) == {2: "Tạm biệt"}


def test_parse_no_array_raises():
    with pytest.raises(ValueError):
        _parse_response("Sorry, I can't help with that.")


def test_parse_empty_raises():
    with pytest.raises(ValueError):
        _parse_response("")


# ── retry behaviour ───────────────────────────────────────────────────────────

class _RateLimit(Exception):
    status_code = 429


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(gt.time, "sleep", lambda _s: None)


def test_parse_error_switches_model():
    t = GroqTranslator(api_key="test")
    calls = []

    def fake_call(model, prompt):
        calls.append(model)
        return "not json" if model == gt.DEFAULT_MODEL else '[{"id": 0, "vi": "OK"}]'

    with patch.object(t, "_call_model", side_effect=fake_call):
        res = t.translate(_segs(1))
    assert res[0].vi == "OK"
    assert calls == [gt.DEFAULT_MODEL, gt.FALLBACK_MODEL]


def test_both_rate_limited_waits_and_retries():
    t = GroqTranslator(api_key="test")
    responses = [_RateLimit("429"), _RateLimit("429"), '[{"id": 0, "vi": "OK"}]']

    def fake_call(model, prompt):
        r = responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    with patch.object(t, "_call_model", side_effect=fake_call):
        res = t.translate(_segs(1))
    assert res[0].vi == "OK"


# ── AutoTranslator gap-filling ────────────────────────────────────────────────

class _FakeGroq:
    def __init__(self, vi_by_id):
        self._vi = vi_by_id

    def translate(self, segments):
        return [TranslateResult(id=s.id, vi=self._vi.get(s.id, "")) for s in segments]


class _FakeLocal:
    def __init__(self):
        self.seen = []

    def translate(self, segments):
        self.seen.extend(s.id for s in segments)
        return [TranslateResult(id=s.id, vi=f"local{s.id}") for s in segments]


def _auto(groq, local):
    a = AutoTranslator.__new__(AutoTranslator)
    a._groq, a._groq_failed = groq, False
    a._local, a._local_available = local, local is not None
    return a


def test_auto_fills_missing_with_local():
    local = _FakeLocal()
    res = _auto(_FakeGroq({0: "groq0", 2: "groq2"}), local).translate(_segs(3))
    assert [r.vi for r in res] == ["groq0", "local1", "groq2"]
    assert local.seen == [1]


def test_auto_all_missing_without_local_raises():
    with pytest.raises(RuntimeError):
        _auto(_FakeGroq({}), None).translate(_segs(2))


def test_auto_partial_missing_without_local_keeps_results():
    res = _auto(_FakeGroq({0: "groq0"}), None).translate(_segs(2))
    assert [r.vi for r in res] == ["groq0", ""]


# ── rate limit → ask the user (GUI) ───────────────────────────────────────────

class _Resp:
    def __init__(self, headers):
        self.headers = headers


def _limited(headers=None):
    e = _RateLimit("rate_limit_exceeded")
    e.response = _Resp(headers or {})
    return e


def _script(t, outcomes):
    """Each API call pops the next outcome: an exception to raise, or 'ok'."""
    calls = []

    def fake_call(model, prompt):
        calls.append(model)
        o = outcomes.pop(0) if outcomes else "ok"
        if isinstance(o, Exception):
            raise o
        import json, re
        ids = [int(x) for x in re.findall(r'"id": (\d+), "zh"', prompt)]
        return json.dumps([{"id": i, "vi": f"vi{i}"} for i in ids])
    t._call_model = fake_call
    return calls


def test_retry_after_header_parsing():
    assert gt._parse_duration("2m59.5s") == pytest.approx(179.5)
    assert gt._parse_duration("120ms") == pytest.approx(0.12)
    assert gt._retry_after(_limited({"retry-after": "7"})) == 7
    assert gt._retry_after(_limited({"x-ratelimit-reset-tokens": "9.5s",
                                     "x-ratelimit-reset-requests": "2s"})) == 9.5
    assert gt._retry_after(_limited({"retry-after": "900"})) == gt.MAX_WAIT
    assert gt._retry_after(_limited()) == gt.RATE_LIMIT_WAIT


def test_rate_limit_asks_once_then_continues(monkeypatch):
    t = GroqTranslator(api_key="test")
    waits = []
    monkeypatch.setattr(t, "_sleep", waits.append)
    asked = []
    t.on_rate_limit = lambda info: asked.append(info) or "continue"
    _script(t, [_limited({"retry-after": "12"}), _limited({"retry-after": "12"})])  # both models
    out = t.translate(_segs(3))
    assert [r.vi for r in out] == ["vi0", "vi1", "vi2"]
    assert len(asked) == 1 and asked[0]["wait"] == 12 and asked[0]["again"] is False
    assert asked[0]["total"] == 3 and asked[0]["done"] == 0
    assert waits == [12]


def test_rate_limit_asks_again_after_four_more_failures(monkeypatch):
    t = GroqTranslator(api_key="test")
    monkeypatch.setattr(t, "_sleep", lambda s: None)
    asked = []
    t.on_rate_limit = lambda info: asked.append(info["again"]) or "continue"
    # first episode: primary + fallback limited → ask; then 4 more limited rounds
    # (each round = primary then fallback) → ask again
    _script(t, [_limited()] * (2 + 2 * gt.ASK_AGAIN_AFTER))
    t.translate(_segs(2))
    assert asked == [False, True]


def test_rate_limit_switch_to_marian_keeps_groq_lines(monkeypatch):
    import dubber.core.translate.local_translator as lt
    t = GroqTranslator(api_key="test", batch_size=2)
    monkeypatch.setattr(t, "_sleep", lambda s: None)
    monkeypatch.setattr(gt, "_local_available", lambda: True)
    t.on_rate_limit = lambda info: "local"

    class FakeMarian:
        cancel_check = None
        def translate(self, segs):
            return [TranslateResult(id=s.id, vi=f"marian{s.id}") for s in segs]
    monkeypatch.setattr(lt, "LocalTranslator", FakeMarian)
    _script(t, ["ok", _limited(), _limited()])       # batch 1 ok, batch 2 limited
    out = t.translate(_segs(4))
    assert [r.vi for r in out] == ["vi0", "vi1", "marian2", "marian3"]


def test_cli_rate_limit_still_gives_up():
    t = GroqTranslator(api_key="test")
    calls = _script(t, [_limited()] * 100)
    out = t.translate(_segs(1))
    assert out[0].vi == "" and len(calls) < 30
