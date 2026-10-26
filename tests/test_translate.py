"""Tests for M2 translator: parser, batching, fallback, JSON output."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest

from dubber.core.asr.base import ASRSegment
from dubber.core.translate.base import (
    CHARS_PER_SEC,
    TranslateResult,
    TranslateSegment,
    make_segments,
    write_translate_json,
)
from dubber.core.translate.gemini import (
    DEFAULT_MODEL,
    FALLBACK_MODEL,
    GeminiTranslator,
    _build_prompt,
    _is_not_found_error,
    _is_quota_error,
    _parse_response,
    _should_use_fallback,
)


# ── _parse_response ───────────────────────────────────────────────────────────

def test_parse_valid_json():
    raw = '[{"id": 0, "vi": "Xin chào"}]'
    result = _parse_response(raw)
    assert result == {0: "Xin chào"}


def test_parse_strips_json_fence():
    raw = '```json\n[{"id": 1, "vi": "Hôm nay"}]\n```'
    result = _parse_response(raw)
    assert result == {1: "Hôm nay"}


def test_parse_strips_plain_fence():
    raw = '```\n[{"id": 2, "vi": "Thế giới"}]\n```'
    result = _parse_response(raw)
    assert result == {2: "Thế giới"}


def test_parse_multiple_items():
    raw = json.dumps([{"id": 0, "vi": "A"}, {"id": 1, "vi": "B"}, {"id": 2, "vi": "C"}])
    result = _parse_response(raw)
    assert result == {0: "A", 1: "B", 2: "C"}


def test_parse_skips_items_without_id_or_vi():
    raw = json.dumps([
        {"id": 0, "vi": "OK"},
        {"id": 1},             # missing vi
        {"vi": "no id"},       # missing id
        {"id": 2, "vi": "好"},
    ])
    result = _parse_response(raw)
    assert result == {0: "OK", 2: "好"}


def test_parse_invalid_json_raises():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_response("not json at all")


def test_parse_non_array_raises():
    with pytest.raises(ValueError):
        _parse_response('{"id": 0, "vi": "x"}')  # object, not array


def test_parse_empty_array():
    assert _parse_response("[]") == {}


# ── _is_quota_error ───────────────────────────────────────────────────────────

def test_quota_error_code_429():
    exc = Exception("some error")
    exc.code = 429
    assert _is_quota_error(exc)


def test_quota_error_resource_exhausted_status():
    exc = Exception("429 RESOURCE_EXHAUSTED")
    exc.code = 429
    exc.status = "RESOURCE_EXHAUSTED"
    assert _is_quota_error(exc)


def test_quota_error_in_message():
    exc = RuntimeError("HTTP 429: quota exceeded")
    assert _is_quota_error(exc)


def test_quota_error_false_for_other():
    exc = RuntimeError("Internal server error 500")
    assert not _is_quota_error(exc)


# ── _is_not_found_error ───────────────────────────────────────────────────────

def test_not_found_error_code_404():
    exc = Exception("model gone")
    exc.code = 404
    exc.status = "NOT_FOUND"
    assert _is_not_found_error(exc)


def test_not_found_error_message_contains_404():
    exc = RuntimeError("404 NOT_FOUND: This model is no longer available")
    assert _is_not_found_error(exc)


def test_not_found_error_no_longer_available():
    exc = RuntimeError("This model models/gemini-2.0-flash is no longer available.")
    assert _is_not_found_error(exc)


def test_not_found_false_for_quota():
    exc = Exception("RESOURCE_EXHAUSTED")
    exc.code = 429
    assert not _is_not_found_error(exc)


# ── _should_use_fallback ──────────────────────────────────────────────────────

def test_should_use_fallback_on_429():
    exc = Exception("quota")
    exc.code = 429
    assert _should_use_fallback(exc)


def test_should_use_fallback_on_404():
    exc = Exception("not found")
    exc.code = 404
    exc.status = "NOT_FOUND"
    assert _should_use_fallback(exc)


def test_should_not_use_fallback_on_500():
    exc = RuntimeError("Internal server error")
    assert not _should_use_fallback(exc)


# ── _build_prompt ─────────────────────────────────────────────────────────────

def test_build_prompt_no_context():
    segs = [TranslateSegment(id=0, zh="大家好", max_chars=10)]
    prompt = _build_prompt(segs, [])
    assert "大家好" in prompt
    assert "Context" not in prompt


def test_build_prompt_with_context():
    ctx = [{"id": 0, "zh": "大家好", "vi": "Xin chào"}]
    segs = [TranslateSegment(id=1, zh="欢迎", max_chars=10)]
    prompt = _build_prompt(segs, ctx)
    assert "Context" in prompt
    assert "Xin chào" in prompt
    assert "欢迎" in prompt


# ── make_segments ─────────────────────────────────────────────────────────────

def test_make_segments_ids_are_sequential():
    asr = [ASRSegment(0, 2, "A"), ASRSegment(2, 5, "B")]
    segs = make_segments(asr)
    assert [s.id for s in segs] == [0, 1]


def test_make_segments_max_chars():
    asr = [ASRSegment(0.0, 3.0, "text")]
    segs = make_segments(asr)
    assert segs[0].max_chars == round(3.0 * CHARS_PER_SEC)


def test_make_segments_min_max_chars():
    # Very short segment — floor at 8
    asr = [ASRSegment(0.0, 0.1, "A")]
    segs = make_segments(asr)
    assert segs[0].max_chars >= 8


# ── GeminiTranslator (no network) ─────────────────────────────────────────────

def _make_translator(**kw) -> GeminiTranslator:
    """Create translator with a fake API key."""
    return GeminiTranslator(api_key="fake-key-for-tests", **kw)


def _json_response(pairs: list) -> str:
    """Build a JSON response string like Gemini returns."""
    return json.dumps([{"id": id_, "vi": vi} for id_, vi in pairs])


def test_translate_empty_returns_empty():
    t = _make_translator()
    # Should NOT call _call_model at all
    with patch.object(t, "_call_model", side_effect=AssertionError("should not call API")):
        result = t.translate([])
    assert result == []


def test_translate_single_batch():
    t = _make_translator()
    segs = [TranslateSegment(id=i, zh=f"text{i}", max_chars=20) for i in range(3)]
    response = _json_response([(0, "v0"), (1, "v1"), (2, "v2")])

    with patch.object(t, "_call_model", return_value=response):
        results = t.translate(segs)

    assert len(results) == 3
    assert results[0].vi == "v0"
    assert results[2].vi == "v2"


def test_translate_missing_id_retried():
    """First call returns only id 0 and 2; id 1 is missing → retry → all returned."""
    t = _make_translator()
    segs = [TranslateSegment(id=i, zh=f"zh{i}", max_chars=20) for i in range(3)]

    call_count = [0]
    def fake_call(model, prompt):
        call_count[0] += 1
        if call_count[0] == 1:
            return _json_response([(0, "v0"), (2, "v2")])  # missing id 1
        return _json_response([(1, "v1")])  # retry fills id 1

    with patch.object(t, "_call_model", side_effect=fake_call):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    vi_map = {r.id: r.vi for r in results}
    assert vi_map[0] == "v0"
    assert vi_map[1] == "v1"
    assert vi_map[2] == "v2"
    assert call_count[0] == 2


def test_translate_fallback_on_quota_error():
    """429 on default model → switch to fallback, retry succeeds."""
    t = _make_translator()
    segs = [TranslateSegment(id=0, zh="text", max_chars=20)]
    used_models = []

    def fake_call(model, prompt):
        used_models.append(model)
        if model == DEFAULT_MODEL:
            exc = Exception("429 RESOURCE_EXHAUSTED")
            exc.code = 429
            exc.status = "RESOURCE_EXHAUSTED"
            raise exc
        return _json_response([(0, "fallback_vi")])

    with patch.object(t, "_call_model", side_effect=fake_call):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    assert DEFAULT_MODEL in used_models
    assert FALLBACK_MODEL in used_models
    assert results[0].vi == "fallback_vi"


def test_translate_two_batches_with_context():
    """Second batch receives context from last segments of first batch."""
    t = _make_translator(batch_size=2)
    segs = [TranslateSegment(id=i, zh=f"zh{i}", max_chars=20) for i in range(4)]
    prompts_seen = []

    def fake_call(model, prompt):
        prompts_seen.append(prompt)
        if "zh0" in prompt:
            return _json_response([(0, "v0"), (1, "v1")])
        return _json_response([(2, "v2"), (3, "v3")])

    with patch.object(t, "_call_model", side_effect=fake_call):
        results = t.translate(segs)

    assert len(results) == 4
    # Second batch prompt should contain context from first batch
    second_prompt = prompts_seen[1]
    assert "v1" in second_prompt  # last translated segment appears as context


def test_translate_fallback_on_404():
    """404 NOT_FOUND on default model → switch to fallback, retry succeeds."""
    t = _make_translator()
    segs = [TranslateSegment(id=0, zh="text", max_chars=20)]
    used_models = []

    def fake_call(model, prompt):
        used_models.append(model)
        if model == DEFAULT_MODEL:
            exc = Exception("404 NOT_FOUND: model no longer available")
            exc.code = 404
            exc.status = "NOT_FOUND"
            raise exc
        return _json_response([(0, "vi_from_fallback")])

    with patch.object(t, "_call_model", side_effect=fake_call):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    assert DEFAULT_MODEL in used_models
    assert FALLBACK_MODEL in used_models
    assert results[0].vi == "vi_from_fallback"


def test_translate_both_models_fail_returns_empty_vi():
    """If both models fail, segments return empty vi_text (not exception)."""
    t = _make_translator()
    segs = [TranslateSegment(id=0, zh="text", max_chars=20)]

    def always_fail(model, prompt):
        exc = Exception("429 RESOURCE_EXHAUSTED")
        exc.code = 429
        exc.status = "RESOURCE_EXHAUSTED"
        raise exc

    with patch.object(t, "_call_model", side_effect=always_fail):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    assert results[0].vi == ""


def test_translate_both_models_fail_404_returns_empty_vi():
    """If both models return 404, segments return empty vi_text."""
    t = _make_translator()
    segs = [TranslateSegment(id=0, zh="text", max_chars=20)]

    def always_404(model, prompt):
        exc = Exception("404 NOT_FOUND")
        exc.code = 404
        exc.status = "NOT_FOUND"
        raise exc

    with patch.object(t, "_call_model", side_effect=always_404):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    assert results[0].vi == ""


def test_translate_fallback_exhausted_returns_empty_vi():
    """Legacy alias — same as test_translate_both_models_fail_returns_empty_vi."""
    t = _make_translator()
    segs = [TranslateSegment(id=0, zh="text", max_chars=20)]

    def always_fail(model, prompt):
        exc = Exception("429 RESOURCE_EXHAUSTED")
        exc.code = 429
        exc.status = "RESOURCE_EXHAUSTED"
        raise exc

    with patch.object(t, "_call_model", side_effect=always_fail):
        with patch("dubber.core.translate.gemini.time.sleep"):
            results = t.translate(segs)

    assert results[0].vi == ""


# ── write_translate_json ──────────────────────────────────────────────────────

def test_write_translate_json_schema(tmp_path):
    asr = [ASRSegment(0.0, 2.5, "大家好")]
    results = [TranslateResult(id=0, vi="Xin chào")]
    out = tmp_path / "translate.json"
    write_translate_json(asr, results, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data) == 1
    row = data[0]
    assert row["id"] == 0
    assert row["start"] == pytest.approx(0.0)
    assert row["end"] == pytest.approx(2.5)
    assert row["src_text"] == "大家好"
    assert row["vi_text"] == "Xin chào"


def test_write_translate_json_utf8_no_escape(tmp_path):
    asr = [ASRSegment(0.0, 1.0, "大家好")]
    results = [TranslateResult(id=0, vi="Xin chào mọi người")]
    out = tmp_path / "translate.json"
    write_translate_json(asr, results, out)
    raw = out.read_text(encoding="utf-8")
    assert "\\u" not in raw
    assert "Xin chào mọi người" in raw


def test_write_translate_json_missing_vi_is_empty(tmp_path):
    asr = [ASRSegment(0.0, 1.0, "text")]
    results: List[TranslateResult] = []  # no translation
    out = tmp_path / "translate.json"
    write_translate_json(asr, results, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data[0]["vi_text"] == ""


def test_no_api_key_raises():
    env_backup = os.environ.pop("GEMINI_API_KEY", None)
    try:
        with pytest.raises(ValueError, match="GEMINI_API_KEY"):
            GeminiTranslator(api_key=None)
    finally:
        if env_backup is not None:
            os.environ["GEMINI_API_KEY"] = env_backup


# ── NLLB 1.3B ─────────────────────────────────────────────────────────────────


from dubber.core.translate import local_translator as lt  # noqa: E402


class _FakeHF:
    """Stands in for AutoTokenizer / AutoModelForSeq2SeqLM; records from_pretrained calls."""

    def __init__(self, fail_revision: bool = False, error: Exception | None = None):
        self.calls: list = []
        self.fail_revision, self.error = fail_revision, error

    def from_pretrained(self, name, **kw):
        self.calls.append((name, kw))
        if self.error:
            raise self.error
        if self.fail_revision and "revision" in kw:
            raise OSError("revision not found")
        return object()


def test_nllb_is_1_3b():
    assert lt.NLLB_MODEL == "facebook/nllb-200-distilled-1.3B"
    assert lt.NLLBTranslator()._model_name == lt.NLLB_MODEL


def test_nllb_loads_pinned_safetensors_revision():
    tok, model = _FakeHF(), _FakeHF()
    lt.NLLBTranslator()._from_pretrained(tok, model)
    assert tok.calls == [(lt.NLLB_MODEL, {"revision": lt.NLLB_REVISION})]
    assert model.calls == [(lt.NLLB_MODEL, {"revision": lt.NLLB_REVISION, "use_safetensors": True})]


def test_nllb_falls_back_to_main_when_revision_missing():
    tok, model = _FakeHF(fail_revision=True), _FakeHF(fail_revision=True)
    lt.NLLBTranslator()._from_pretrained(tok, model)
    assert tok.calls[-1] == (lt.NLLB_MODEL, {})
    assert model.calls[-1] == (lt.NLLB_MODEL, {})


def test_nllb_other_model_has_no_pinned_revision():
    tok, model = _FakeHF(), _FakeHF()
    lt.NLLBTranslator("facebook/nllb-200-distilled-600M")._from_pretrained(tok, model)
    assert model.calls == [("facebook/nllb-200-distilled-600M", {})]


def test_nllb_out_of_memory_gives_clear_message(monkeypatch):
    pytest.importorskip("transformers")
    tr = lt.NLLBTranslator()
    monkeypatch.setattr(tr, "_from_pretrained", lambda *a, **k: (_ for _ in ()).throw(MemoryError()))
    with pytest.raises(RuntimeError, match="không đủ RAM"):
        tr._load()


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("DUBBER_SLOW_TESTS"), reason="set DUBBER_SLOW_TESTS=1")
def test_nllb_real_translation():
    from dubber.core.translate.base import TranslateSegment
    out = lt.NLLBTranslator().translate([TranslateSegment(id=0, zh="你好，朋友。", max_chars=30)])
    assert out[0].vi and not any("一" <= c <= "鿿" for c in out[0].vi)
