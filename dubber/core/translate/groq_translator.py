"""Groq-based translator — uses Llama models via the Groq API."""
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

DEFAULT_MODEL = "qwen/qwen3.8-27b"
FALLBACK_MODEL = "openai/gpt-oss-20b"

BATCH_SIZE = 30
CONTEXT_SIZE = 3
MAX_RETRIES = 6
MAX_BACKOFF = 30.0
RATE_LIMIT_WAIT = 20.0  # free tier is per-minute (TPM/RPM) — waiting usually clears it
MIN_WAIT, MAX_WAIT = 3.0, 120.0   # bounds for the server's retry-after hint
ASK_AGAIN_AFTER = 4     # after "continue", ask again once this many requests in a row
                        # are still rate-limited


class SwitchToLocal(Exception):
    """The user chose to translate the rest with MarianMT (rate limit dialog)."""

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
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


class GroqTranslator(Translator):
    """Translate Chinese segments to Vietnamese via the Groq API.

    Args:
        api_key: Groq API key (reads GROQ_API_KEY env var if None).
        model: Primary model ID (default: llama-3.3-70b-versatile).
        fallback: Fallback model on quota error.
        batch_size: Segments per API request.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        fallback: str = FALLBACK_MODEL,
        batch_size: int = BATCH_SIZE,
    ) -> None:
        key = api_key or os.environ.get("GROQ_API_KEY", "")
        if not key:
            raise ValueError(
                "Chưa có khóa Groq — đặt ở tab Giọng & dịch → 🔑 Đặt khóa Groq… "
                "(hoặc biến môi trường GROQ_API_KEY)."
            )
        self._api_key = key
        self._model = model
        self._fallback = fallback
        self._batch_size = batch_size
        self._client = None
        # GUI hook, called when both models are rate-limited:
        #   on_rate_limit({"wait", "done", "total", "again", "can_local"}) -> "continue" | "local"
        # Without it (CLI) the translator just waits and retries on its own.
        self.on_rate_limit = None
        self._done = self._total = 0
        self._asked = False
        self._limited_since_ask = 0

    def _get_client(self):
        if self._client is None:
            from groq import Groq
            # No hidden SDK retries: rate-limit waits are decided here (and may ask the user)
            self._client = Groq(api_key=self._api_key, max_retries=0)
        return self._client

    def _call_model(self, model: str, prompt: str) -> str:
        response = self._get_client().chat.completions.create(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=model,
            temperature=0.3,
            timeout=30,
        )
        return response.choices[0].message.content

    # ── Public API ────────────────────────────────────────────────────────────

    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        if not segments:
            return []

        all_results: Dict[int, str] = {}
        context: List[Dict] = []
        self._total = len(segments)

        for i in range(0, len(segments), self._batch_size):
            self._check()
            self._done = len(all_results)
            batch = segments[i : i + self._batch_size]
            try:
                batch_results = self._translate_batch_with_retry(batch, context)
            except SwitchToLocal as sw:
                all_results.update(sw.args[0] if sw.args else {})
                return self._finish_locally(segments, all_results)
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

    def _finish_locally(self, segments, done: Dict[int, str]) -> List[TranslateResult]:
        """Translate what Groq hasn't with MarianMT; keep Groq's lines."""
        from dubber.core.translate.local_translator import LocalTranslator
        rest = [s for s in segments if not done.get(s.id)]
        log.warning("Groq bị giới hạn — dịch %d/%d câu còn lại bằng MarianMT", len(rest), len(segments))
        local = LocalTranslator()
        local.cancel_check = self.cancel_check
        for r in local.translate(rest):
            done[r.id] = r.vi
        return [TranslateResult(id=s.id, vi=done.get(s.id, "")) for s in segments]

    def _rate_limited(self, exc: Exception, partial: Dict[int, str]) -> None:
        """Both models are rate-limited: ask the user (GUI) when due, then wait it out.

        Raises SwitchToLocal if the user picks MarianMT.
        """
        wait = _retry_after(exc)
        if self.on_rate_limit is not None:
            self._limited_since_ask += 1
            if not self._asked or self._limited_since_ask >= ASK_AGAIN_AFTER:
                choice = self.on_rate_limit({
                    "wait": wait, "done": self._done + len(partial), "total": self._total,
                    "again": self._asked, "can_local": _local_available(),
                })
                self._asked, self._limited_since_ask = True, 0
                if choice == "local":
                    raise SwitchToLocal(partial)
        log.warning("Groq rate-limited → waiting %.0fs", wait)
        self._sleep(wait)

    def _check(self) -> None:
        if self.cancel_check:
            self.cancel_check()

    def _sleep(self, seconds: float) -> None:
        """time.sleep that still honours cancellation every 0.25 s."""
        if not self.cancel_check:
            time.sleep(seconds)
            return
        left = seconds
        while left > 0:
            self._check()
            time.sleep(min(0.25, left))
            left -= 0.25

    def _translate_batch_with_retry(
        self,
        batch: List[TranslateSegment],
        context: List[Dict],
    ) -> Dict[int, str]:
        results: Dict[int, str] = {}
        pending = list(batch)
        current_model = self._model
        attempt = 0   # non-rate-limit failures; rate-limit waits don't use up attempts

        while pending and attempt < MAX_RETRIES:
            prompt = _build_prompt(pending, context)
            try:
                raw = self._call_model(current_model, prompt)
                parsed = _parse_response(raw)
                valid_ids = {s.id for s in pending}
                results.update({k: v for k, v in parsed.items() if k in valid_ids})
                self._asked = False          # a fresh limit later is a new episode
                self._limited_since_ask = 0
            except Exception as exc:
                if _is_rate_limit(exc):
                    if current_model != self._fallback:
                        log.warning("rate limit on %s → switching to fallback %s",
                                    current_model, self._fallback)
                        current_model = self._fallback
                    else:
                        log.warning("Both models rate-limited (%s, %s)", self._model, self._fallback)
                        self._rate_limited(exc, results)
                        current_model = self._model   # limits are per model: retry the primary
                        if self.on_rate_limit is None:
                            attempt += 1   # no one to ask (CLI): give up after MAX_RETRIES
                    continue
                attempt += 1
                if _is_model_not_found(exc):
                    if current_model == self._fallback:
                        log.error("Both models failed (%s, %s). Last error: %s",
                                  self._model, self._fallback, exc)
                        break
                    log.warning("model not found: %s → switching to fallback %s",
                                current_model, self._fallback)
                    current_model = self._fallback
                else:
                    log.warning("API/parse error on attempt %d (%s): %s", attempt, current_model, exc)
                    # A model that returned unparseable output is likely to repeat it — try the other one
                    current_model = self._fallback if current_model == self._model else self._model
                delay = min(2 ** attempt + random.uniform(0, 1), MAX_BACKOFF)
                log.info("Backoff %.1fs before retry %d/%d", delay, attempt, MAX_RETRIES)
                self._sleep(delay)

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
    """Parse model output → {id: vi_text}.

    Tolerates ```json fences, <think> blocks and prose around the JSON array.
    """
    text = _FENCE_RE.sub("", _THINK_RE.sub("", raw or "")).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start == -1 or end <= start:
            raise ValueError(f"No JSON array in response: {text[:120]!r}")
        data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError(f"Expected JSON array, got {type(data)}")
    result: Dict[int, str] = {}
    for item in data:
        if isinstance(item, dict) and "id" in item and "vi" in item:
            result[int(item["id"])] = str(item["vi"]).strip()
    return result


def _retry_after(exc: Exception) -> float:
    """Seconds the server asks us to wait (retry-after / x-ratelimit-reset-*), bounded."""
    headers = getattr(getattr(exc, "response", None), "headers", None) or {}
    waits = []
    ra = headers.get("retry-after")
    if ra:
        try:
            waits.append(float(ra))
        except ValueError:
            pass
    for key in ("x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
        if headers.get(key):
            waits.append(_parse_duration(headers[key]))
    wait = max(waits) if waits else RATE_LIMIT_WAIT
    return max(MIN_WAIT, min(MAX_WAIT, wait))


def _parse_duration(text: str) -> float:
    """Groq reset headers: "2m59.56s", "7.66s", "120ms" → seconds."""
    total = 0.0
    for num, unit in re.findall(r"([\d.]+)(ms|h|m|s)", text):
        total += float(num) * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
    return total


def _local_available() -> bool:
    import importlib.util
    return all(importlib.util.find_spec(m) is not None for m in ("torch", "transformers"))


def _is_rate_limit(exc: Exception) -> bool:
    msg = str(exc)
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 429 or "rate_limit" in msg.lower() or "429" in msg


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc)
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code == 404 or "model_not_found" in msg.lower() or "does not exist" in msg.lower()


def _should_use_fallback(exc: Exception) -> bool:
    return _is_rate_limit(exc) or _is_model_not_found(exc)
