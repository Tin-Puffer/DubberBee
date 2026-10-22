# Kế hoạch xây dựng ứng dụng lồng tiếng video Trung → Việt (Windows + macOS)

*Bản 1.0 · 21/10/2026 · Tài liệu giao cho agent lập trình thực hiện*

## 1. Mục tiêu và phạm vi

Ứng dụng desktop nhận một video tiếng Trung (có hoặc không có phụ đề Trung cứng) và tạo ra video mới có: giọng đọc tiếng Việt do AI sinh (to hơn giọng gốc), phụ đề tiếng Việt đốt thẳng vào hình, và tùy chọn làm mờ vùng phụ đề cũ để phụ đề Việt đè lên.

**Phạm vi bản đầu (MVP):** kéo thả một file video → bấm một nút → nhận file MP4 đầu ra. Chạy trên Windows 10/11 và macOS (máy chính để thử là Mac chip Apple Silicon M1 Pro). Máy người dùng yếu nên không chạy LLM hay model nặng ở local: chỉ chạy local model nhỏ cho nhận dạng giọng nói, còn dịch dùng API miễn phí.

**Ngoài phạm vi bản đầu:** dán link tải video, hàng đợi nhiều video, màn hình sửa bản dịch, nhận diện nhiều người nói, OCR tự dò phụ đề, inpainting xóa sạch phụ đề cũ. Kiến trúc phải chừa chỗ cho các mục này.

## 2. Các quyết định đã chốt

| Hạng mục | Quyết định |
| --- | --- |
| Giao diện | PySide6 (Qt), một codebase Python. Người dùng không yêu cầu cụ thể, chỉ cần kéo thả file tiện (lựa chọn của người lập kế hoạch) |
| Nhập video | Kéo thả file từ máy. Chưa làm dán link và hàng đợi |
| Nhận dạng giọng nói (ASR) | Mặc định local: SenseVoice-Small (nhẹ, khoảng 250 MB bản q8). Tùy chọn trong Settings: Groq Whisper large-v3 (API miễn phí) |
| Dịch Trung → Việt | API miễn phí Gemini (Google AI Studio): mặc định Gemini 3.8 Flash, tự chuyển sang bản Flash-Lite khi hết quota. Thiết kế provider để đổi sang Claude/GPT sau này |
| Giọng đọc tiếng Việt | edge-tts. Một giọng cố định cho cả video, người dùng chọn nam (vi-VN-NamMinhNeural) hoặc nữ (vi-VN-HoaiMyNeural). Xác nhận lại tên giọng bằng `edge-tts --list-voices` |
| Phụ đề Việt | Người dùng tự chọn vùng hiển thị (kéo khung trên khung hình mẫu) và cỡ chữ. Đốt cứng vào video |
| Phụ đề Trung cũ | Không dùng OCR. Có tùy chọn bật/tắt “làm mờ nền” đúng vùng người dùng đã chọn, nên che luôn phụ đề Trung nếu có |
| Âm thanh gốc | Người dùng chọn trong Settings: (1) hạ âm lượng, mặc định; (2) tách giọng bằng Demucs và giữ nhạc nền, tùy chọn, nặng; (3) tắt hẳn |
| Quy trình | Tự động hoàn toàn, một lần bấm. Người dùng xem kết quả rồi nâng cấp sau (mục 12) |
| Đóng gói | PyInstaller, build riêng trên từng hệ điều hành, dùng cá nhân, chưa cần notarize (giả định của người lập kế hoạch, đổi được) |

## 3. Kiến trúc tổng thể

```
GUI (PySide6)
   │  signal/slot, job chạy ở worker thread
Pipeline Orchestrator ── cache từng bước theo thư mục job, hủy / chạy lại từ bước lỗi
   │
   ├─ AudioExtractor   (FFmpeg)
   ├─ ASREngine        ← SenseVoiceLocal | GroqWhisper
   ├─ Translator       ← GeminiTranslator | (sau này: Claude / GPT / Ollama)
   ├─ TTSEngine        ← EdgeTTS | (sau này: Azure / VietTTS)
   ├─ TimingFitter     (khớp thời lượng giọng Việt với khung thời gian)
   ├─ AudioMixer       ← chế độ: duck | demucs | mute
   ├─ SubtitleBuilder  (sinh file .ass)
   └─ VideoRenderer    (FFmpeg: blur + đốt ASS + ghép audio)
```

Nguyên tắc: mỗi engine là một interface (abstract class) với một hàm chính, chọn implementation qua config. Orchestrator không biết chi tiết provider. Mỗi bước ghi JSON hoặc file trung gian vào thư mục job để chạy lại từ bước lỗi mà không tốn lại quota API.

**Segment** (dataclass dùng xuyên suốt): `id, start, end` (giây), `src_text, vi_text, tts_path, tts_duration, final_start, final_end, speed`.

**Thư mục job:** `<user_data>/jobs/<hash_video>/` gồm `audio.wav, asr.json, translate.json, tts/, dub.wav, mix.wav, subs.ass, output.mp4`.

## 4. Pipeline chi tiết

### 4.1 Tách audio

`ffmpeg -i input -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav`. Giữ thêm bản audio gốc nguyên kênh cho mixer. Dùng ffprobe lấy thời lượng, fps, độ phân giải, kiểm tra có audio hay không (video không có tiếng thì báo lỗi rõ ràng).

### 4.2 Nhận dạng giọng nói (ASR)

- **Local (mặc định):** SenseVoice-Small kèm FSMN-VAD, ra danh sách đoạn `{start, end, text}` có dấu câu. Cần một spike (mốc M1) thử hai runtime: binary GGUF/llama.cpp trong releases của repo SenseVoice (có VAD sẵn, không cần Python) và sherpa-onnx. Chọn cái chạy ổn định trên cả Mac arm64 và Windows x64. Tránh kéo cả PyTorch vào bản đóng gói nếu không bắt buộc.
- **Hậu xử lý:** gộp đoạn quá ngắn (dưới 1 giây) vào đoạn kề, cắt đoạn quá dài (trên khoảng 12 giây) tại dấu câu, bỏ đoạn rỗng và các tag sự kiện âm thanh.
- **Groq Whisper (tùy chọn):** cắt audio thành chunk khoảng 10 phút, nén mono 16 kHz (mp3 hoặc opus) để mỗi file dưới giới hạn 25 MB của gói miễn phí. Gọi `whisper-large-v3` với `response_format=verbose_json` để lấy timestamp từng đoạn, cộng offset của chunk. Gói free giới hạn khoảng 20 request/phút, 7.200 giây audio/giờ, 28.800 giây/ngày: gặp lỗi 429 thì backoff và báo người dùng.

### 4.2b Kiểm thử nhanh chất lượng ASR

So sánh kết quả với transcript tự gõ của 3 clip thật (có nhạc nền, nhiều người nói, giọng địa phương) và ghi lại tỉ lệ lỗi ký tự. Nếu local quá tệ trên clip có nhạc nền, ghi nhận để nâng cấp sau bằng cách tách vocal bằng Demucs trước ASR.

### 4.3 Dịch (Gemini)

- Gửi theo lô 20–40 đoạn mỗi request dưới dạng JSON `[{id, zh, max_chars}]`, yêu cầu trả đúng `[{id, vi}]`, kèm 2–3 câu trước làm ngữ cảnh. Parse an toàn (bỏ hàng rào \`\`\`json nếu có), kiểm tra đủ id, thiếu thì gọi lại riêng phần thiếu.
- Prompt hệ thống: dịch tự nhiên như lời thoại, xưng hô nhất quán, tên riêng nhất quán (phiên âm Hán-Việt hoặc giữ nguyên, không đổi giữa chừng), không thêm giải thích, **dịch ngắn gọn để đọc vừa `max_chars`**. Công thức: `max_chars = thời_lượng_đoạn × CHARS_PER_SEC`, hằng số cấu hình, bắt đầu 14 ký tự/giây và hiệu chỉnh bằng thử nghiệm thực tế với edge-tts.
- **Quota:** free tier có giới hạn khác nhau theo model; model mạnh nhất có thể chỉ được vài chục request/ngày, bản Flash-Lite được nhiều hơn. Triển khai chuỗi fallback (3.8 Flash → Flash-Lite), bắt lỗi 429 / RESOURCE\_EXHAUSTED, backoff có jitter. Dùng SDK chính thức `google-genai`, đọc tài liệu hiện hành để xác nhận tên model và kiểu gọi (ID model và API có thể đã đổi).
- Chỉ gửi văn bản lên Gemini, không gửi audio hay video. UI phải cảnh báo rằng gói miễn phí của Google có thể dùng dữ liệu gửi lên để cải thiện model.

### 4.4 Giọng đọc (edge-tts)

- Mỗi đoạn một lần gọi, voice theo cài đặt, rate mặc định +0%. Giới hạn song song 2–4 (semaphore), retry 3 lần với backoff, cache file theo `hash(text, voice, rate)`.
- edge-tts là endpoint không chính thức của Microsoft, không công bố giới hạn và có thể bị đổi hoặc chặn bất cứ lúc nào. Bắt buộc bọc sau interface `TTSEngine`, báo lỗi rõ ràng, để thêm Azure TTS sau mà không sửa pipeline.
- Đổi mp3 nhận được sang wav mono để xử lý.

### 4.5 Khớp thời lượng (TimingFitter)

Đây là phần quyết định chất lượng. Với mỗi đoạn, khung thời gian `slot` là từ `start` đến `min(end + khoảng lặng cho phép, start của đoạn kế − 0,1 s)`. So `tts_duration` với `slot` theo thứ tự:

1. Vừa khung: đặt tại `start`.
2. Dài hơn không quá 8%: cho tràn vào khoảng lặng phía sau.
3. Dài hơn nữa: tăng tốc. Trước hết sinh lại bằng edge-tts với rate đến +25%, sau đó dùng `atempo` của FFmpeg, tối đa 1,3×.
4. Vẫn dài: gọi lại Gemini một lần để rút gọn câu (“còn tối đa N ký tự”), rồi TTS lại.
5. Vẫn dài: chấp nhận, đẩy đoạn kế muộn tối đa 0,5 s và ghi cảnh báo vào log.

Kết quả là `final_start` / `final_end` cho từng đoạn. Phụ đề dùng chính các mốc này để khớp với tiếng.

### 4.6 Trộn âm thanh

- Dựng track lồng tiếng: timeline im lặng dài bằng video, đặt từng đoạn tại `final_start` (FFmpeg `adelay` + `amix`, hoặc numpy + soundfile, ưu tiên cách ít phụ thuộc).
- Chuẩn hóa giọng Việt về khoảng −16 LUFS (`loudnorm`) để to hơn giọng gốc.
- **Chế độ audio gốc:** `duck` (mặc định): giọng gốc nhân 0,15, hoặc `sidechaincompress` hạ khi có giọng Việt. `demucs`: chạy tách hai stem, dùng stem không có vocal làm nền ở mức 0,8–1,0. `mute`: bỏ audio gốc.
- Demucs là tùy chọn tải thêm ở lần dùng đầu (cảnh báo dung lượng và thời gian, chạy chậm trên máy yếu), import lười, đóng gói tách riêng.
- Cuối chuỗi dùng `alimiter` chống méo, xuất `mix.wav`.

### 4.7 Phụ đề và làm mờ

- `SubtitleBuilder` sinh file `.ass`: `PlayResX/Y` bằng độ phân giải video. Chữ Việt tối đa 2 dòng (khoảng 40 ký tự/dòng), ngắt dòng theo từ. Dùng font hỗ trợ đủ dấu tiếng Việt đi kèm app (ví dụ Noto Sans hoặc Be Vietnam Pro, giấy phép mở), viền đen, đổ bóng, tùy chọn nền hộp.
- Khung hiển thị và cỡ chữ do người dùng chọn. Lưu tọa độ khung theo tỉ lệ 0–1 so với khung hình để áp dụng cho mọi độ phân giải.
- **Làm mờ (nếu bật):** trong `filter_complex`, tách luồng, crop đúng vùng đã chọn, `boxblur` mạnh (cho chỉnh độ mờ), có thể phủ thêm lớp `drawbox` đen bán trong suốt, rồi overlay lại, sau đó đốt ASS:

```
[0:v]split[a][b];[b]crop=W:H:X:Y,boxblur=25:3[bl];[a][bl]overlay=X:Y,ass=subs.ass:fontsdir=fonts[v]
```

- Lưu ý: blur chỉ làm mờ, chữ Trung vẫn có thể lờ mờ nếu mức mờ thấp, nên cho chỉnh độ mờ và độ tối lớp phủ. Vùng mờ phủ suốt video, kể cả lúc không có phụ đề.

### 4.8 Xuất video

`-map "[v]" -map mix.wav -c:a aac -b:a 192k -movflags +faststart`. Chọn encoder theo máy: `h264_videotoolbox` (macOS), `h264_nvenc` (NVIDIA), mặc định `libx264 -crf 20 -preset medium`. Phát hiện bằng `ffmpeg -encoders` và thử chạy thật, lỗi thì quay về libx264. Giữ nguyên độ phân giải và fps gốc. Tên file: `<tên gốc>_vi.mp4` cạnh file gốc (đổi được).

## 5. Giao diện (PySide6)

- Vùng kéo thả video (kèm nút Chọn file), hiển thị tên, thời lượng, độ phân giải.
- Khung hình mẫu lấy tại giữa video: vẽ khung chữ nhật kéo/đổi kích thước để chọn vùng phụ đề Việt, hiển thị dòng phụ đề mẫu theo cỡ chữ đang chọn (xem trước).
- Cài đặt: cỡ chữ; checkbox “Làm mờ nền vùng phụ đề” kèm độ mờ và độ tối lớp phủ; giọng đọc (Nam/Nữ) và tốc độ; chế độ audio gốc (Hạ âm lượng / Tách giọng Demucs / Tắt hẳn) và mức âm lượng; engine ASR (Local / Groq); ô nhập Gemini API key (lưu bằng keyring); thư mục xuất.
- Nút Bắt đầu / Hủy, thanh tiến độ theo bước, ô log, nút Mở thư mục kết quả.
- Mọi tác vụ nặng chạy ở QThread/worker và giao tiếp bằng signal, không chặn UI. Cài đặt lưu JSON trong thư mục dữ liệu người dùng (platformdirs).

## 6. Cấu trúc dự án

```
dubber/
  app/              # GUI PySide6
  core/
    pipeline.py     # orchestrator, cache, cancel
    models.py       # Segment, JobConfig
    asr/            # base.py, sensevoice.py, groq_whisper.py
    translate/      # base.py, gemini.py
    tts/            # base.py, edge.py
    audio/          # extract.py, fit.py, mixer.py, demucs_sep.py
    video/          # subtitles.py (ASS), render.py (ffmpeg)
    utils/          # ffmpeg_locator.py, paths.py, retry.py
  resources/        # fonts/, ffmpeg binaries theo hệ điều hành
  tests/
  packaging/        # pyinstaller spec cho win / mac
  pyproject.toml
```

## 7. Cấu hình, bảo mật, xử lý lỗi

- API key lưu bằng thư viện `keyring` (Keychain / Credential Manager), không ghi vào file cấu hình hay log.
- Mọi lời gọi mạng có timeout, retry có backoff và thông báo lỗi dễ hiểu (hết quota, mất mạng, key sai).
- Tìm FFmpeg: ưu tiên binary đi kèm trong `resources/`, sau đó PATH.
- Dọn thư mục tạm sau khi xuất, giữ cache nếu người dùng bật.
- Dùng `pathlib` cho mọi đường dẫn, xử lý đúng tên file có dấu cách và ký tự Unicode trên cả hai hệ điều hành.

## 8. Đóng gói và phân phối

- PyInstaller `--onedir`, build riêng trên Windows và trên macOS arm64, không build chéo.
- Đi kèm FFmpeg static cho từng hệ điều hành và font tiếng Việt. Model SenseVoice tải ở lần chạy đầu (có thanh tiến độ, lưu trong thư mục dữ liệu). Demucs tải khi người dùng bật lần đầu.
- macOS dùng cá nhân: ký ad-hoc, mở bằng chuột phải → Open. Nếu sau này phát hành cho người khác thì cần Developer ID và notarize. Windows có thể bị SmartScreen cảnh báo nếu không ký.

## 9. Lộ trình và tiêu chí nghiệm thu

| Mốc | Nội dung | Nghiệm thu |
| --- | --- | --- |
| M0 | Khung dự án, CLI `dubber input.mp4`, tìm FFmpeg, logging | Chạy được trên Win và Mac, tách audio và xuất lại video |
| M1 | ASR local: spike hai runtime SenseVoice, chọn một, đo lỗi trên 3 clip thật | Ra đoạn có timestamp, ổn định trên cả hai hệ điều hành, nhanh hơn thời gian thực |
| M2 | Translator Gemini: lô, JSON, fallback quota, giới hạn ký tự | Bản dịch đủ id, không rỗng, hết quota thì tự chuyển model |
| M3 | edge-tts + cache + TimingFitter | Clip 5 phút: ít nhất 90% đoạn khớp khung mà không phải tăng tốc quá 1,3× |
| M4 | Mixer: duck và mute, loudnorm | Giọng Việt rõ và to hơn giọng gốc, không méo |
| M5 | Phụ đề ASS + blur + xuất MP4 (encoder phần cứng và dự phòng) | Phụ đề đúng vị trí, cỡ chữ, đủ dấu tiếng Việt, blur bật/tắt được |
| M6 | GUI đầy đủ: kéo thả, chọn vùng, cài đặt, tiến độ, hủy | Làm trọn quy trình không cần dòng lệnh |
| M7 | Tùy chọn: Groq Whisper, Demucs | Đổi engine trong Settings hoạt động |
| M8 | Đóng gói Win + Mac, thử trên máy sạch | Cài và chạy được không cần Python |

## 10. Kiểm thử

- Bộ video mẫu: clip 30 giây có phụ đề Trung, clip 30 giây không phụ đề, clip 5 phút có nhạc nền, clip 20 phút, clip nhiều người nói, video dọc 9:16.
- Unit test: hàm khớp thời lượng, sinh ASS, dựng lệnh FFmpeg, parser JSON của translator (dùng provider giả), retry/backoff.
- Kiểm tra thủ công mỗi bản: nghe đồng bộ, đọc phụ đề, so âm lượng giọng Việt với giọng gốc.

## 11. Rủi ro và cách giảm

| Rủi ro | Cách giảm |
| --- | --- |
| edge-tts không chính thức, có thể bị chặn hoặc đổi | Interface `TTSEngine`, giới hạn song song, cache, sẵn đường thêm Azure TTS |
| Quota Gemini free thấp ở model mạnh | Gộp lô, fallback Flash-Lite, cache bản dịch, báo người dùng khi hết |
| Tiếng Việt dài hơn tiếng Trung, lệch tiếng | Giới hạn ký tự trong prompt, quy trình khớp thời lượng 5 bước |
| ASR sai khi có nhạc nền, ồn, giọng địa phương | Groq Whisper dự phòng; về sau thêm Demucs trước ASR và màn hình sửa bản dịch |
| Blur vẫn lộ chữ Trung | Cho chỉnh độ mờ và lớp phủ tối; về sau cân nhắc inpainting |
| Máy yếu chạy chậm | Mặc định chế độ nhẹ, Demucs tùy chọn, encoder phần cứng |
| Bản quyền video gốc | Người dùng tự chịu trách nhiệm về quyền sử dụng khi đăng lại |
| Gói free của Google có thể dùng dữ liệu gửi lên | Cảnh báo trong UI, chỉ gửi văn bản |

## 12. Hướng nâng cấp sau

Màn hình duyệt/sửa bản dịch và thời gian; dán link tải video (yt-dlp) và hàng đợi; nhiều giọng và nhận diện người nói; OCR tự dò vùng phụ đề; inpainting; thêm provider (Azure TTS, Claude/GPT cho dịch); nhân bản giọng.

## 13. Prompt mẫu giao cho agent

**Prompt chung (đặt ở đầu mỗi phiên, kèm tài liệu này):**

```
Bạn là kỹ sư Python. Làm theo tài liệu kế hoạch đính kèm (mục 2–9), lần lượt từng mốc M0 → M8; mỗi mốc xong phải chạy được và có test. Không thêm tính năng ngoài phạm vi. Trước khi code mỗi mốc, tóm tắt cách làm trong 5 dòng. Nếu thư viện, model hoặc API có thể đã đổi (ID model Gemini, edge-tts, runtime SenseVoice), hãy kiểm tra tài liệu hiện hành rồi mới dùng. Phải chạy được trên cả Windows và macOS arm64, dùng pathlib, không hard-code API key.
```

**Prompt cho từng mốc:**

```
Hãy thực hiện mốc Mx trong kế hoạch. Tiêu chí nghiệm thu: <chép từ bảng mục 9>. Viết test, chạy và báo kết quả. Liệt kê rõ những điểm bạn chưa chắc chắn hoặc đã giả định.
```
