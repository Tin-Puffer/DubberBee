"""Gemini-based translator with batching, context, and quota/model fallback."""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import Dict, List, Optional

from dubber.core.translate.base import (
    Translator,
    TranslateResult,
    TranslateSegment,
)

log = logging.getLogger(__name__)

# Model IDs verified against live API (google-genai 2.32.0, Oct 2026).
# DEFAULT: gemini-3.8-flash  (confirmed present in models.list())
# FALLBACK: gemini-3.5-flash-lite  (confirmed present; gemini-3.8-flash-lite
#           does NOT exist in the API — verified Oct 2026)
DEFAULT_MODEL = "gemini-3.8-flash"
FALLBACK_MODEL = "gemini-3.5-flash-lite"

BATCH_SIZE = 30
CONTEXT_SIZE = 3      # prior translated segments sent as context per batch
MAX_RETRIES = 4       # total attempts per batch (includes missing-ID retries)
MAX_BACKOFF = 30.0    # seconds

_SYSTEM_PROMPT = """\
Bạn là dịch giả chuyên nghiệp dịch phim. Nhận JSON array [{id, zh, max_chars}], trả về JSON array [{id, vi}].
Quy tắc:
- Dịch tự nhiên như lời thoại, không văn viết.
- Xưng hô nhất quán trong toàn video.
- Tên riêng: phiên âm Hán-Việt hoặc giữ nguyên, không đổi giữa chừng.
- Không thêm giải thích, ghi chú hay markdown.
- Bản dịch tiếng Việt phải ngắn gọn: dưới max_chars ký tự.
- Chỉ trả về JSON array, không có text khác."""

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.MULTILINE)


class GeminiTranslator(Translator):
    """Translate Chinese segments to Vietnamese via the Gemini API.

    Args:
        api_key: Gemini API key (reads GEMINI_API_KEY env var if None).
        model: Primary model ID (default: gemini-3.8-flash).
        fallback: Fallback model on quota/404 error (default: gemini-3.5-flash-lite).
        batch_size: Segments per API request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        fallback: str = FALLBACK_MODEL,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        key = api_key or os.environ.get("GEMINI_API_KEY", "")
        if not key:
            raise ValueError(
                "Gemini API key not found. "
                "Set the GEMINI_API_KEY environment variable."
            )
        self._api_key = key
        self._model = model
        self._fallback = fallback
        self._batch_size = batch_size
        self._client = None  # lazy init

    def _get_client(self):
        if self._client is None:
            from google import genai
            from google.genai import types
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=60000),  # 60s — avoid infinite hang
            )
        return self._client

    def _call_model(self, model: str, prompt: str) -> str:
        """Call Gemini and return raw text. Override in tests."""
        import concurrent.futures
        from google.genai import types

        def _do_call():
            return self._get_client().models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    temperature=0.3,
                ),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_do_call)
            try:
                response = future.result(timeout=90)
            except concurrent.futures.TimeoutError:
                raise TimeoutError("Gemini API did not respond within 90s")
        return response.text

    # ── Public API ────────────────────────────────────────────────────────────

    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        if not segments:
            return []

        all_results: Dict[int, str] = {}
        context: List[Dict] = []

        for i in range(0, len(segments), self._batch_size):
            batch = segments[i : i + self._batch_size]
            batch_results = self._translate_batch_with_retry(batch, context)
            all_results.update(batch_results)

            for seg in batch[-CONTEXT_SIZE:]:
                if seg.id in all_results:
                    context.append({"id": seg.id, "zh": seg.zh, "vi": all_results[seg.id]})
            context = context[-CONTEXT_SIZE:]

        return [
            TranslateResult(id=seg.id, vi=all_results.get(seg.id, ""))
            for seg in segments
        ]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _translate_batch_with_retry(
        self,
        batch: List[TranslateSegment],
        context: List[Dict],
    ) -> Dict[int, str]:
        results: Dict[int, str] = {}
        pending = list(batch)
        current_model = self._model

        for attempt in range(MAX_RETRIES):
            if not pending:
                break
            if attempt > 0:
                delay = min(2 ** attempt + random.uniform(0, 1), MAX_BACKOFF)
                log.info("Backoff %.1fs before retry %d/%d", delay, attempt, MAX_RETRIES)
                time.sleep(delay)

            prompt = _build_prompt(pending, context)
            try:
                raw = self._call_model(current_model, prompt)
                parsed = _parse_response(raw)
                valid_ids = {s.id for s in pending}
                results.update({k: v for k, v in parsed.items() if k in valid_ids})
            except Exception as exc:
                if _should_use_fallback(exc):
                    if current_model != self._fallback:
                        reason = "quota exceeded" if _is_quota_error(exc) else "model not found"
                        log.warning(
                            "%s on %s → switching to fallback %s",
                            reason, current_model, self._fallback,
                        )
                        current_model = self._fallback
                    else:
                        log.error(
                            "Both models failed (%s and %s). "
                            "Check quota at https://aistudio.google.com or verify model IDs. "
                            "Last error: %s",
                            self._model, self._fallback, exc,
                        )
                        break
                else:
                    log.warning("API/parse error on attempt %d: %s", attempt, exc)

            pending = [s for s in pending if s.id not in results]

        if pending:
            log.warning(
                "%d segment(s) could not be translated: ids=%s",
                len(pending), [s.id for s in pending],
            )
        return results


# ── Pure functions ────────────────────────────────────────────────────────────

def _build_prompt(segments: List[TranslateSegment], context: List[Dict]) -> str:
    parts: List[str] = []
    if context:
        parts.append("Context (đã dịch trước):\n" + json.dumps(context, ensure_ascii=False))
        parts.append("")
    payload = [{"id": s.id, "zh": s.zh, "max_chars": s.max_chars} for s in segments]
    parts.append("Dịch các đoạn sau:\n" + json.dumps(payload, ensure_ascii=False))
    return "\n".join(parts)


def _parse_response(raw: str) -> Dict[int, str]:
    """Parse Gemini response → {id: vi_text}. Strips ```json fences."""
    text = _FENCE_RE.sub("", raw).strip()
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data)}")
    result: Dict[int, str] = {}
    for item in data:
        if isinstance(item, dict) and "id" in item and "vi" in item:
            result[int(item["id"])] = str(item["vi"]).strip()
    return result


def _is_quota_error(exc: Exception) -> bool:
    """True if the error is quota exhaustion (HTTP 429 / RESOURCE_EXHAUSTED)."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", "") or ""
    msg = str(exc)
    return (
        code == 429
        or "RESOURCE_EXHAUSTED" in status.upper()
        or "RESOURCE_EXHAUSTED" in msg.upper()
        or "429" in msg
    )


def _is_not_found_error(exc: Exception) -> bool:
    """True if the error is model-not-found (HTTP 404 / NOT_FOUND)."""
    code = getattr(exc, "code", None)
    status = getattr(exc, "status", "") or ""
    msg = str(exc)
    return (
        code == 404
        or "NOT_FOUND" in status.upper()
        or "404" in msg
        or "no longer available" in msg.lower()
    )


def _should_use_fallback(exc: Exception) -> bool:
    """True if the error should trigger a switch to the fallback model."""
    return _is_quota_error(exc) or _is_not_found_error(exc)
