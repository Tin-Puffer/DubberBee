# Plan M9 — Nâng cấp tách câu (FSMN-VAD), dịch offline (NLLB 1.3B), giọng đọc (VieNeu-TTS) và ghi chú GUI

> Tài liệu này dành cho Claude Code thực thi. Đọc hết một lượt trước khi bắt đầu.
> Ngày chốt yêu cầu: 2026-10-22. Repo: `D:\claudeAgent` (Dubber), nhánh gốc `main` @ `6174206`.

---

## 0. Cách làm việc (bắt buộc)

1. **Không cần đọc lại `CLAUDE.md`** — các quy tắc cần thiết đã ghi sẵn ở đây: không bao giờ log/print API key (chỉ đọc qua `os.environ`); `render_final()` không hardcode `scale/fps/crf/encoder_preset/audio_bitrate`; khi chạy bản đóng gói (`sys.frozen`) model nằm ở `sys._MEIPASS/resources/models/`; FFmpeg tìm theo `resources/ffmpeg[.exe]` → Homebrew `ffmpeg-full` (macOS) → PATH; `AutoTranslator` fallback Groq → Local, cả hai lỗi thì raise `RuntimeError` rõ ràng.
2. **Làm và commit thẳng trên nhánh `main`** (người dùng đã chọn, không tạo nhánh mới). Vì không có nhánh riêng để quay lui, **mỗi phase một (hoặc vài) commit nhỏ, độc lập**, chạy `pytest tests/` xanh trước mỗi commit — không bao giờ commit trạng thái test đỏ hoặc app không mở được. Trước khi bắt đầu, kiểm tra `git status` sạch và `main` khớp `origin/main` (`git fetch` rồi so sánh); nếu không khớp → hỏi người dùng.
3. **Làm theo thứ tự phase.** Phase có "Cổng kiểm tra" (gate) thì phải đạt mới đi tiếp; nếu không đạt → dừng, ghi lại kết quả, hỏi người dùng.
4. **Hỏi người dùng trước khi:** push, tạo PR, upload asset lên GitHub Release, xóa file trong cache HuggingFace, hoặc thay đổi gì ngoài phạm vi plan.
5. **Không đoán API thư viện.** Với `vieneu` và FSMN-VAD: đọc mã nguồn/tài liệu của đúng bản đã cài rồi mới viết code. Các tên hàm trong plan này chỉ là định hướng, phải xác minh.
6. **Viết code giống code xung quanh**: cùng mật độ comment, cách đặt tên, lazy import thư viện nặng, log tiếng Việt/Anh như file đang có.
7. Môi trường dev hiện tại: Windows 11, Python 3.12.10, venv `.venv`, numpy 2.5.4, onnxruntime 1.31, sherpa-onnx 1.13.8, torch 2.14.1, transformers 5.20.2, piper-tts 1.8.0, PySide6 6.12. Mọi thay đổi phải chạy được cả **Windows và macOS** (có `scripts/build_exe_mac.sh`).

---

## 1. Mục tiêu và phạm vi

| # | Thay đổi | Lý do |
|---|---|---|
| A | Tách câu chuẩn hơn: cắt theo **mốc thời gian từng chữ của SenseVoice**, **chạy ASR trên giọng đã tách nhạc**, thay **Silero VAD → FSMN-VAD** (giữ Silero làm dự phòng) | Câu đang bị cắt giữa chừng, thời gian phụ đề lệch |
| B | Thay **NLLB-200 distilled 600M → NLLB-200 distilled 1.3B** | Dịch offline tốt hơn |
| C | Thêm lựa chọn bộ đọc **Piper hoặc VieNeu-TTS**; VieNeu có **thư viện giọng clone được lưu lại** | Giọng tự nhiên hơn, tự tạo giọng mới |
| D | **Mỗi lựa chọn trong GUI có 1 dòng ghi chú ngắn, dễ hiểu** về đánh đổi | Người dùng hiểu ngay chọn gì được/mất gì |

**Ngoài phạm vi (KHÔNG làm):** tự lấy giọng người nói trong video để clone, nhận diện nhiều người nói (diarization), fine-tune giọng, MADLAD, thay Groq, đổi giao diện tổng thể.

---

## 2. Hiện trạng code liên quan (đã khảo sát)

- `dubber/core/pipeline.py`
  - Chuỗi gọi lồng nhau: `run_m5 → run_m4 → run_m3 → run_m2 → run_m1`.
  - `run_m1` trích `audio.wav` (mono 16k) rồi **luôn chạy ASR lại**, ghi `asr.json`.
  - `run_m2` **tái dùng `translate.json` nếu tồn tại** (`_translate_json_is_valid`) mà **không kiểm tra** ASR hay bộ dịch có đổi không → đổi ASR/VAD/bộ dịch vẫn dùng bản dịch cũ. **Phải sửa (Phase 1).**
  - Tách nhạc (`separate_tracks`) chỉ chạy **trong `run_m5`, sau ASR**, và chỉ khi cần cho phần trộn âm.
  - `run_m3` hardcode `PiperTTSEngine` + `find_piper_model(voice)`.
- `dubber/core/asr/sensevoice_sherpa.py`: `SherpaOnnxASR` dùng Silero VAD của sherpa-onnx (`threshold=0.5, min_silence_duration=0.4, min_speech_duration=0.25, max_speech_duration=MAX_SEGMENT_S=6.0`), mỗi đoạn VAD → decode → `ASRSegment(start, end, text)` → `postprocess()`.
- `dubber/core/asr/postprocess.py`: `split_long` chia đoạn >6s tại `。！？；，、` và **ước lượng thời gian theo số ký tự**; `merge_short` gộp đoạn <1s vào đoạn trước **bất kể khoảng cách**.
- **Đã xác minh:** `stream.result.tokens` và `stream.result.timestamps` của SenseVoice (sherpa-onnx 1.13.8) trả mốc thời gian **từng ký tự kèm dấu câu**, ví dụ `('大',0.18) ('家',0.36) ('好',0.48) ('，',0.72)` — tính từ đầu đoạn audio đưa vào.
- `dubber/core/asr/model_manager.py`: tìm model theo `_search_bases()` = bundle (`sys._MEIPASS/resources/models`) → user-data (`platformdirs.user_data_dir("dubber","dubber")/models`) → `spike/models`.
- `dubber/core/audio/separate.py`: `separate_tracks(stereo_wav, out_dir) -> (vocals, instrumental)`, có cache; MDX-Net qua `audio-separator`, fallback FFmpeg (fallback "vocals" chỉ là band-pass — kém). **`audio-separator` không có trong `pyproject.toml`** và **bị exclude trong `dubber.spec`**.
- `dubber/core/translate/local_translator.py`: `NLLB_MODEL = "facebook/nllb-200-distilled-600M"`; `AutoTranslator` chỉ fallback sang MarianMT.
- `dubber/core/tts/base.py`: `TTSEngine.synthesize_one(text, segment_id, rate) -> TTSResult(segment_id, wav_path, duration)`; `tts_cache_key(text, voice, rate)`.
- `dubber/core/tts/piper_tts.py`: `PiperTTSEngine(model_path, tts_dir)`, đổi `rate` → `length_scale`.
- `dubber/core/audio/fit.py` `TimingFitter`: bước 3a gọi lại `synthesize_one(rate="+X%")`, bước 3b `atempo` FFmpeg ≤1.3×, xuất 24 kHz. `mixer.build_dub_wav` resample về 24 kHz (nearest-neighbour, chỉ ổn với tỉ lệ nguyên).
- `dubber/app/gui.py`
  - `OptionsPanel` có `_combo_voice` (liệt kê `list_piper_models()`), `_combo_translator` (auto/groq/local/nllb), `get_config()/set_config()` cho từng video trong hàng đợi.
  - `PipelineWorker` truyền `voice`, `translator` vào `run_m5`.
  - **Bug sẵn có:** dòng ~988 stylesheet nút "▶ Chạy hàng đợi" là chuỗi thường nhưng dùng `}}` → Qt báo `Could not parse stylesheet`.
- `dubber/core/utils/settings.py`: settings JSON tại `user_config_dir("dubber","dubber")/settings.json`, hiện chỉ có `output_dir`.
- `dubber.spec`: gom `resources/models/**` vào bundle; exclude `librosa, scipy, numba, audio_separator, onnx…`.
- `spike/download_models.py`: tải từ mirror GitHub Release `models-v1` trước, nguồn gốc sau; `PIPER_VOICES` có 16 giọng.
- Test hiện có: `tests/test_asr_postprocess.py`, `test_timing_fitter.py`, `test_tts.py`, `test_translate.py`, `test_gui_queue.py`, … — 124 test, đang xanh.

### Thông tin thư viện đã tra (2026-10-22) — vẫn phải xác minh lại khi cài

- **`funasr-onnx` 0.4.3** (MIT): phụ thuộc `librosa, scipy, numpy<=1.26.4, kaldi-native-fbank, PyYAML, jieba, sentencepiece`. **`numpy<=1.26.4` xung đột với numpy 2.5.4 của dự án** → **không cài trực tiếp** (xem Phase 4).
- **`vieneu` 3.8.3** (Apache-2.0, repo `pnnbao97/VieNeu-TTS`, model HF `pnnbao-ump/VieNeu-TTS-v3-Turbo`):
  - API ví dụ: `from vieneu import Vieneu; tts = Vieneu(); audio = tts.infer("...", voice="Hải Đăng"); tts.save(audio, "out.wav")`; ép CPU: `Vieneu(backend="onnx")`.
  - CPU chạy **ONNX Runtime, không cần torch**. Codec v3: MOSS-Audio-Tokenizer-Nano.
  - Clone: audio mẫu **~3–8 giây** (SDK tự cắt ≤8s, tự khử ồn `denoise=True`), **v3 Turbo không cần lời thoại mẫu**.
  - Giọng có sẵn: v3 Turbo 25 giọng (`list_preset_voices()`), giọng tự tạo lưu bằng `save_voices()` vào `voices.json` (spec `vieneu.voice.presets` v1.0).
  - Đầu ra **48 kHz** (Turbo). **Turbo không có tham số `speed`** (chỉ v3 Nano có).
  - Phụ thuộc cứng gồm **`gradio>=5.49.1`** (rất nặng, chỉ cho web UI), `librosa>=0.11`, `sea-g2p`, `soxr`, `kaldi-native-fbank`, `tokenizers`, `huggingface_hub`, `onnxruntime>=1.20`, `soundfile`, `PyYAML`.
  - Ghi chú: v4 là độc quyền (chỉ qua API) — **chỉ dùng v3**.

---

## Phase 0 — Chuẩn bị và đo baseline

1. Ở nhánh `main`, ghi lại commit xuất phát (`git rev-parse --short HEAD`) vào báo cáo để có thể so sánh/quay lui. Chạy `pytest tests/` → ghi số test pass.
2. Sửa bug stylesheet ở `gui.py` (~dòng 988): `"... letter-spacing: 0.3px; }}"` → `"... letter-spacing: 0.3px; }"`. Chạy app, xác nhận không còn warning `Could not parse stylesheet`. Commit riêng.
3. **Bộ video đánh giá:** hỏi người dùng 2–3 video thật đang bị tách câu sai (nên có: 1 video nhạc nền to, 1 video hội thoại nhanh). Nếu người dùng chưa đưa, tạm dùng `spike/models/example_16k.wav` (giọng sạch) và **ghi rõ trong báo cáo là chưa đánh giá trên video thật**.
4. Viết script `scripts/eval_asr.py` (CLI, không đụng GUI):
   - Input: đường dẫn video/wav, tham số `--vad {silero,fsmn}`, `--split {legacy,timestamps}`, `--vocals {on,off}`.
   - Output: `asr.json` + file `.srt` để mở cùng video xem trực quan, in thống kê: số câu, độ dài TB/min/max, số câu kết thúc không phải `。！？`, số câu > 8s, thời gian chạy (RTF).
5. Chạy baseline (`--vad silero --split legacy --vocals off`) trên bộ video, lưu kết quả vào `eval/baseline/` (thêm `eval/` vào `.gitignore` nếu chứa video/audio; chỉ commit script).

**Nghiệm thu:** test xanh, bug stylesheet hết, có số liệu baseline.

---

## Phase 1 — Cache theo "chữ ký" cấu hình (tiền đề bắt buộc)

**Vấn đề:** đổi ASR/VAD/bộ dịch/giọng mà vẫn tái dùng `asr.json`/`translate.json`/`tts/` cũ → kết quả sai, đánh giá sai.

1. Thêm helper trong `pipeline.py` (hoặc `dubber/core/utils/job_cache.py`): ghi `*.sig.json` cạnh mỗi artifact, chứa các tham số ảnh hưởng tới nó:
   - `asr.json`: `{asr_version, vad, split_mode, use_vocals, sherpa_model_name, vad_model_name}`.
   - `translate.json`: chữ ký của `asr.json` (hash nội dung) + `{translator}`.
   - TTS: đã có `tts_cache_key(text, voice, rate)`; bổ sung **engine** vào khóa (`"piper:<stem>"`, `"vieneu:<voice_id>"`) để Piper và VieNeu không đụng nhau.
2. `run_m1`: nếu `asr.json` tồn tại và chữ ký khớp → tái dùng (hiện tại luôn chạy lại — giữ hành vi chạy lại khi khác chữ ký).
3. `run_m2`: chỉ tái dùng `translate.json` khi chữ ký khớp; nếu không → dịch lại.
4. Giữ tương thích: job cũ không có `.sig.json` → coi như không khớp.
5. Danh sách xóa cache khi trim trong `PipelineWorker.run` (`gui.py` ~dòng 143): thêm các file `.sig.json` mới.

**Test mới:** `tests/test_job_cache.py` — đổi translator → dịch lại; đổi VAD → ASR + dịch lại; giữ nguyên → tái dùng (mock engine/translator, không gọi mạng).

---

## Phase 2 — Tách câu theo mốc thời gian từng chữ (thay đổi tác động lớn nhất)

**Ý tưởng:** VAD chỉ còn cắt thô (đoạn dài, có thể nhiều câu); câu được cắt tại dấu kết câu dựa trên **timestamp thật** của từng ký tự.

1. `ASRSegment` (`dubber/core/asr/base.py`): thêm trường tùy chọn `tokens: list[tuple[str, float]] | None = None` (mốc tuyệt đối, giây). Không ghi `tokens` vào `asr.json` (giữ format `{start,end,text}` — `write_asr_json` không đổi), hoặc ghi vào file phụ `asr_tokens.json` để debug.
2. `SherpaOnnxASR._transcribe_chunk`: trả thêm `tokens` + `timestamps`, cộng offset `start` của đoạn VAD → mốc tuyệt đối. **Xác minh** độ dài 2 list bằng nhau và đơn vị là giây; nếu sherpa-onnx không trả timestamps (list rỗng) → fallback chế độ `legacy`.
3. `postprocess.py` — thêm `split_by_timestamps(segments, ...)`:
   - Duyệt token; **ranh giới câu cứng** tại `。！？` (và `…`, `?`, `!`); **ranh giới mềm** tại `，；、：` chỉ dùng khi câu hiện tại vượt `MAX_SEGMENT_S`.
   - `start` câu = timestamp token đầu; `end` câu = timestamp token cuối + ước lượng độ dài token cuối (vd. 0.2s, hoặc trung bình khoảng cách token trong câu), **không vượt** `end` của đoạn VAD và không vượt `start` câu kế.
   - Khoảng lặng lớn giữa 2 token liên tiếp (> 0.6s, hằng số) cũng là ranh giới cứng.
   - Bỏ token rỗng/tag sự kiện (`<|...|>`) nếu có.
4. Sửa `merge_short`: chỉ gộp khi **khoảng cách tới đoạn trước ≤ 0.3s** và tổng độ dài sau gộp ≤ `MAX_SEGMENT_S`; nếu không thì gộp vào đoạn sau (cùng điều kiện) hoặc giữ nguyên.
5. Đổi hằng số: `MAX_SEGMENT_S` cho câu cuối cùng = 8.0 (phụ đề 2 dòng vẫn đọc kịp); VAD `max_speech_duration` tăng lên ~15–20s để VAD không cắt giữa câu (câu sẽ do bước 3 cắt).
6. `postprocess()` nhận `mode: Literal["timestamps","legacy"] = "timestamps"`; `legacy` giữ nguyên thuật toán cũ (để so sánh và fallback).

**Test:** mở rộng `tests/test_asr_postprocess.py` với token giả lập: cắt đúng tại `。！？`; câu dài không dấu kết → cắt tại `，` gần mốc 8s; khoảng lặng >0.6s → tách; `merge_short` không gộp qua khoảng lặng lớn; fallback legacy khi không có tokens.

**Gate 2:** chạy `scripts/eval_asr.py --split timestamps` trên bộ video. Số câu kết thúc không phải `。！？` phải giảm rõ so với baseline, không có câu > 8s trừ khi không có dấu câu nào. Ghi số liệu vào báo cáo.

---

## Phase 3 — Chạy ASR trên giọng đã tách nhạc

1. Trong `run_m1` (trước ASR), nếu tùy chọn `asr_on_vocals=True`:
   - Trích `audio_stereo.wav` (đã có `extract_audio_stereo`) → `separate_tracks(...)` (cache sẵn ở `job_dir/separated`, `run_m5` sẽ tái dùng — không tách 2 lần).
   - Chuyển `vocals.wav` → mono 16 kHz PCM16 `audio_vocals_16k.wav` bằng FFmpeg (dùng `no_window()`), đưa vào ASR.
   - **Chỉ dùng vocals khi tách bằng MDX-Net thành công.** Nếu `audio-separator` không có (ImportError → fallback FFmpeg band-pass) thì **dùng `audio.wav` gốc** cho ASR (band-pass làm ASR tệ hơn). Cần cách nhận biết: cho `separate_tracks` trả thêm/ghi cờ `method: "mdxnet"|"ffmpeg"` (vd. file `separated/method.txt`) — không phá chữ ký hàm cũ (thêm hàm mới hoặc tham số tùy chọn).
   - **Timestamps không đổi** vì vocals cùng độ dài audio gốc — xác minh độ dài (sai lệch < 50 ms), nếu lệch thì fallback audio gốc.
2. Progress GUI: bước tách nhạc giờ có thể xảy ra trước ASR — đảm bảo callback `"separate"` vẫn hiển thị đúng (truyền `on_progress` xuống `run_m1`, hoặc emit từ worker).
3. **Phụ thuộc:** thêm `audio-separator` vào `pyproject.toml` (extra mới `separation`, hoặc vào deps chính — **hỏi người dùng**, mặc định: extra `separation` + đưa vào `setup_dev.ps1`/`setup_mac.sh`). Về bản đóng gói: hiện spec exclude `audio_separator` → trong .exe tùy chọn này tự rơi về audio gốc; **không** tự ý đưa audio-separator vào bundle trong phase này, ghi vào báo cáo như việc cần quyết sau.
4. Tùy chọn trong GUI (xem Phase 8 về ghi chú): checkbox **"Lọc nhạc trước khi nhận diện"**, mặc định **bật**.

**Gate 3:** so sánh `--vocals on/off` trên video có nhạc nền: số câu, câu sai ranh giới (xem .srt). Nếu vocals không cải thiện hoặc làm tệ hơn → báo người dùng trước khi để mặc định bật.

---

## Phase 4 — FSMN-VAD (giữ Silero làm dự phòng)

### 4.1 Quyết định cách tích hợp
`funasr-onnx` ép `numpy<=1.26.4` → **không cài**. Phương án mặc định: **tự viết module suy luận FSMN-VAD gọn** `dubber/core/asr/fsmn_vad.py`, port từ mã nguồn `funasr_onnx` (giấy phép MIT — ghi credit + link trong docstring):
- Đặc trưng fbank bằng `kaldi-native-fbank` (nhẹ, có wheel cho Win/Mac; cũng là phụ thuộc của `vieneu`), áp CMVN từ `am.mvn`, LFR theo `config.yaml`.
- Chạy `model_quant.onnx` (hoặc `model.onnx`) bằng `onnxruntime` (đã có).
- Port phần hậu xử lý `E2EVadModel` (máy trạng thái → danh sách `[start_ms, end_ms]`). Chỉ cần chế độ offline (cả file), không cần streaming.
- **Không** dùng `librosa`/`scipy` (spec đang exclude). Đọc wav bằng `wave` + numpy như `_read_wav` hiện có.
- Đọc `config.yaml` bằng `PyYAML` (thêm vào deps nếu chưa có).

Trước khi port: tải `funasr-onnx` source (sdist) về thư mục riêng trong `$CLAUDE_JOB_DIR/tmp` hoặc thư mục tạm, **đọc mã chứ không cài**, xác định chính xác: tên file model, tham số frontend (n_mels, frame_length, frame_shift, lfr_m, lfr_n, dither=0), tên input/output ONNX, cách giữ cache giữa các chunk.

### 4.2 Model
- Nguồn: ModelScope, model ONNX FSMN-VAD tiếng Trung 16k (thường là `iic/speech_fsmn_vad_zh-cn-16k-common-onnx`, trước đây `damo/...` — **xác minh id**). Các file cần: `model_quant.onnx` (hoặc `model.onnx`), `config.yaml`, `am.mvn`.
- Đặt tại `<models>/fsmn-vad/` (theo `_search_bases()`); thêm `find_fsmn_vad_dir()` vào `model_manager.py`.
- Thêm vào `spike/download_models.py` (tải mặc định), mirror qua Release: **hỏi người dùng trước khi upload asset** lên `models-v1`. Trong lúc chờ, script tải trực tiếp từ ModelScope làm nguồn gốc.

### 4.3 Tích hợp vào ASR
- `SherpaOnnxASR(…, vad: Literal["fsmn","silero"]="fsmn")`. Với FSMN: chạy `fsmn_vad` trên cả file → các khoảng → cắt samples → decode như cũ (giữ nguyên luồng Phase 2: token timestamps + offset).
- Ghép các khoảng VAD quá gần nhau (< 0.3s) và cắt khoảng > 20s tại điểm lặng dài nhất (giữ đoạn đưa vào SenseVoice không quá dài).
- Fallback: không tìm thấy model FSMN hoặc lỗi khi chạy → log cảnh báo, dùng Silero; không có cả hai → chế độ 1 đoạn như hiện nay.
- Pipeline: `run_m1` nhận `vad` (mặc định `"fsmn"`); CLI `__main__.py` thêm `--vad {fsmn,silero}`. **GUI không cần lộ lựa chọn VAD** (tùy chọn kỹ thuật) — nếu người dùng muốn thì thêm sau.
- `dubber.spec`: thêm `kaldi_native_fbank` (collect_all nếu có binary), `yaml` vào hiddenimports; `fsmn-vad/` nằm trong `resources/models/` sẽ tự được gom.

**Test:** `tests/test_fsmn_vad.py` — (a) frontend: shape đặc trưng đúng với audio giả; (b) hậu xử lý: chuỗi xác suất giả → khoảng đúng; (c) tích hợp (skip nếu thiếu model): `example_16k.wav` ra ≥ 5 khoảng, tất cả nằm trong [0, duration].

**Gate 4:** chạy `eval_asr.py` với 4 tổ hợp `{silero,fsmn} × vocals{off,on}` (cùng `--split timestamps`). Chọn mặc định theo số liệu; nếu FSMN **không** tốt hơn Silero trên video thật → báo người dùng (họ đã chốt FSMN nhưng cần biết số liệu) trước khi đặt FSMN làm mặc định.

---

## Phase 5 — NLLB-200 distilled 1.3B

1. `local_translator.py`: `NLLB_MODEL = "facebook/nllb-200-distilled-1.3B"`; sửa docstring đầu file + docstring `NLLBTranslator` + log "lần đầu cần tải ~2.4GB" → **~5.5GB**.
2. Ưu tiên tải **safetensors** (tránh tải trùng `.bin` như bản 600M đang bị — cache 4,7GB): dùng `from_pretrained(..., use_safetensors=True)` nếu repo có safetensors (xác minh trên HF); nếu không có thì để mặc định.
3. Bộ nhớ: bản 1.3B fp32 cần ~6GB RAM. Thêm kiểm tra nhẹ (nếu có `psutil`, hoặc bỏ qua) — **không** thêm phụ thuộc mới chỉ cho việc này; thay vào đó bắt `MemoryError`/`RuntimeError` khi load → raise thông báo tiếng Việt rõ ràng ("Máy không đủ RAM cho NLLB 1.3B (cần ~6GB) — chọn MarianMT hoặc Groq").
4. Batch: giảm `_BATCH` cho NLLB (vd. 8) nếu đo thấy RAM tăng vọt; giữ MarianMT như cũ.
5. GUI: nhãn `"Local — NLLB offline (~2.4GB)"` → `"Offline — NLLB 1.3B (~5.5GB)"` (giữ `userData="nllb"` để config cũ vẫn chạy).
6. `dubber.spec` comment dòng 18 cập nhật dung lượng.
7. **Không tự xóa** cache bản 600M (`~/.cache/huggingface/hub/models--facebook--nllb-200-distilled-600M`, ~4,7GB) — chỉ nhắc người dùng trong báo cáo.

**Test:** cập nhật `tests/test_translate.py` nếu có assert tên model; test load thật đánh dấu `@pytest.mark.slow` và skip mặc định.

**Đo (không bắt buộc đạt ngưỡng):** dịch 30 câu `src_text` trong `%LOCALAPPDATA%\dubber\dubber\jobs\5698eb84ab0c\translate.json` bằng 600M vs 1.3B, ghi thời gian và vài ví dụ so với `vi_text` (bản Groq) vào báo cáo.

---

## Phase 6 — Trừu tượng hóa bộ đọc + engine VieNeu-TTS

### 6.1 Cổng khả thi (làm trước, chưa sửa code app)
Tạo venv thử **riêng** (`$CLAUDE_JOB_DIR/tmp/venv-vieneu` hoặc thư mục tạm) để không làm bẩn `.venv`:
1. `pip install vieneu==3.8.3` (đầy đủ) → chạy ví dụ README, đọc mã nguồn package để xác định **chính xác**: tên class/hàm (`Vieneu`, `infer`, `list_preset_voices`, hàm tạo giọng clone, `save_voices`/load voices), định dạng `audio` trả về (numpy? sample rate?), cách chỉ định thư mục model local / tắt tải tự động, backend ONNX CPU.
2. Thử **không có gradio**: `pip install vieneu --no-deps` + từng dependency trừ `gradio` → `import vieneu` và `infer()` có chạy không. Ghi lại danh sách deps tối thiểu thật sự cần.
3. Kiểm tra xung đột với `.venv` hiện tại: numpy 2.5.4, onnxruntime 1.31, librosa (VieNeu đòi `librosa>=0.11` → kéo `scipy`, `numba` — spec đang exclude!). Nếu `librosa` bắt buộc khi suy luận → cần bỏ khỏi `excludes` trong spec (bundle nặng thêm) — **ghi lại, hỏi người dùng**.
4. Đo trên CPU máy dev: thời gian load model, RTF (thời gian sinh / thời lượng audio) cho 30 câu `vi_text` của job phô mai, RAM đỉnh. So với Piper (gần như tức thì).
5. Thử clone: 1 file mẫu 6–8s (dùng một file trong `C:\Users\VOST\Downloads\dubber-voice-samples\` hoặc mẫu VieNeu) → nghe chất lượng.

**Gate 6:** báo người dùng: deps tối thiểu, có cần gradio/librosa không, RTF, RAM, dung lượng model. Nếu RTF CPU quá chậm (vd. > 3, tức 1 phút audio mất > 3 phút) hoặc không bỏ được gradio → dừng, hỏi người dùng có tiếp tục không.

### 6.2 Trừu tượng hóa (sau khi qua gate)
1. `TTSEngine` (`base.py`): thêm thuộc tính lớp `supports_rate: bool = True` và `engine_id: str`. VieNeu Turbo đặt `supports_rate = False`.
2. `TimingFitter` (`fit.py`) bước 3a: nếu `not engine.supports_rate` → bỏ qua re-synthesize, đi thẳng `atempo` (3b). Giữ `MAX_ATEMPO=1.3`; ghi thống kê số câu phải `pushed` để so sánh Piper/VieNeu.
3. Tạo `dubber/core/tts/factory.py`: `make_tts_engine(engine: str, voice: str | None, tts_dir: Path) -> TTSEngine` với `engine ∈ {"piper","vieneu"}`. `run_m3/run_m4/run_m5` thêm tham số `tts_engine: str = "piper"` truyền xuống; `run_m3` dùng factory thay vì hardcode Piper.
4. `tts_cache_key` gồm engine (Phase 1).

### 6.3 Engine VieNeu
`dubber/core/tts/vieneu_tts.py` — `VieNeuTTSEngine(voice_id, tts_dir, voices_dir)`:
- Lazy import `vieneu`; ImportError → `RuntimeError` tiếng Việt ("Chưa cài VieNeu-TTS: pip install -e \".[vieneu]\"" / trong bản .exe: "Bản đóng gói chưa có VieNeu-TTS").
- Ép backend CPU ONNX (trừ khi có GPU và đã cài bản cuda — ngoài phạm vi).
- Load model **một lần** cho cả video; `synthesize_one` chạy trong `asyncio.to_thread` như Piper; ghi WAV (giữ 48 kHz gốc — mixer resample 48k→24k tỉ lệ nguyên 2:1 OK; nếu SR khác bội số của 24k thì resample bằng `soxr` hoặc FFmpeg để tránh méo do nearest-neighbour).
- `synthesize_all` tuần tự (CPU-bound), log tiến độ như Piper; thêm callback tiến độ để GUI hiện % bước TTS (VieNeu chậm — người dùng cần thấy tiến độ).
- Văn bản: tiền xử lý tối thiểu (bỏ ký tự lạ); để `sea-g2p`/SDK xử lý số, viết tắt.
- Model: tải về `<user-data>/models/vieneu/` (hoặc thư mục SDK cho phép chỉ định); bundle: `resources/models/vieneu/`. Thêm `find_vieneu_model_dir()` vào `model_manager.py`.
- `pyproject.toml`: extra mới `vieneu = [...]` với danh sách deps tối thiểu đã xác định ở 6.1 (không gradio nếu được). Cập nhật `setup_dev.ps1`, `scripts/setup_mac.sh` cài extra này.

**Test:** `tests/test_tts.py` thêm: factory trả đúng engine; `TimingFitter` với engine `supports_rate=False` không gọi `synthesize_one(rate=...)` (mock). Test VieNeu thật `@pytest.mark.slow`, skip khi thiếu package/model.

---

## Phase 7 — Thư viện giọng VieNeu (clone một lần, chọn lại mãi)

### 7.1 Lưu trữ
- Thư mục: `<user_data_dir("dubber","dubber")>/voices/vieneu/` (không nằm trong bundle — giọng của người dùng phải sống qua các lần cập nhật app).
- Mỗi giọng clone: `<voice_id>/` chứa `reference.wav` (bản đã chuẩn hóa: mono, cắt còn ≤8s), `meta.json` (`{id, name, created_at, source_file_name, duration, engine_version}`), và dữ liệu giọng do SDK sinh ra (embedding/voices.json entry — **dùng đúng định dạng của SDK**, ví dụ `save_voices()`), để lần sau **không phải xử lý lại**.
- `voice_id`: slug từ tên + hậu tố ngắn chống trùng.
- Module `dubber/core/tts/voice_library.py`: `list_voices() -> list[VoiceEntry]` (preset của SDK + giọng clone), `add_clone(audio_path, name) -> VoiceEntry`, `rename(voice_id, new_name)`, `delete(voice_id)`. Có test đơn vị (dùng thư mục tạm, mock phần SDK).

### 7.2 Quy trình clone (GUI)
- Trong ô "Giọng" khi bộ đọc = VieNeu: danh sách gồm nhóm **Giọng có sẵn** + nhóm **Giọng đã clone** + mục cuối **"➕ Clone giọng mới…"**.
- Chọn "➕ Clone giọng mới…" → hộp thoại (`QDialog`):
  1. Chọn file (`.wav .mp3 .m4a .flac .ogg`, hoặc **video** — tách audio bằng FFmpeg).
  2. Hiện thời lượng; nếu > 8s cho chọn **đoạn bắt đầu** (spinbox giây) — mặc định 0; nếu < 3s → cảnh báo "Đoạn quá ngắn, giọng sẽ kém giống".
  3. Nút **Nghe thử** (phát đoạn mẫu đã cắt — `QMediaPlayer`/`QSoundEffect`, hoặc bỏ qua nếu phức tạp) và **Đọc thử** (sinh câu mẫu "Xin chào, đây là giọng đọc thử nghiệm." bằng giọng mới, chạy trong `QThread`, không treo GUI).
  4. Ô **Tên giọng** (bắt buộc, không trùng).
  5. **Lưu** → `voice_library.add_clone(...)` → combo tự chọn giọng mới.
- Quản lý: nút nhỏ cạnh combo (⋯) để **đổi tên / xóa** giọng clone (xác nhận trước khi xóa). Giọng có sẵn không xóa được.
- Ghi chú dưới hộp thoại (xem Phase 8): "Dùng 5–8 giây rõ tiếng, một người nói, không nhạc nền".
- Pipeline nhận `voice` dạng `"preset:<tên>"` hoặc `"clone:<voice_id>"`; engine VieNeu phân giải tương ứng. Giọng clone đã bị xóa mà vẫn còn trong config hàng đợi → báo lỗi rõ ràng trước khi chạy video đó.

### 7.3 GUI bộ đọc
- Thêm combo **"Bộ đọc:"** trên dòng "Giọng": `Piper` (mặc định) / `VieNeu-TTS`. Đổi bộ đọc → nạp lại danh sách giọng tương ứng.
- Nếu `vieneu` chưa cài (hoặc bản .exe không có) → mục VieNeu vẫn hiện nhưng **disabled** kèm ghi chú "Chưa cài VieNeu-TTS".
- `get_config()/set_config()`: thêm `"tts_engine"` và giữ `"voice"`; `set_config` phải chịu được config cũ thiếu key (`cfg.get("tts_engine", "piper")`). Cập nhật `tests/test_gui_queue.py`.
- `PipelineWorker`: thêm `tts_engine`, truyền vào `run_m5`.
- Ghi nhớ lựa chọn cuối (bộ đọc + giọng) vào `settings.json` (`DEFAULTS` thêm `tts_engine`, `voice`) để lần mở sau giữ nguyên.

---

## Phase 8 — Ghi chú ngắn cho mọi lựa chọn trong GUI

**Quy tắc:** mỗi ô chọn/checkbox có **1 dòng chữ phụ** (font 10px, màu `_TXT_DIM`, giống `self._hint` đang có) ngay dưới, **đổi theo lựa chọn hiện tại**; đồng thời đặt `setToolTip` cùng nội dung. Một dòng, tiếng Việt đơn giản, không thuật ngữ. Nêu: chất lượng ↔ tốc độ, cần mạng không, dung lượng tải, cần RAM/GPU nếu có.

Nội dung đề xuất (có thể chỉnh câu chữ cho gọn, giữ ý):

| Ô | Lựa chọn | Ghi chú |
|---|---|---|
| Bộ đọc | Piper | Nhanh, giọng hơi đều |
| | VieNeu-TTS | Giọng tự nhiên hơn, xử lý lâu hơn |
| Giọng (VieNeu) | ➕ Clone giọng mới… | Gửi đoạn ghi âm 5–8 giây rõ tiếng, một người nói |
| Dịch thuật | Auto | Dùng Groq, mất mạng thì tự chuyển sang MarianMT |
| | Groq | Dịch hay nhất, cần mạng và khóa GROQ_API_KEY |
| | MarianMT | Không cần mạng, nhanh, dịch kém hơn |
| | NLLB 1.3B | Không cần mạng, dịch khá hơn, chậm, tải ~5,5GB, cần ~6GB RAM |
| Lọc nhạc trước khi nhận diện | bật/tắt | Nhận diện chính xác hơn với video có nhạc, chậm hơn một chút |
| Phân giải | Gốc / 1080p / … | Thấp hơn = file nhỏ hơn, xuất nhanh hơn |
| FPS | Gốc / 24 / … | Để "Gốc" nếu không cần đổi |
| Chất lượng | CRF 18…35 | Cao = nét hơn nhưng file nặng hơn |
| Tốc độ (preset) | ultrafast … slow | Nhanh = xuất lẹ nhưng file nặng hơn; chậm = file nhẹ hơn |
| Audio | 96–320 kbps | 192 kbps là đủ cho giọng nói |
| Phụ đề | bật/tắt | Chèn phụ đề tiếng Việt vào video |
| Cắt video | bật/tắt | Chỉ lồng tiếng đoạn đã chọn, chạy nhanh hơn khi thử |

Triển khai gọn: helper `_hint_label(parent) -> QLabel` và một dict ánh xạ `userData → ghi chú` cho mỗi combo, nối `currentIndexChanged` để cập nhật. Không làm vỡ layout hiện tại (kiểm tra cửa sổ ở kích thước mặc định không bị tràn).

**Test:** `tests/test_gui_queue.py` (hoặc file mới) — đổi lựa chọn → nội dung hint đổi đúng (QApplication offscreen như test GUI hiện có).

---

## Phase 9 — Đóng gói, tải model, tài liệu

1. `spike/download_models.py`: thêm FSMN-VAD (mặc định), cờ `--vieneu` tải model VieNeu (không mặc định vì nặng). Nguồn mirror Release: chỉ thêm khi người dùng đã upload (hỏi).
2. `scripts/build_exe.ps1`, `scripts/build_exe_mac.sh`: copy `fsmn-vad/` (và `vieneu/` nếu người dùng muốn bundle) vào `resources/models/`.
3. `dubber.spec`: hiddenimports/collect cho `kaldi_native_fbank`, `yaml`, `vieneu` + deps (nếu bundle VieNeu); xem lại `excludes` (librosa/scipy) theo kết luận Gate 6. **Build thử Windows** (`pyinstaller dubber.spec --noconfirm`), chạy `dist\Dubber\Dubber.exe` dub 1 clip ngắn với FSMN + Piper; nếu bundle VieNeu thì thử thêm VieNeu. Báo dung lượng bundle trước/sau. (macOS: không build được trên máy này — ghi rõ "chưa kiểm tra trên Mac".)
4. `pyproject.toml`: extra `vieneu`, `separation` (audio-separator), `PyYAML`, `kaldi-native-fbank` vào deps chính (cần cho FSMN).
5. Cập nhật `CLAUDE.md` (bảng models, cấu trúc thư mục: `fsmn_vad.py`, `vieneu_tts.py`, `voice_library.py`, `factory.py`; trạng thái: thêm M9), `README.md` phần cài đặt.
6. Ghi "Ghi nhận ngoài phạm vi" trong báo cáo: bundle audio-separator, tự clone giọng từ video, diarization.

---

## Phase 10 — Nghiệm thu cuối và báo cáo

Chạy đủ:
1. `pytest tests/` xanh (kèm số test trước/sau).
2. `scripts/eval_asr.py` trên bộ video: bảng so sánh baseline vs cấu hình mới (số câu, câu không kết thúc bằng dấu câu, câu > 8s, RTF).
3. Dub end-to-end 1 video qua GUI với: (a) FSMN + vocals + Groq + Piper; (b) FSMN + vocals + NLLB 1.3B + VieNeu (giọng có sẵn); (c) VieNeu với 1 giọng clone vừa tạo — **tắt/mở lại app**, giọng clone vẫn còn và chọn được ngay.
4. Kiểm tra cache: chạy lại cùng video không đổi gì → không ASR/dịch lại; đổi bộ dịch → chỉ dịch lại.

Báo cáo cuối (in ra cho người dùng, tiếng Việt): việc đã làm theo phase, số liệu các Gate, những chỗ lệch so với plan và lý do, việc còn lại/cần người dùng quyết (upload Release, bundle VieNeu/audio-separator, xóa cache NLLB 600M, kiểm tra trên Mac), lệnh để chạy thử.

---

## Phụ lục A — Quyết định mặc định khi chưa hỏi được người dùng

| Vấn đề | Mặc định |
|---|---|
| VAD mặc định | FSMN (Silero fallback) — trừ khi Gate 4 cho thấy tệ hơn rõ rệt |
| Lọc nhạc trước ASR | Bật nếu MDX-Net có sẵn; không thì dùng audio gốc |
| Bộ đọc mặc định | Piper (VieNeu là tùy chọn) |
| VieNeu trong bản .exe | Chưa bundle cho tới khi người dùng đồng ý (dung lượng) |
| Mirror model lên Release | Không tự upload; tải từ nguồn gốc |
| Lộ lựa chọn VAD trên GUI | Không (chỉ CLI `--vad`) |

## Phụ lục B — Rủi ro chính

| Rủi ro | Cách xử lý |
|---|---|
| VieNeu chậm trên CPU | Gate 6 đo RTF; ghi chú GUI "xử lý lâu hơn"; hiện % tiến độ |
| VieNeu không chỉnh được tốc độ → câu tràn | `supports_rate=False` → atempo ≤1.3×; theo dõi số câu `pushed` |
| Deps VieNeu nặng (gradio, librosa/scipy) | Thử `--no-deps`; extra riêng; hỏi trước khi đưa vào bundle |
| Port FSMN sai → VAD hỏng | So khớp đầu ra với `funasr_onnx` gốc trên 1 file (chạy trong venv thử riêng có numpy 1.26) |
| Đổi thứ tự tách nhạc làm chậm | Có cache, `run_m5` tái dùng; tùy chọn tắt trong GUI |
| Cache cũ gây kết quả sai | Phase 1 làm trước tiên |
| NLLB 1.3B thiếu RAM | Bắt lỗi load, thông báo rõ, gợi ý MarianMT/Groq |
