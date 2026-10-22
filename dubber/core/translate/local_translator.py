"""Offline translators: Helsinki-NLP/opus-mt-zh-vi (MarianMT) and NLLB-200 1.3B.

Models download to the HuggingFace cache on first use (MarianMT ~300 MB,
NLLB ~5.5 GB), then work completely offline. No API key required.
"""
from __future__ import annotations

import logging
from typing import List

from dubber.core.translate.base import Translator, TranslateResult, TranslateSegment

log = logging.getLogger(__name__)

MARIAN_MODEL  = "Helsinki-NLP/opus-mt-zh-vi"   # ~300 MB, fast
NLLB_MODEL    = "facebook/nllb-200-distilled-1.3B"  # ~5.5 GB, better quality, ~6 GB RAM
# The repo's main branch only has pytorch_model.bin; HF's converter bot added
# model.safetensors in refs/pr/5. Pinning that commit downloads the safetensors once —
# without it transformers fetches the .bin and then the safetensors too (2× 5.5 GB) —
# and still resolves from the cache when offline.
NLLB_REVISION = "f9e6ff7fc090740fcce5af7d7636cb27a33d0f18"

_BATCH = 16
_NLLB_BATCH = 8   # 1.3B: smaller batches keep peak RAM in check
_MAX_LEN = 256


def _best_device() -> str:
    """Apple GPU (MPS) / CUDA when available — NLLB is ~2.4× faster on M1 MPS than CPU."""
    import torch
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


class LocalTranslator(Translator):
    """Offline Chinese → Vietnamese via Helsinki-NLP/opus-mt-zh-vi (MarianMT).

    Lazy-loads the model on first call to translate().
    """

    def __init__(self, model_name: str = MARIAN_MODEL) -> None:
        self._model_name = model_name
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import os, sys
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from transformers import MarianMTModel, MarianTokenizer
        except ImportError as exc:
            if getattr(sys, 'frozen', False):
                raise RuntimeError(
                    "Dịch offline không khả dụng trong bản đóng gói (.exe).\n"
                    "Vui lòng dùng Groq (online) hoặc chạy từ source code."
                ) from exc
            raise RuntimeError(
                "transformers chưa được cài. Chạy: pip install transformers sentencepiece"
            ) from exc

        log.info("Loading local translation model: %s (lần đầu cần tải ~300MB)", self._model_name)
        self._tokenizer = MarianTokenizer.from_pretrained(self._model_name)
        self._device = _best_device()
        self._model = MarianMTModel.from_pretrained(self._model_name).to(self._device)
        self._model.eval()
        log.info("Local model ready: %s (%s)", self._model_name, self._device)

    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        if not segments:
            return []
        self._load()

        import torch

        texts = [seg.zh for seg in segments]
        all_vi: List[str] = []

        for i in range(0, len(texts), _BATCH):
            if self.cancel_check:
                self.cancel_check()
            batch = texts[i : i + _BATCH]
            inputs = self._tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=_MAX_LEN,
            ).to(self._device)
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    max_length=_MAX_LEN,
                    num_beams=4,
                )
            decoded = self._tokenizer.batch_decode(outputs, skip_special_tokens=True)
            all_vi.extend(decoded)
            log.debug("Local translate batch %d/%d done", i // _BATCH + 1, (len(texts) - 1) // _BATCH + 1)

        return [TranslateResult(id=seg.id, vi=vi) for seg, vi in zip(segments, all_vi)]


class NLLBTranslator(Translator):
    """Offline Chinese → Vietnamese via facebook/nllb-200-distilled-1.3B.

    Better quality than MarianMT but ~5.5 GB download and ~6 GB RAM.
    """

    SRC_LANG = "zho_Hans"
    TGT_LANG = "vie_Latn"

    def __init__(self, model_name: str = NLLB_MODEL,
                 revision: "str | None" = NLLB_REVISION) -> None:
        self._model_name = model_name
        self._revision = revision if model_name == NLLB_MODEL else None
        self._model = None
        self._tokenizer = None

    def _load(self) -> None:
        if self._model is not None:
            return
        import os, sys
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        try:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        except ImportError as exc:
            if getattr(sys, 'frozen', False):
                raise RuntimeError(
                    "Dịch offline không khả dụng trong bản đóng gói (.exe).\n"
                    "Vui lòng dùng Groq (online) hoặc chạy từ source code."
                ) from exc
            raise RuntimeError(
                "transformers chưa được cài. Chạy: pip install transformers sentencepiece"
            ) from exc

        log.info("Loading NLLB model: %s (lần đầu cần tải ~5.5GB)", self._model_name)
        try:
            self._tokenizer, model = self._from_pretrained(AutoTokenizer, AutoModelForSeq2SeqLM)
            # Must be set on the tokenizer: transformers 5 ignores src_lang passed per call,
            # and NLLB then treats the input as English and copies the Chinese through.
            self._tokenizer.src_lang = self.SRC_LANG
            self._device = _best_device()
            self._model = model.to(self._device)
        except (MemoryError, RuntimeError) as exc:
            if isinstance(exc, MemoryError) or "memory" in str(exc).lower():
                raise RuntimeError(
                    "Máy không đủ RAM cho NLLB 1.3B (cần ~6GB) — chọn MarianMT hoặc Groq"
                ) from exc
            raise
        self._model.eval()
        log.info("NLLB model ready (%s)", self._device)

    def _from_pretrained(self, tok_cls, model_cls):
        """Tokenizer + model, preferring the pinned safetensors commit."""
        if self._revision:
            try:
                return (tok_cls.from_pretrained(self._model_name, revision=self._revision),
                        model_cls.from_pretrained(self._model_name, revision=self._revision,
                                                  use_safetensors=True))
            except OSError as exc:   # commit gone / not cached offline → plain main branch
                log.warning("NLLB safetensors revision unavailable (%s) — using main", exc)
        return (tok_cls.from_pretrained(self._model_name),
                model_cls.from_pretrained(self._model_name))

    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        if not segments:
            return []
        self._load()

        import torch

        tgt_id = self._tokenizer.convert_tokens_to_ids(self.TGT_LANG)
        texts = [seg.zh for seg in segments]
        all_vi: List[str] = []

        for i in range(0, len(texts), _NLLB_BATCH):
            if self.cancel_check:
                self.cancel_check()
            batch = texts[i : i + _NLLB_BATCH]
            inputs = self._tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=_MAX_LEN,
            ).to(self._device)
            with torch.no_grad():
                outputs = self._model.generate(
                    **inputs,
                    forced_bos_token_id=tgt_id,
                    max_length=_MAX_LEN,
                    num_beams=4,
                )
            decoded = self._tokenizer.batch_decode(outputs, skip_special_tokens=True)
            all_vi.extend(decoded)

        return [TranslateResult(id=seg.id, vi=vi) for seg, vi in zip(segments, all_vi)]


class AutoTranslator(Translator):
    """Try Groq; automatically fall back to LocalTranslator on any failure.

    Falls back immediately (no retry) when:
    - GROQ_API_KEY is not set
    - Network is unavailable
    - API returns an error

    When torch/transformers are not installed (e.g. in the packaged .exe),
    local fallback is silently disabled — Groq is required in that case.
    """

    on_rate_limit = None   # passed on to Groq (GUI: ask continue / MarianMT)

    def __init__(self, local_model: str = MARIAN_MODEL) -> None:
        # Check if offline translation libraries are available
        self._local_available = False
        self._local = None
        try:
            import torch  # noqa: F401
            self._local = LocalTranslator(local_model)
            self._local_available = True
        except ImportError:
            log.info("AutoTranslator: torch/transformers không khả dụng — chỉ dùng Groq")

        self._groq = None
        self._groq_failed = False
        try:
            from dubber.core.translate.groq_translator import GroqTranslator
            self._groq = GroqTranslator()
            log.info(
                "AutoTranslator: Groq sẵn sàng, local fallback=%s",
                "có" if self._local_available else "không có",
            )
        except ValueError:
            log.info("AutoTranslator: no Groq key — sử dụng local translator")
            self._groq_failed = True

    def translate(self, segments: List[TranslateSegment]) -> List[TranslateResult]:
        import sys
        for sub in (self._groq, self._local):
            if sub is not None:
                sub.cancel_check = self.cancel_check
        if self._groq is not None:
            self._groq.on_rate_limit = self.on_rate_limit
        _groq_error = None
        if self._groq and not self._groq_failed:
            from dubber.core.utils.cancel import JobCancelled
            try:
                results = self._groq.translate(segments)
                return self._fill_missing(segments, results)
            except JobCancelled:
                raise
            except Exception as exc:
                log.warning("Groq failed (%s) → chuyển sang local translator", exc)
                self._groq_failed = True
                _groq_error = exc
        if self._local_available:
            return self._local.translate(segments)
        # Both paths failed — build a helpful error
        if _groq_error:
            raise RuntimeError(
                f"Groq gặp lỗi: {_groq_error}\n"
                "Kiểm tra kết nối mạng và khóa Groq (tab Giọng & dịch → 🔑 Đặt khóa Groq…)."
            )
        raise RuntimeError(
            "Chưa có khóa Groq.\n"
            "Đặt khóa ở tab Giọng & dịch → 🔑 Đặt khóa Groq…,\n"
            "hoặc chọn 'Local — MarianMT offline' để dịch không cần internet."
        )

    def _fill_missing(
        self, segments: List[TranslateSegment], results: List[TranslateResult]
    ) -> List[TranslateResult]:
        """Groq returns vi="" for segments it could not translate — fill those locally."""
        by_id = {r.id: r for r in results}
        missing = [s for s in segments if not by_id.get(s.id) or not by_id[s.id].vi.strip()]
        if not missing:
            return results
        if not self._local_available:
            if len(missing) == len(segments):
                raise RuntimeError(
                    f"Groq không dịch được đoạn nào ({len(missing)} đoạn).\n"
                    "Có thể đã hết quota miễn phí — đợi vài phút rồi thử lại."
                )
            log.warning("%d đoạn Groq không dịch được — để trống", len(missing))
            return results
        log.warning("%d/%d đoạn Groq không dịch được → dịch bằng local", len(missing), len(segments))
        for r in self._local.translate(missing):
            by_id[r.id] = r
        return [by_id.get(s.id, TranslateResult(id=s.id, vi="")) for s in segments]
