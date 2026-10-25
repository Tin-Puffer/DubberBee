# CLAUDE.md — Hướng dẫn cho Claude agent

Đây là project **Dubber Bee** (package `dubber`): phần mềm dub video tự động Trung → Việt chạy trên desktop.
Pipeline: video → (tách nhạc) → ASR (sherpa-onnx SenseVoice + FSMN-VAD) → dịch (Groq/MarianMT/NLLB 1.3B)
→ TTS (piper hoặc VieNeu-TTS) → render (FFmpeg).

---

## Môi trường

- **Python**: 3.12
- **GUI**: PySide6
- **Venv**: `.venv/` (không commit)
- **API keys**: translator chỉ đọc từ `os.environ`. Khóa Groq nhập trong GUI được lưu trong kho
  mật khẩu hệ điều hành (`dubber/core/utils/api_keys.py`, `keyring`) và nạp vào `os.environ`
  khi khởi động — TUYỆT ĐỐI không ghi khóa vào code, file (kể cả settings.json), log hay file kết quả
  - `GROQ_API_KEY` — dịch online
  - `GEMINI_API_KEY` — (dự phòng, hiện chưa dùng chính)

---

## Cài đặt trên máy mới

Một lệnh: cài phụ thuộc → venv → thư viện → models → chạy test.

```bash
# Mac (cần Homebrew)
bash scripts/setup_mac.sh --all            # --all: mọi model (16 giọng piper + VieNeu + tách nhạc, ~2GB)

# Windows (PowerShell)
powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -All
```

- FFmpeg **phải có libass** (filter `ass`) để đốt phụ đề: Mac dùng `ffmpeg-full`
  (bản `ffmpeg` thường của Homebrew thiếu libass), Windows dùng Gyan full build.
- Models tải từ GitHub Release `models-v1` của repo **public riêng cho bản phát hành**
  (github.com/Tin-Puffer/DubberBee-releases, ~1,7GB: SenseVoice, FSMN/Silero VAD, 16 giọng piper,
  gói VieNeu, UVR MDX-Net), lỗi thì tải từ nguồn gốc (k2-fsa, ModelScope, HuggingFace,
  audio-separator). Tên repo đặt **một chỗ**: `RELEASES_REPO` trong `dubber/__init__.py`
  (mirror models + tự cập nhật đều đọc từ đây; repo code có thể để private).
- API key — Mac: thêm vào `~/.zshrc`; Windows: `setx GROQ_API_KEY "gsk_..."`
  ```bash
  export GROQ_API_KEY="gsk_..."
  export GEMINI_API_KEY="AI..."   # dự phòng
  ```
- Cài tay: `pip install -e ".[dev]"` (GUI, ASR, TTS, Groq, phụ đề) hoặc `".[dev,offline,vieneu]"`
  (+ dịch offline torch/transformers ~1-2GB, tách nhạc `separation`, VieNeu-TTS),
  rồi `python spike/download_models.py`.
- Thư viện phụ đề (có trong `dependencies`, `dubber.spec` đóng gói sẵn): `Pillow` (đo độ rộng chữ
  theo font thật → sub 1 dòng, vùng mờ tự động), `pyvi` (tách từ tiếng Việt → không cắt dòng giữa
  một từ; chạy không cần sklearn). Venv cũ: chạy lại `pip install -e ".[dev,offline,vieneu]"`
  (script build Mac/Windows tự cài phần còn thiếu). Mac Intel không cài `pyvi` (không có wheel
  `python-crfsuite`) → cắt câu dùng danh sách từ ghép có sẵn; Mac Apple Silicon (M1…) có đủ.

---

## Chạy từ source

```bash
source .venv/bin/activate
python -m dubber
```

---

## Cấu trúc thư mục

```
dubber/
├── __main__.py              # entry point
├── assets/                  # fonts/ (phụ đề), logo/ (logo.svg → logo_*.png, icon.icns, icon.ico)
├── app/
│   ├── gui.py               # PySide6 GUI, PipelineWorker thread; video chạy xong phần dịch →
│   │                        # video sau cần người dùng (dịch AI thủ công / duyệt) được chuẩn bị
│   │                        # nền (prepare=True) tới popup của nó; popup hiện lần lượt từng cái
│   ├── tasks.py             # TaskRunner + BusyBar: việc chậm chạy nền, có thanh loading
│   ├── review_dialog.py     # duyệt/sửa bản dịch trước TTS, copy cho AI / dán kết quả AI
│   ├── queue_card.py        # thẻ video trong hàng đợi (trạng thái + nút theo trạng thái)
│   ├── progress.py          # % tổng + thời gian còn lại (trừ lúc chờ người dùng)
│   ├── wait_dialogs.py      # hỏi khi Groq giới hạn + mở file/thư mục
│   ├── settings_dialog.py   # ⚙ Cài đặt: Groq API key (kiểm tra + đèn), thông báo, prompt AI, cache,
│   │                        # cập nhật (phiên bản, tự kiểm tra khi mở, nút kiểm tra)
│   ├── update_dialog.py     # tải models lần đầu (trước cửa sổ chính) + hộp thoại cập nhật app
│   ├── prompt_dialog.py     # soạn prompt AI (sửa bản dịch / TikTok): lưu, khôi phục mặc định
│   ├── preview.py           # khung hình + phụ đề mẫu (gần đúng) trong "Thông tin video gốc"
│   ├── notify.py            # thông báo hệ thống + Dock khi xong / lỗi / cần người dùng
│   ├── mix_preview.py       # 🎧 Nghe thử (tab Âm thanh): phát 20s đã trộn theo thanh âm lượng,
│   │                        # kéo thanh nghe ngay (1 luồng QAudioSink), vạch đo + cảnh báo nền lấn giọng
│   ├── manual_dialog.py     # dịch thủ công: tiếng Trung + giờ, sao chép cho AI, dán + kiểm tra kết quả
│   └── voice_dialog.py      # hộp thoại clone giọng VieNeu
└── core/
    ├── pipeline.py          # run_m5() — điều phối toàn bộ pipeline
    ├── model_setup.py       # danh sách models + tải lần đầu vào user-data (mirror models-v1 → nguồn gốc,
    │                        # sha256, tải tiếp khi đứt); app đóng gói KHÔNG kèm models
    ├── updater.py           # tự cập nhật: GitHub Releases vX.Y.Z có Dubber-macos/windows.zip → tải,
    │                        # giải nén cạnh app, script đợi app thoát → đổi thư mục → mở lại
    ├── asr/
    │   ├── sensevoice_sherpa.py # sherpa-onnx SenseVoice; VAD FSMN (mặc định) / Silero
    │   ├── fsmn_vad.py      # FSMN-VAD trên onnxruntime (port từ funasr-onnx, MIT)
    │   ├── _fsmn_e2e_vad.py # máy trạng thái FSMN-VAD, giữ nguyên văn upstream
    │   ├── postprocess.py   # tách câu theo mốc từng chữ ("timestamps") / "legacy"
    │   └── model_manager.py # tìm model: bundle → user-data → spike/models
    ├── translate/
    │   ├── base.py          # interface Translator
    │   ├── groq_translator.py   # Groq API (online)
    │   ├── local_translator.py  # MarianMT / NLLB 1.3B / AutoTranslator
    │   ├── normalize_vi.py  # chuẩn hóa bản dịch (viết hoa, dấu câu, tên riêng Groq)
    │   ├── review.py        # câu cần chú ý, định dạng copy/dán kết quả AI
    │   ├── manual.py        # dịch thủ công "ai_manual": tiếng Trung + giờ → AI gộp/tách câu, tự đặt giờ
    │   │                    # → parse "[mm:ss.cc → mm:ss.cc] câu" thay hẳn cách chia câu của ASR;
    │   │                    # bảng duyệt (mọi bộ dịch) cũng nhận dạng này qua "Copy chỉ tiếng Trung"
    │   ├── tiktok.py        # prompt TikTok + lời thoại → <video>_vi.txt cạnh video xuất
    │   ├── prompts.py       # prompt AI người dùng sửa được (settings.json; mặc định / reset)
    │   └── gemini.py        # Gemini (dự phòng)
    ├── tts/
    │   ├── factory.py       # make_tts_engine("piper" | "vieneu", voice, tts_dir)
    │   ├── piper_tts.py     # piper TTS tiếng Việt
    │   ├── vieneu_tts.py    # VieNeu-TTS v3 Turbo (ONNX/CPU, 48 kHz, không chỉnh tốc độ)
    │   └── voice_library.py # giọng VieNeu có sẵn + giọng clone (user-data/voices/vieneu)
    ├── audio/
    │   ├── fit.py           # TimingFitter: xếp câu TTS không đè nhau, nghỉ theo dấu câu, cắt im lặng,
    │   │                    # mọi câu về cùng độ to −14 dBFS (piper/VieNeu lệch ~7 dB) + limiter mềm,
    │   │                    # tăng tốc đều ≤1,15× (1 câu ≤1,3×), trễ ≤1,5s; câu quá dài → Groq rút gọn
    │   ├── mixer.py         # build_dub_wav(): ghép dub.wav đúng sample rate của giọng (không resample)
    │   ├── preview_mix.py   # Nghe thử: cắt 20s các track (cache job / tách riêng đoạn / câu mẫu TTS),
    │   │                    # trộn đúng quy tắc run_m5 (nhạc = tiếng Trung cùng mức → âm gốc…)
    │   ├── music.py         # "Nhạc của bạn": file chép vào user-data/music, lặp + nhỏ dần cuối khi render
    │   └── separate.py      # tách nhạc nền (audio-separator, hoặc FFmpeg fallback); chất lượng = overlap
    │                        # theo từng video (0,1/0,25/0,5 mặc định/0,75), đổi mức chỉ tách lại, giữ ASR/dịch;
    │                        # hàng đợi tách trước video kế khi video đang chạy đã dịch xong
    ├── video/
    │   ├── subtitles.py     # build_ass(): cắt câu thành dòng 1 dòng, viền/màu chữ
    │   ├── text_metrics.py  # đo chữ như libass (Pillow + bảng OS/2 của font)
    │   ├── logo.py          # logo kênh: ảnh chép vào user-data/logos (PNG), cỡ % rộng, góc / tự chọn
    │   └── render.py        # render_final() — (lật ngang) + làm mờ + logo + subtitle + audio (FFmpeg);
    │                        # dub_wav=None = chỉ phụ đề (không giọng Việt), không âm thanh nào = -an
    │                        # portrait=True: khung dọc 9:16 (1080×1920 / theo độ phân giải), hình đã có
    │                        # phụ đề + logo đặt giữa, nền là chính video phóng to + làm mờ;
    │                        # brightness / bg_brightness: độ sáng hình / nền mờ (%, eq = % / 200)
    └── utils/
        ├── ffmpeg_locator.py # tìm ffmpeg: resources/ → PATH
        ├── download.py      # tải HTTP: tải tiếp (.part + Range), tiến trình, hủy, sha256
        ├── gpu.py           # gpu_lock: MPS/CoreML chỉ 1 luồng (tách nhạc ↔ dịch offline), 2 luồng = crash Metal
        └── job_cache.py     # chữ ký *.sig.json: chỉ tái dùng asr/translate khi cấu hình khớp
```

---

## Models cần có

Đặt tại `spike/models/` (dev) hoặc tải tự động qua `download_models.py`:

| Model | Kích thước | Dùng cho |
|---|---|---|
| `sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17/` | ~229MB | ASR |
| `fsmn-vad/` (`model_quant.onnx`, `config.yaml`, `am.mvn`) | ~0,5MB | VAD mặc định (ModelScope `iic/speech_fsmn_vad_zh-cn-16k-common-onnx`) |
| `silero_vad.onnx` | nhỏ | VAD dự phòng |
| `piper/vi_VN-thanh_nien_tu_tin-medium.onnx` + `.json` | ~60MB | TTS mặc định (HF: vongocanhthi/acut-piper-vietnamese) |
| `piper/*.onnx` khác (15 giọng) | ~60MB/giọng | TTS — `download_models.py --all-voices` |
| HuggingFace MarianMT (tải lần đầu khi chọn Local) | ~300MB | Dịch offline |
| HuggingFace NLLB-200 distilled 1.3B (ghim commit safetensors) | ~5,5GB | Dịch offline chất lượng hơn |
| `vieneu/` (VieNeu-TTS v3 Turbo + codec MOSS, file thật — không dùng cache HF) | ~610MB | TTS VieNeu — `download_models.py --vieneu` hoặc tự tải lần đầu vào user-data |

---

## Build exe/app

Chạy sau khi setup xong. Script tự copy FFmpeg vào `resources/` rồi chạy PyInstaller.
**Không đóng gói models** (zip Mac ~530MB thay vì 1,9GB): lần đầu mở, app tải ~1,8GB models từ
Release `models-v1` vào thư mục user-data — giữ nguyên qua các lần cập nhật.

```bash
# Mac → dist/Dubber.app + dist/Dubber-macos.zip
bash scripts/build_exe_mac.sh
```
```powershell
# Windows → dist\Dubber\Dubber.exe + dist\Dubber-windows.zip
powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
```

Không build chéo được: bản Windows build trên GitHub Actions (`.github/workflows/build-windows.yml`,
chạy khi push nhánh `main-windown-edition`) → artifact `Dubber-windows` + đính vào Release
`RELEASE_TAG` của repo `DubberBee-releases`.

Mac: app ký ad-hoc, chưa notarize → máy khác mở lần đầu bằng chuột phải → Open.

**Phát hành bản mới (để app tự cập nhật):** tăng `__version__` (`dubber/__init__.py`, Info.plist đọc
từ đây) + `pyproject.toml`, tạo Release tag `vX.Y.Z` **trong repo `Tin-Puffer/DubberBee-releases`**
(không draft / pre-release), đính đúng tên `Dubber-macos.zip` và `Dubber-windows.zip`
(`RELEASE_TAG` trong workflow; CI đẩy lên đó bằng secret `RELEASES_TOKEN`). App đang chạy thấy tag
mới hơn → hỏi người dùng, ghi chú Release hiện trong popup. Đổi models → Release `models-v2` +
sửa `MODELS_MIRROR_URL`.

---

## Những điều quan trọng khi chỉnh sửa

1. **Không bao giờ** log hoặc print API key — key chỉ được đọc qua `os.environ`
2. `render_final()` nhận `scale`, `fps`, `crf`, `encoder_preset`, `audio_bitrate` — không hardcode
3. Khi chạy từ `.exe` (`sys.frozen = True`), models nằm ở thư mục user-data (`_user_models_dir()`,
   tải lần đầu); `sys._MEIPASS/resources/models/` chỉ còn được dò nếu bản build có kèm
4. `AutoTranslator` tự fallback Groq → Local; nếu cả hai fail thì raise RuntimeError rõ ràng
5. FFmpeg được tìm theo thứ tự: `resources/ffmpeg[.exe]` → Homebrew `ffmpeg-full` (macOS) → PATH

---

## Trạng thái hiện tại (cập nhật 2026-10-23)

- [x] M1 ASR (sherpa-onnx SenseVoice)
- [x] M2 Dịch (Groq online + MarianMT/NLLB offline)
- [x] M3 TTS (piper tiếng Việt)
- [x] M4 Tách nhạc nền (audio-separator + FFmpeg fallback)
- [x] M5 Render video (FFmpeg + ASS subtitle)
- [x] M6 GUI PySide6
- [x] M7 Tùy chỉnh xuất video (resolution, FPS, CRF, preset, audio bitrate)
- [x] M8 PyInstaller bundle (Windows, ~2GB full với torch+transformers)
- [x] M8 Mac build script (`scripts/build_exe_mac.sh`)
- [x] Hàng đợi nhiều video, cấu hình riêng từng video, chọn thư mục lưu
- [x] M9 Tách câu theo mốc từng chữ, FSMN-VAD (Silero dự phòng), ASR trên giọng đã tách nhạc,
      NLLB 1.3B, bộ đọc VieNeu-TTS + thư viện giọng clone, ghi chú cho mọi lựa chọn GUI
      (bản .app đóng gói đủ VieNeu + audio-separator + model, chạy offline: ~3,7GB, zip ~2,1GB)
- [x] M10 Phụ đề (`docs/plan-m10-subtitle.md`): chuẩn hóa bản dịch, cắt câu đúng ý, sub 1 dòng,
      che sub gốc Tắt/Tự động/Thủ công, viền + màu chữ; thanh loading khi nạp file, video đang
      chạy chỉ khóa sửa (vẫn xem được cấu hình)
- [x] M11 Duyệt bản dịch: checkbox "Bỏ qua" (mặc định bật = chạy thẳng); bỏ tick thì dịch xong
      dừng lại mở bảng sửa từng câu, nghe gốc/đọc thử, sao chép cho chatbot + dán kết quả;
      bản sửa ghi đè `translations/<bộ dịch>.json` nên chạy lại dùng tiếp
- [x] UI: hàng đợi dạng thẻ (chọn nhiều, kéo đổi thứ tự, chuột phải, nhân bản), tiến trình % +
      thời gian còn lại, thông báo, ⚙ Cài đặt, ảnh xem trước phụ đề, nhớ cửa sổ/tab/hàng đợi
- [x] Xuất kèm `<tên>_vi.txt` cạnh `<tên>_vi.mp4`: prompt ChatGPT (tiêu đề + mô tả + 5 hashtag + chữ ảnh bìa + bình luận ghim) đã ghép lời thoại
- [x] Prompt AI (sửa bản dịch, TikTok) sửa được trong ⚙ Cài đặt, lưu `settings.json`, có ↺ Mặc định
- [x] Bỏ dịch thủ công (cấu hình cũ `translator: manual` chạy như `auto`); bỏ mọi tự dọn cache
      (chỉ nút "Dọn cache"); lăn chuột không đổi giá trị ô chọn / ô số / thanh trượt
- [x] Mỗi video một popup riêng: khi video đang chạy đã dịch xong, video chờ chạy cần dán bản
      dịch AI / duyệt được tách nhạc + nhận dạng (+ dịch) nền và hỏi ngay (từng popup một);
      câu trả lời lưu cache, tới lượt chạy không hỏi lại (`translations/<bộ dịch>.reviewed`)
- [x] Tên hiển thị **Dubber Bee** + logo ong (`dubber/assets/logo/`); thư mục dữ liệu, service
      keyring `Dubber`, tên file build `Dubber.app`/`Dubber.exe` giữ nguyên
- [x] Bản đóng gói không kèm models (tải lần đầu vào user-data) + tự cập nhật từ GitHub Releases
      (⚙ Cài đặt → Cập nhật); gỡ cài đặt (Uninstall / xóa dữ liệu) để sau

---

## Lệnh thường dùng

```bash
# Chạy app
python -m dubber

# Chạy test (nếu có)
pytest tests/

# Build lại exe (Windows)
pyinstaller dubber.spec --noconfirm

# Xem log chi tiết khi chạy
python -m dubber --log-level DEBUG   # nếu có flag này
```
