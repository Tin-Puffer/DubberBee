# Dubber Bee — Tài liệu kỹ thuật

Dubber Bee (package Python `dubber`) là ứng dụng desktop (macOS / Windows) lồng tiếng tự động video **tiếng Trung → tiếng Việt**,
chạy chủ yếu **offline trên máy** (trừ dịch Groq). Tài liệu này mô tả kiến trúc, công nghệ,
dữ liệu, cách build/test và những quyết định kỹ thuật quan trọng.

Hướng dẫn cho người dùng cuối: xem [HUONG_DAN_SU_DUNG.md](HUONG_DAN_SU_DUNG.md).

---

## 1. Tổng quan pipeline

```
video ─► (cắt đoạn) ─► tách nhạc MDX-Net ─► VAD (FSMN / Silero) ─► ASR SenseVoice
      ─► tách câu theo mốc từng chữ ─► dịch (Groq / MarianMT / NLLB) ─► (duyệt bản dịch)
      ─► TTS (Piper / VieNeu-TTS) ─► căn thời gian (TimingFitter) ─► dub.wav
      ─► phụ đề ASS + làm mờ ─► trộn âm (giọng Việt + nhạc nền + tiếng Trung) ─► render FFmpeg
```

| Bước | Hàm | Đầu ra (trong thư mục job) |
|---|---|---|
| M1 — nhận diện | `Pipeline.run_m1` | `audio.wav`, `audio_vocals_16k.wav`, `separated/`, `asr.json` |
| M2 — dịch | `Pipeline.run_m2` | `translate.json`, `translations/<bộ dịch>.json` |
| M3 — đọc tiếng Việt | `Pipeline.run_m3` | `tts/*.wav`, `fit.json`, `dub.wav` |
| M4 — ghép tiếng | `Pipeline.run_m4` | `dubbed.mp4` |
| M5 — xuất video | `Pipeline.run_m5` | `subs.ass`, `background_mixed.wav`, `output.mp4` |

Các bước gọi lồng nhau `run_m5 → run_m4 → run_m3 → run_m2 → run_m1`; mỗi bước tái dùng kết
quả của bước trước nếu còn hợp lệ (xem §5 Cache).

---

## 2. Công nghệ

| Thành phần | Công nghệ | Ghi chú |
|---|---|---|
| Ngôn ngữ | Python 3.12 | venv `.venv/` |
| Giao diện | PySide6 (Qt 6) | `dubber/app/` |
| ASR | **SenseVoice-Small int8** qua **sherpa-onnx** | trả về chữ + mốc thời gian từng ký tự |
| VAD | **FSMN-VAD** (FunASR, ModelScope) trên onnxruntime; **Silero VAD** (sherpa-onnx) dự phòng | |
| Tách nhạc | **audio-separator** — model `UVR-MDX-NET-Inst_HQ_5.onnx` | dự phòng: lọc FFmpeg (kém) |
| Dịch online | **Groq API** — `qwen/qwen3.8-27b`, dự phòng `openai/gpt-oss-20b` | khóa nhập trong GUI (Keychain) hoặc `GROQ_API_KEY` |
| Dịch offline | **MarianMT** `Helsinki-NLP/opus-mt-zh-vi`, **NLLB-200 distilled 1.3B** (transformers + torch, GPU MPS/CUDA nếu có) | |
| TTS | **Piper** (16 giọng tiếng Việt) và **VieNeu-TTS v3 Turbo** (ONNX/CPU, 48 kHz, clone giọng) | |
| Xử lý video/âm thanh | **FFmpeg có libass** (macOS: `ffmpeg-full`; Windows: Gyan full build) | mã hóa: `h264_videotoolbox` / `h264_nvenc` / `libx264` |
| Đóng gói | PyInstaller (`dubber.spec`) | macOS `.app`, Windows `.exe` |
| Kiểm thử | pytest (~300 test) | |

---

## 3. Cấu trúc mã nguồn

```
dubber/
├── __main__.py                 CLI + chọn GUI (python -m dubber)
├── app/
│   ├── gui.py                  Cửa sổ chính, OptionsPanel, ExportPanel, OutputPanel, PipelineWorker,
│   │                           hàng đợi (thẻ, chọn nhiều, kéo đổi thứ tự, menu chuột phải), lưu /
│   │                           khôi phục cửa sổ + tab + hàng đợi, chặn lăn chuột đổi giá trị
│   ├── queue_card.py           Thẻ video: trạng thái + nút theo trạng thái
│   ├── progress.py             RunProgress: % tổng (trọng số từng bước), thời gian còn lại
│   ├── notify.py               Thông báo hệ thống + Dock khi xong / lỗi / cần người dùng
│   ├── preview.py              Khung hình + phụ đề mẫu (Qt, gần đúng) trong "Thông tin video gốc"
│   ├── settings_dialog.py      ⚙ Cài đặt: Groq API key (+ Kiểm tra), thông báo, prompt AI, cache
│   ├── prompt_dialog.py        Soạn prompt AI: lưu / khôi phục mặc định
│   ├── tasks.py                TaskRunner + BusyBar: việc chậm chạy nền
│   ├── trim_editor.py          Thanh cắt 2 đầu + ô nhập giây (tự sửa giá trị sai)
│   ├── layout_dialog.py        Xem trước phụ đề & chọn vùng làm mờ (render bằng FFmpeg/libass)
│   ├── voice_dialog.py         Hộp thoại clone giọng VieNeu
│   ├── wait_dialogs.py         Hộp thoại Groq bị giới hạn + mở file / thư mục
│   ├── review_dialog.py        Duyệt / sửa bản dịch trước TTS, sao chép / dán chatbot
│   ├── error_dialog.py         Popup lỗi (sao chép / mở file báo cáo)
│   ├── api_key_dialog.py       Nhập / kiểm tra / xóa Groq API key
│   └── workspace_dialog.py     Workspace: mở / đổi chỗ lưu cache, log
└── core/
    ├── pipeline.py             Điều phối M1–M5, cache theo chữ ký, hook duyệt bản dịch
    ├── asr/
    │   ├── sensevoice_sherpa.py   SenseVoice + VAD (FSMN/Silero), điểm hủy theo từng đoạn
    │   ├── fsmn_vad.py            FSMN-VAD (fbank kaldi-native-fbank → LFR → CMVN → ONNX)
    │   ├── _fsmn_e2e_vad.py       Máy trạng thái FSMN-VAD, giữ nguyên văn từ funasr-onnx (MIT)
    │   ├── postprocess.py         Tách câu "timestamps" / "legacy", gộp câu ngắn
    │   └── model_manager.py       Tìm / tải model (bundle → user-data → spike/models)
    ├── translate/
    │   ├── groq_translator.py     Groq + xử lý rate limit (hỏi người dùng)
    │   ├── local_translator.py    MarianMT, NLLB 1.3B, AutoTranslator (Groq → MarianMT)
    │   ├── review.py              Câu cần chú ý, định dạng sao chép / dán chatbot
    │   ├── tiktok.py              Prompt TikTok + lời thoại → <video>_vi.txt
    │   ├── prompts.py             Prompt AI người dùng sửa được (settings.json, mặc định / reset)
    │   └── base.py                Translator, TranslateSegment, make_segments
    ├── tts/
    │   ├── factory.py             make_tts_engine("piper" | "vieneu")
    │   ├── piper_tts.py           Piper
    │   ├── vieneu_tts.py          VieNeu v3 Turbo (ONNX, SDK dùng chung trong tiến trình)
    │   ├── voice_library.py       Giọng có sẵn + giọng clone
    │   └── demo.py                Câu nghe thử (cache)
    ├── audio/                     extract, separate (MDX-Net), fit (TimingFitter), mixer
    ├── video/                     render (FFmpeg), subtitles (ASS), fonts, info
    assets/                        fonts/ (phụ đề), logo/ (logo.svg + PNG + icon.icns / icon.ico)
    └── utils/
        ├── job_cache.py           Chữ ký *.sig.json
        ├── paths.py               Thư mục job / log, dọn cache theo yêu cầu
        ├── cancel.py              CancelToken, JobCancelled
        ├── error_report.py        Ghi báo cáo lỗi
        ├── settings.py            settings.json
        ├── api_keys.py            Groq API key trong keyring + ping_groq (1 request 1 token)
        └── ffmpeg_locator.py      Tìm ffmpeg (bundle → Homebrew ffmpeg-full → PATH)
scripts/   setup_mac.sh, setup_dev.ps1, build_exe_mac.sh, build_exe.ps1,
           eval_asr.py (đánh giá tách câu), fetch_uvr_model.py
spike/     download_models.py (tải model), thử nghiệm M1
tests/     pytest
```

---

## 4. Chi tiết từng khối

### 4.1 Nhận diện giọng nói (ASR)

1. **Trích âm thanh** mono 16 kHz (`audio.wav`).
2. **Lọc nhạc** (tùy chọn "Lọc nhạc trước khi nhận diện", mặc định bật): tách bằng MDX-Net →
   `audio_vocals_16k.wav`. Chỉ dùng khi tách bằng MDX-Net thật (không dùng bản lọc dự phòng
   FFmpeg) và độ dài lệch < 50 ms so với audio gốc; ngược lại dùng `audio.wav`.
3. **VAD** cắt thô các đoạn có tiếng nói:
   - **FSMN-VAD** (mặc định). Kết quả trùng khớp từng mili-giây với `funasr-onnx` gốc.
     Gộp đoạn cách nhau < 0,3 s (≤ 20 s), đoạn > 20 s cắt ở 100 ms yên lặng nhất.
   - **Silero VAD** dự phòng khi thiếu model FSMN; không có cả hai → cả file là một đoạn.
4. **SenseVoice** nhận diện từng đoạn → chữ + mốc thời gian từng ký tự (cả dấu câu).
5. **Tách câu** (`postprocess.py`, chế độ `timestamps`):
   - Cắt cứng tại `。！？…?!` (và "." trước khoảng trắng / cuối câu), hoặc khi hai chữ cách
     nhau > 0,8 s (mốc là thời điểm *bắt đầu* chữ nên 0,8 s ≈ 0,5 s im lặng thật).
   - Dấu phẩy chỉ dùng khi câu sắp vượt **8 s** (có tính phần đệm đầu/cuối).
   - Kết thúc câu = chữ cuối + ≤ 0,5 s (SenseVoice đôi khi phát dấu chấm muộn vài giây).
   - Gộp câu < 1 s vào câu kề nếu cách ≤ 0,3 s và không vượt 8 s.
   - Không có mốc thời gian → thuật toán `legacy` (chia theo số ký tự).

### 4.2 Dịch

| Lựa chọn | Lớp | Ghi chú |
|---|---|---|
| Tự động | `AutoTranslator` | Groq; lỗi / không có khóa → MarianMT; câu Groq bỏ sót → MarianMT |
| Groq | `GroqTranslator` | lô 30 câu, 3 câu ngữ cảnh, `max_chars` = thời lượng × 14 ký tự/s |
| MarianMT | `LocalTranslator` | ~300 MB |
| NLLB 1.3B | `NLLBTranslator` | ~5,5 GB, ghim commit safetensors (xem §8) |

**Groq bị giới hạn (HTTP 429):** model chính bị chặn → đổi sang model dự phòng; cả hai bị chặn
→ hỏi người dùng (GUI): *tiếp tục Groq* (đợi theo header `retry-after` /
`x-ratelimit-reset-*`, giới hạn 3–120 s, mặc định 20 s), *dịch phần còn lại bằng MarianMT*
(giữ các câu Groq đã dịch), hoặc *hủy*. Sau khi chọn tiếp tục, 4 request liên tiếp vẫn bị
chặn thì hỏi lại. SDK Groq tắt tự retry (`max_retries=0`). Không có GUI (CLI) → tự đợi và
thử lại tối đa 6 lần.

**Duyệt bản dịch** (`Pipeline.review_translation`, chỉ khi bỏ tích *Bỏ qua*): sau M2 worker hỏi
GUI (`ask("review")`), `ReviewDialog` trả về danh sách câu Việt; câu sửa được chuẩn hóa
(`normalize_text`) và ghi đè `translate.json` + `translations/<bộ dịch>.json`. Định dạng chatbot:
gửi `n. [thời lượng] 中文 → tiếng Việt  ⚠ …`, nhận `[n] câu` (cũng hiểu `n.` / `n)`; bỏ dòng
không số, khối ```), `review.parse_chatbot_reply`.

### 4.3 Đọc tiếng Việt (TTS)

- `TTSEngine` có `engine_id` và `supports_rate`. Khóa cache file giọng:
  `sha1(engine:voice + text + rate)`.
- **Piper:** chỉnh tốc độ bằng `length_scale`.
- **VieNeu v3 Turbo:** ONNX trên CPU (RTF ≈ 0,26 trên Mac M-series, RAM ≈ 1,6 GB), xuất
  48 kHz, **không chỉnh tốc độ** (`supports_rate=False`). Giọng: `"preset:<tên>"` (25 giọng
  có sẵn) hoặc `"clone:<id>"`. SDK được nạp **một lần mỗi tiến trình** (`shared_vieneu`), các
  lời gọi được tuần tự hóa bằng `sdk_lock`.
- **Clone giọng:** đoạn mẫu cắt ≤ 8 s (giới hạn của SDK), `encode_reference()` →
  `speaker_emb` (192 số) + `codes` → lưu `voice.json`; không bao giờ phải mã hóa lại.
- **TimingFitter** (`audio/fit.py`) đặt từng câu vào khe thời gian:
  vừa khe → giữ; tràn ≤ 8 % → cho tràn vào khoảng lặng; tràn nhiều → đọc lại nhanh hơn
  (chỉ engine có `supports_rate`) rồi `atempo` FFmpeg ≤ 1,3×; vẫn tràn → đẩy câu sau ≤ 0,5 s.

### 4.4 Phụ đề, làm mờ, xuất video

- Phụ đề ASS theo thời điểm giọng Việt thật trong `dub.wav` (`fit.json`), font nhúng sẵn
  (Arial, Be Vietnam Pro, Roboto, Montserrat, Nunito, Lexend, Noto Sans), cỡ 20–120 px theo
  khung 1080p.
- Sau dịch, `translate/normalize_vi.py` chuẩn hóa mọi bản dịch (khoảng trắng, viết hoa, bỏ dấu
  phẩy cuối; tên riêng theo danh sách `names` Groq trả về). Chạy lại nhiều lần vẫn cho cùng kết quả.
- Mỗi phụ đề là **một dòng** (`WrapStyle: 2`): `subtitles._split_to_chunks` cắt câu theo điểm phạt
  (dấu câu, từ nối, không kết thúc bằng từ yếu, không cắt trong từ — tách từ bằng `pyvi`), đo độ
  rộng thật bằng `video/text_metrics.py` (Pillow + bảng OS/2 của font, khớp libass) và tối đa
  `MAX_CUE_CHARS` ký tự. Chữ có viền (`BorderStyle 1`), màu chữ/viền/độ dày chọn trong GUI; tùy
  chọn hộp nền tối là một layer `Box` riêng.
- Che phụ đề gốc: tắt / tự động (`pipeline.compute_auto_blur`: dòng rộng nhất + lề, một vùng cố
  định cả video) / thủ công (vùng tự kéo).
- GUI: việc chậm (ffprobe, dựng khung xem trước, đọc/cắt audio clone, kiểm tra file dịch) chạy
  qua `app/tasks.py` (`TaskRunner` + `BusyBar`), không chặn luồng giao diện.
- Âm thanh: giọng Việt + nhạc nền + tiếng Trung. Hai mức bằng nhau → dùng thẳng âm gốc; khác
  nhau → tách MDX-Net và trộn riêng; chuẩn hóa `loudnorm I=-16`.
- Xuất video: độ phân giải, FPS, CRF, preset, bitrate audio đều là tham số của
  `render_final()` (không hardcode).

---

## 5. Cache & tái dùng kết quả

- **Thư mục job**: `<jobs>/<sha1(đường dẫn video)[:12]>/`. Chỉ được giữ khi **cùng file nguồn**
  (đường dẫn, kích thước, thời điểm sửa) **và cùng đoạn cắt** (`input.json`); khác → xóa sạch.
- **Chữ ký** (`*.sig.json`, `utils/job_cache.py`):
  - `asr.json`: hash audio đầu vào + VAD + chế độ tách câu + có lọc nhạc + tên model.
  - `translations/<bộ dịch>.json`: hash nội dung `asr.json` + bộ dịch → mỗi bộ dịch giữ một
    bản; đổi qua lại không phải dịch lại.
- TTS: mỗi câu một file theo khóa ở §4.3. Tách nhạc: `separated/` (đánh dấu `.ffmpeg_fallback`
  nếu là bản dự phòng).

---

## 6. Dữ liệu trên máy & dọn dẹp

| Dữ liệu | macOS mặc định | Windows mặc định |
|---|---|---|
| File tạm job | `~/Library/Application Support/dubber/jobs` | `%LOCALAPPDATA%\dubber\dubber\jobs` |
| Model VieNeu (tải) | `~/Library/Application Support/dubber/models/vieneu` | `…\dubber\dubber\models\vieneu` |
| Giọng clone | `~/Library/Application Support/dubber/voices/vieneu` | `…\dubber\dubber\voices\vieneu` |
| Câu nghe thử, model UVR | `~/Library/Caches/dubber/` | `%LOCALAPPDATA%\dubber\dubber\Cache` |
| Log, báo cáo lỗi | `~/Library/Logs/dubber/` (`dubber.log`, `errors/`) | `%LOCALAPPDATA%\dubber\dubber\Logs` |
| Cấu hình | `~/Library/Preferences/dubber/settings.json` | `%LOCALAPPDATA%\dubber\dubber\settings.json` |
| Khung hình xem trước | `~/Library/Caches/dubber/frames/` | `…\dubber\dubber\Cache\frames` |
| Groq API key | macOS Keychain (`Dubber` / `GROQ_API_KEY`) | Windows Credential Manager |
| Model HuggingFace (MarianMT, NLLB) | `~/.cache/huggingface/hub` | `%USERPROFILE%\.cache\huggingface\hub` |

- Thư mục **job** và **log** đổi được trong *⚙ Cài đặt → Workspace* (`settings.json`:
  `workspace_dir`, `logs_dir`). Thư mục người dùng chọn chỉ nhận thư mục con của Dubber
  (`Dubber workspace/jobs`, `Dubber logs`); dọn dẹp chỉ xóa thư mục job thật
  (`_is_job_dir`: tên 12 ký tự hex hoặc có `input.json`).
- **Không tự dọn:** app không tự xóa thư mục job hay file nào trong đó (kể cả `output.mp4`),
  cũng không xóa báo cáo lỗi cũ; chỉ nút **Dọn cache** (`clear_jobs`) xóa job.
  `dubber.log` xoay vòng 5 MB × 3.
- **Hủy video** xóa toàn bộ thư mục job của video đó.
- Các khóa khác trong `settings.json`: `output_dir`, `tts_engine` / `voice` (lần chọn cuối),
  `notify`, `review_prompt` / `tiktok_prompt` (chỉ lưu khi khác mặc định; `null` = mặc định),
  `window_geometry`, `options_tab`, `queue` (danh sách video: đường dẫn, cấu hình, trạng thái,
  file kết quả — video đang chạy lưu thành "chờ chạy", file nguồn không còn thì bỏ qua).
  Tên thư mục dữ liệu (`dubber`, `Dubber workspace`, `Dubber logs`) và service keyring `Dubber`
  **giữ nguyên** sau khi đổi tên app thành Dubber Bee để không mất dữ liệu cũ.

---

## 7. Luồng GUI, hủy, hỏi người dùng, báo lỗi

- `PipelineWorker` chạy trong `QThread`; các signal `step_started/progress/done`,
  `finished`, `error`, `error_report`, `cancelled`, `ask`.
- **Hủy** (`utils/cancel.py`): `CancelToken.check()` được gọi ở từng đoạn ASR, từng lô dịch
  (cả khi đang đợi rate limit), từng câu TTS, mỗi tick tiến độ tách nhạc / render (raise trong
  lúc render sẽ kill FFmpeg). Dừng sau ~1 s khi đang TTS/render; riêng bước tách nhạc chỉ dừng
  sau khi bước đó xong.
- **Hỏi người dùng** (`ask` / `answer`): worker phát `ask(kind, payload)` rồi chờ
  `threading.Event`; GUI hiện hộp thoại và gọi `answer()`. Hủy cũng giải phóng chờ.
  Dùng cho duyệt bản dịch và Groq rate limit.
- **Báo lỗi:** `utils/error_report.py` ghi `<logs>/errors/<thời gian>_<video>.txt` (thông báo,
  traceback, cấu hình job, môi trường, 150 dòng log cuối; **không** ghi biến môi trường) và GUI
  hiện popup không chặn (`error_dialog.py`).
- Callback từ thread nền vào GUI luôn nối vào **method của QObject** (để Qt chuyển về thread
  GUI) — không nối vào lambda/hàm thường.
- **Tiến trình:** `RunProgress` tính % tổng theo trọng số bước (tách nhạc 25 %, ASR 10 %, dịch
  10 %, TTS 35 %, ghép 5 %, render 15 %); bước trước bị bỏ qua / dùng cache tính là xong khi bước
  sau bắt đầu. Thời gian còn lại = đã chạy / % − đã chạy (hiện khi ≥ 3 % và ≥ 10 s). Thời gian
  chờ người dùng (`ask`) không tính. `QTimer` 1 s cập nhật thẻ + tiêu đề khung tiến trình.
- **Hàng đợi:** `QListWidget` (ExtendedSelection, InternalMove) + `QueueCard` qua
  `setItemWidget`; item giữ `id(job)` ở `Qt.UserRole` để sắp lại `_jobs` khi kéo thả
  (`rowsMoved`), card bị mất sau khi kéo được tạo lại.
- **Thông báo** (`notify.py`): chỉ khi cửa sổ không active; macOS `osascript display
  notification` (tham số qua `argv`, không ghép chuỗi script) + `QApplication.alert`.
- **Lăn chuột:** event filter toàn app chặn Wheel trên `QComboBox` / `QAbstractSpinBox` /
  `QAbstractSlider` (trừ thanh cuộn) và chuyển lên widget cha để trang vẫn cuộn.

---

## 8. Quyết định kỹ thuật & lý do

| Quyết định | Lý do |
|---|---|
| Không cài `funasr-onnx`, tự port FSMN-VAD | gói này ép `numpy<=1.26.4`, xung đột numpy 2.x của dự án |
| NLLB ghim commit `f9e6ff7f…` (`refs/pr/5`, safetensors) | repo chính chỉ có `.bin`; không ghim thì transformers tải cả `.bin` lẫn safetensors (2 × 5,5 GB) và không chạy được khi offline |
| Model VieNeu lưu file thật, không qua cache HuggingFace | onnxruntime 1.31 từ chối external data `.data` truy cập qua symlink của cache HF |
| Bọc `OnnxV3LiteEngine.__init__` khi nạp VieNeu | SDK không chuyển `codec_dir` xuống engine |
| Ngưỡng im lặng 0,8 s thay vì 0,6 s | mốc SenseVoice là thời điểm bắt đầu chữ; 0,6 s cắt nhầm giữa câu |
| ASR chỉ dùng stem MDX-Net, không dùng bản lọc FFmpeg | bản lọc band-pass làm ASR kém hơn audio gốc |
| Ô nhập giây tự đọc cả "12.5" và "12,5" | locale tiếng Việt dùng dấu phẩy, "12.5" bị hiểu thành 125 |
| VieNeu dùng extra đầy đủ (kéo gradio, librosa) | lựa chọn của chủ dự án; đường ONNX của app không dùng gradio |

**Đánh giá tách câu** (`scripts/eval_asr.py`, video thử 2 phút): baseline 30 câu / 10 câu không
kết thúc bằng dấu câu; mặc định hiện tại (FSMN + timestamps + lọc nhạc) 21 câu / 10; Silero +
timestamps + lọc nhạc 25 câu / 6. FSMN là mặc định theo lựa chọn của chủ dự án.

---

## 9. Cài đặt, chạy, test, build

```bash
# Lấy mã nguồn
git clone https://github.com/Tin-Puffer/DubberBee.git && cd DubberBee
# Cài (macOS) — Homebrew, venv, thư viện, mọi model (~2 GB), test
bash scripts/setup_mac.sh --all          # hoặc --all-voices / --vieneu
# Windows
powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -All

# Chạy
source .venv/bin/activate
python -m dubber                         # GUI
python -m dubber video.mp4 --translator groq --vad fsmn --tts vieneu --voice "preset:Mai Anh" -v

# Test
QT_QPA_PLATFORM=offscreen pytest tests/ -q
DUBBER_SLOW_TESTS=1 pytest -m slow       # test nạp model thật (NLLB, VieNeu)

# Đánh giá tách câu
python scripts/eval_asr.py video.mp4 --vad fsmn --split timestamps --vocals on

# Build
bash scripts/build_exe_mac.sh            # dist/Dubber.app + dist/Dubber-macos.zip (~3,7 GB / 2,1 GB)
.\scripts\build_exe.ps1                  # dist\Dubber\Dubber.exe + dist\Dubber-windows.zip
                                         # (hoặc GitHub Actions: .github/workflows/build-windows.yml)
```

**Model** được mirror ở Release `models-v1` của repo
(`MODELS_MIRROR_URL` trong `core/asr/model_manager.py`): SenseVoice (tar.bz2), FSMN-VAD
(`fsmn-vad-*`), Silero, 16 giọng Piper (`.onnx` + `.onnx.json`), VieNeu
(`vieneu-v3-turbo-onnx.tar`), UVR MDX-Net (`UVR-MDX-NET-Inst_HQ_5.onnx`, `uvr-*.json`),
`SHA256SUMS.txt`. Mỗi file thử mirror trước, lỗi thì tải từ nguồn gốc.

**Extras** (`pyproject.toml`): `dev` (pytest), `offline` (torch, transformers; kèm
`separation`), `separation` (audio-separator), `vieneu` (VieNeu-TTS).

**Tên & icon:** hiển thị "Dubber Bee" (`CFBundleName` / `CFBundleDisplayName`, tiêu đề cửa sổ,
thông báo); file build vẫn là `Dubber.app` / `Dubber.exe`. Icon lấy từ `dubber/assets/logo/`
(`icon.icns` / `icon.ico`); các `logo_*.png` được đóng gói để vẽ logo ở header và icon Dock.
Nguồn logo là `logo.svg` — sửa xong xuất lại PNG / icns / ico.

**Build** đóng gói: FFmpeg (macOS: `ffmpeg-full` + dylib qua `dylibbundler`), SenseVoice,
FSMN-VAD, Silero, giọng Piper, model VieNeu, model UVR + 3 file danh sách model (để tách nhạc
chạy offline), torch/transformers, audio-separator (+ torchvision vì `onnx2torch` cần, PIL,
pandas), VieNeu SDK. Loại trừ: gradio, torchaudio, công cụ dev. Bản build đã kiểm tra chạy
offline (`HF_HUB_OFFLINE=1`) trên macOS. PySide6 chỉ gom QtCore/Gui/Widgets/Multimedia
(trước đây `collect_all` kéo cả WebEngine/3D/QML, thừa ~450 MB).

---

## 10. Bảo mật & quy tắc

- Khóa Groq nhập trong GUI (`app/api_key_dialog.py`, `core/utils/api_keys.py`) được lưu trong
  **kho mật khẩu hệ điều hành** qua `keyring` (macOS Keychain, Windows Credential Manager;
  service `Dubber`, username `GROQ_API_KEY`) — không bao giờ trong file. Khi khởi động (GUI và
  CLI) khóa đã lưu được nạp vào `os.environ` của tiến trình; khóa lưu trong app ưu tiên hơn biến
  môi trường của shell (xóa khóa trong app → trả lại giá trị của shell). Translator vẫn chỉ đọc
  từ `os.environ`. UI chỉ hiện dạng che (`gsk_…abcd`); thông báo lỗi kiểm tra khóa không lặp lại
  khóa. Không ghi khóa vào code, log, báo cáo lỗi hay file kết quả. Test dùng kho giả trong bộ
  nhớ (`tests/conftest.py`), không đụng Keychain thật.
- Thư mục người dùng chọn: chỉ ghi vào thư mục con của Dubber Bee; chỉ xóa thư mục job thật.
- Model và giọng clone không bao giờ bị tự dọn.

---

## 11. Giấy phép thành phần chính

| Thành phần | Giấy phép |
|---|---|
| sherpa-onnx | Apache-2.0 |
| SenseVoice-Small (model) | Giấy phép model riêng của FunASR (xem repo modelscope/FunASR) |
| FSMN-VAD model (ModelScope) | Apache-2.0 |
| funasr-onnx (phần port / giữ nguyên văn) | MIT |
| VieNeu-TTS v3 Turbo, MOSS Audio Tokenizer | Apache-2.0 |
| audio-separator | MIT |
| NLLB-200 | CC-BY-NC-4.0 (phi thương mại) |
| MarianMT opus-mt-zh-vi | Apache-2.0 |
| piper-tts (gói Python) | **GPL-3.0-or-later** — cần lưu ý nếu phân phối bản đóng gói; từng giọng có giấy phép riêng |
| Font phụ đề | SIL OFL (`dubber/assets/font-licenses/`) |

Giấy phép trên đọc từ metadata gói / model card tại thời điểm viết; kiểm tra lại trước khi
phân phối thương mại.

---

## 12. Hạn chế đã biết / việc còn mở

- Bước tách nhạc không dừng giữa chừng khi hủy.
- Trên video thử, FSMN cho nhiều câu nối bằng dấu phẩy hơn Silero.
- NLLB 1.3B dịch kém Groq rõ rệt, có thể bịa nội dung ở câu ngắn.
- VieNeu không chỉnh được tốc độ → câu dài chỉ được tăng tốc bằng `atempo` ≤ 1,3×.
- Clone giọng dùng tối đa 8 s mẫu (giới hạn SDK); không fine-tune.
- Hộp thoại *Workspace*: trong bản build, dòng "Model" trỏ thư mục user-data
  (model thật nằm trong app).
- Bản Windows build trên GitHub Actions (test + khởi động thử), chưa chạy thử pipeline đầy đủ
  trên máy Windows thật.
- GUI không xử lý SIGTERM: vòng lặp sự kiện Qt chặn, nên handler tín hiệu của Python không
  chạy — `kill` / `pkill` không đóng được app (Cmd + Q thì được). Khắc phục được bằng cách đăng
  ký `signal.signal(SIGTERM, …)` gọi `QApplication.quit()` kèm một `QTimer` định kỳ để trình
  thông dịch Python có cơ hội xử lý tín hiệu.
