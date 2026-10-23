# Plan M10 — Chất lượng phụ đề (chuẩn hóa, cắt câu, 1 dòng, blur, viền chữ)

Ngày: 2026-10-23 · Nhánh: `main-windown-edition` (commit `c3519f1`)
**Trạng thái: đã triển khai trên nhánh `m10-subtitle`.** Khác với plan:
- Cắt cue dùng thêm `pyvi` để tách từ tiếng Việt (vẫn có danh sách từ ghép làm dự phòng), và giới hạn
  `MAX_CUE_CHARS = 50` ký tự mỗi dòng (vừa khung nhưng quá dài thì vẫn khó đọc).
- Đo chữ: libass co font theo `usWinAscent + usWinDescent` (bảng OS/2), sai số < 1% so với khung render thật.
- Tên riêng Groq giữ trong `GroqTranslator.names` và được áp ngay khi chuẩn hóa, không lưu thêm vào `translate.json`.
Nguồn review: `exsample_vi (3).mp4` + job tương ứng (`translate.json`, `fit.json`, `subs.ass`;
bản dịch dùng **dịch thủ công** — `translations/manual.json`).

---

## 0. Hiện trạng & nguyên nhân (đã đối chiếu code)

| # | Vấn đề | Ví dụ thực tế (subs.ass) | Nguyên nhân trong code |
|---|---|---|---|
| 1 | Cuối cue còn `,` | "Trong những khu rừng\Nnguyên sinh rậm rạp ở Bomi," | `_split_at_clause` (`video/subtitles.py`) cắt **sau** dấu phẩy và giữ dấu ở cue trước; không có bước nào xóa |
| 2 | Đầu cue viết thường | "là chìa khóa để tạo nên phô mai." · segment 20 "bẻ một miếng bánh…" | Cue thứ 2 của câu bắt đầu giữa câu; segment ASR bị cắt giữa câu nên bản dịch cũng bắt đầu thường. Không có bước viết hoa |
| 3 | Cắt sai chỗ | "Điều đặc biệt nhất\Ncủa món ăn này chính" / "là cách chế biến độc\Nđáo có một không hai." · "…bình thường ấy chính" / "là chìa khóa…" | Bản dịch **đúng 1 câu** (segment 2). Câu dài 83 ký tự > `MAX_CHUNK_CHARS=68`, không có dấu phẩy → `_split_at_clause` rơi xuống nhánh "cắt ở **giữa số từ**" → ra "…này chính \| là…". Sau đó `_wrap_text` lại chia mỗi cue thành 2 dòng, nên nhìn như câu bị lặp |
| 4 | Sub 2 dòng | gần như mọi cue có `\N` | `MAX_LINES=2`, `_wrap_text` cân 2 dòng, `WrapStyle: 0` |
| 5 | Blur | Chế độ `auto` = **hợp** tất cả box của từng cue, chỉ bật khi có sub (`_compute_sub_blurs` + `_enable_groups`). Độ rộng chữ ước lượng `0.62 × cỡ chữ` | Box lệch so với chữ thật; có lúc tắt nên lộ sub Trung |
| 6 | Không viền chữ | Style `BorderStyle=3` (hộp tối `&H55000000`), `Outline=0` | Chưa có option viền/màu. `SubtitleStyle` đã có sẵn `primary_color`, `outline_color`, `outline` nhưng GUI chưa dùng |

→ Không có bug lặp ở phần dịch. "Lặp" là do mỗi cue bị chia thêm thành 2 dòng.

---

## 1. Chuẩn hóa chữ tiếng Việt

Tạo module mới `dubber/core/translate/normalize_vi.py`, viết **hàm thuần, chạy lại nhiều lần vẫn cho cùng kết quả**:

```python
def normalize_text(vi: str, zh: str = "") -> str        # mức segment (TTS + sub)
def normalize_cue(text: str, is_last: bool) -> str      # mức cue hiển thị
def apply_names(vi: str, names: dict[str, str]) -> str  # tên riêng (chỉ Groq)
```

### 1.1 Mức segment — `normalize_text` (gọi trong `pipeline.run_m2`)
Gọi ngay trước `write_translate_json(...)`. Gọi **cả ở nhánh dùng cache** (sau `shutil.copyfile(stored, translate_json)`), để bản dịch cũ đã lưu cũng được chuẩn hóa. Vì hàm chạy lại vẫn cho cùng kết quả nên không cần đổi `sig`, cũng không phải dịch lại. Áp cho mọi translator: groq, local, nllb, gemini, manual.

1. `unicodedata.normalize("NFC", …)`, gộp khoảng trắng, trim.
2. Khoảng trắng quanh dấu câu: bỏ space trước `, . ! ? : ;`, thêm 1 space sau (trừ số `3.5`, `1,5`).
3. Viết hoa chữ đầu: `s[0].upper() + s[1:]`. Không dùng `str.capitalize()` vì nó hạ thường các chữ còn lại, làm hỏng tên riêng như "Bomi" hay "Tây Tạng".
4. Viết hoa sau `. ! ?` trong câu.
5. Cuối segment còn `, ; : -` → xóa. Nếu câu nguồn `zh` kết thúc bằng `。！？` mà bản Việt chưa có `. ! ?` → thêm `.`

Kết quả: TTS đọc cùng một text với sub, và `fit.json` vẫn khớp với `translate.json`. Hàm `_load_fit_json` so sánh `vi_text` của hai file. Video cũ sẽ bị dựng lại TTS một lần, chấp nhận được.

### 1.2 Mức cue — `normalize_cue` (gọi trong `video/subtitles.py`)
Gọi sau khi chia cue (mục 2), áp cho từng cue:
- Xóa `, ; :` ở cuối cue. Nếu là cue cuối của segment thì giữ `. ! ? …`.
- Viết hoa chữ đầu mỗi cue, theo yêu cầu "đầu dòng viết hoa".

### 1.3 Tên riêng — chỉ với Groq
- Sửa `_SYSTEM_PROMPT` trong `groq_translator.py` để model trả về `[{id, vi, names: ["Bomi", "Tây Tạng", …]}]`. Thêm yêu cầu: "tên riêng (người, địa danh, tổ chức) viết hoa từng âm tiết".
- Sửa `_parse_response`: đọc thêm `names`, gom lại thành dict `{lower: chuẩn}` dùng chung cho cả video. Truyền dict này vào context của các batch sau để cách viết tên thống nhất.
- Trong `normalize_text`, gọi `apply_names()`: thay theo ranh giới từ, không phân biệt hoa thường.
- Lưu `names` vào `translate.json` (key phụ, không phá format cũ) để nhánh cache dùng lại.
- Local/NLLB/Gemini/manual: **không xử lý tên riêng**.

### 1.4 Test — `tests/test_normalize_vi.py`
Các case: dấu phẩy cuối, chữ đầu viết thường, chữ "Bomi" ở giữa câu không bị hạ thường, chuỗi NFD, số thập phân, câu đã chuẩn thì giữ nguyên, chạy 2 lần cho cùng kết quả. Thêm case cho `_parse_response` khi có và không có `names` (`test_groq_translator.py`).

---

## 2. Cắt cue đúng ý — viết lại `_split_to_chunks` / `_split_at_clause`

Phần dịch vẫn giữ theo segment như hiện tại vì đã đúng câu. Chỉ sửa **bộ chia cue hiển thị** trong `video/subtitles.py`.

### 2.1 Thuật toán mới
Đầu vào là `vi_text` của 1 segment và độ rộng tối đa 1 dòng (px, mục 3). Đầu ra là danh sách cue, mỗi cue vừa 1 dòng.

1. Tách câu tại `. ! ?` như hiện tại (`_SENT_END`).
2. Câu nào vượt độ rộng thì chọn **điểm cắt tốt nhất** trong tất cả ranh giới từ, theo điểm phạt (cost):
   - `+|len trái − len phải|`: ưu tiên cắt cân đối.
   - `−20` nếu cắt **sau** `, ; :`.
   - `−12` nếu từ đầu phần phải là từ nối hoặc từ mở cụm: `chính là, là, nhưng, và, để, khi, vì, nên, mà, thì, nếu, rồi, còn, cho nên, bởi vì, trong khi, sau khi, trước khi, với`. Với `chính là`, cắt **trước** "chính".
   - `+40` nếu phần trái kết thúc bằng từ yếu: `chính, là, của, và, các, những, một, này*, cho, với, để, được, đã, sẽ, đang, rất, cái, con`. (*"này" chỉ phạt khi theo sau là danh từ. Đơn giản hơn là bỏ "này" khỏi danh sách.)
   - `+30` nếu một bên chỉ còn 1–2 từ.
   - Không cắt giữa các cặp từ ghép thường gặp như `độc | đáo`, `nguyên | liệu`, `thưởng | thức`. Dùng một danh sách nhỏ trong module, không thêm thư viện tách từ.
3. Đệ quy cho tới khi mọi phần vừa 1 dòng.
4. Kết quả mong đợi với dữ liệu thật:
   - "Điều đặc biệt nhất của món ăn này" | "Chính là cách chế biến độc đáo có một không hai."
   - "Những cành cây trông bình thường ấy" | "Chính là chìa khóa để tạo nên phô mai."
   - "Trong những khu rừng nguyên sinh rậm rạp ở Bomi" | "Tây Tạng, có rất nhiều cây vân sam cao lớn." (Dấu phẩy sau "Bomi" bị xóa bởi `normalize_cue`.)

### 2.2 Thời gian
- Giữ cách chia theo tỉ lệ ký tự và `_retime` như hiện tại.
- `_merge_short_chunks`: điều kiện ghép đổi từ `MAX_CHUNK_CHARS` sang **"ghép xong vẫn vừa 1 dòng"**. Nếu không ghép được thì giữ cue ngắn: thà cue ngắn còn hơn bị 2 dòng.

### 2.3 Test — `tests/test_subtitles.py`
- 3 câu ở trên phải ra đúng các cue như mong đợi.
- Ghép tất cả cue (bỏ chuẩn hóa) phải bằng `vi_text`: không mất chữ, không lặp chữ.
- Không cue nào kết thúc bằng từ yếu hoặc `,`.

---

## 3. Sub 1 dòng

- `_header`: đổi sang `WrapStyle: 2` (libass không tự xuống dòng). Bỏ `_wrap_text` khỏi `_events_section`, không chèn `\N` nữa.
- **Đo độ rộng chữ thật**: module mới `dubber/core/video/text_metrics.py`:
  - `text_width_px(text, font_family, size_px) -> int`, dùng **Pillow** `ImageFont.truetype(...).getlength()`. Thêm `Pillow` vào `dependencies` trong `pyproject.toml` và kiểm tra `dubber.spec`.
  - Tìm file font: tra `fonts.SUBTITLE_FONTS` sang file trong `fonts_dir()` (thêm cột tên file `.ttf` vào list). Arial lấy từ font hệ thống (`C:\Windows\Fonts\arialbd.ttf`, macOS `/Library/Fonts/Arial Bold.ttf` hoặc `/System/Library/Fonts/Supplemental/Arial Bold.ttf`). Không tìm thấy font thì dùng ước lượng cũ `0.62 × size`.
- Độ rộng tối đa 1 dòng = `video_w − 2·margin_h − 2·blur_pad`, tối đa khoảng 90% `video_w`. Nếu người dùng đặt `sub_pos` lệch tâm thì giới hạn theo khoảng cách từ tâm tới mép gần nhất × 2.
- Bỏ các hằng `CHARS_PER_LINE`, `MAX_LINE_CHARS`, `MAX_LINES`, `MAX_CHUNK_CHARS`. Thay bằng `max_line_px` truyền vào `_events_section`.
- Không thêm option 1/2 dòng: chỉ hỗ trợ 1 dòng, cho code đơn giản.

---

## 4. Che phụ đề gốc: Tắt / Tự động / Thủ công (chỉ chọn 1)

### 4.1 GUI (`gui.py`, tab "Phụ đề")
- Gộp `_chk_blur` và `_combo_blur` thành **một combo** "Che phụ đề gốc" với 3 lựa chọn:
  - `off`: "Tắt"
  - `auto`: "Tự động (theo khung phụ đề Việt, cả video)"
  - `custom`: "Thủ công (vùng tự chọn)"
- Nút "Chọn vùng…" chỉ bật khi chọn `custom`. Giữ nguyên luồng `LayoutDialog` hiện tại.
- Thêm `QSpinBox` "Lề vùng mờ" (0–40 px trên khung 1080p, mặc định 12, tự co giãn theo độ phân giải). Chỉ bật khi chọn `auto`.
- Gợi ý (`_add_hint`): *"Tự động: vùng mờ ôm sát dòng phụ đề dài nhất, hiện suốt video · Thủ công: tự kéo vùng che sub Trung"*. Lề: *"Lớn hơn → che kín hơn nhưng che nhiều hình hơn"*.
- `get_config` / `set_config`: bỏ dùng `blur_on`. Khi load config cũ: `blur_on=False` thì chuyển thành `off`. Thêm key `blur_pad`.
- `_layout_kwargs`: map tương ứng và truyền `blur_pad`. Trong `gui.py` dòng ~2033, đang tính `blur_mode = "auto" if _chk_blur.isChecked() …` → sửa thành đọc từ combo.

### 4.2 Core (`pipeline.py`)
- `run_m5(…, blur_pad: int = 12)`.
- `_compute_sub_blurs` đổi thành `_compute_auto_blur(...)`, trả về **một box duy nhất** `(0.0, duration+1, x, y, w, h)`:
  - `w` = độ rộng thật (mục 3) của **cue dài nhất** + `2·pad`. `h` = chiều cao 1 dòng (`ascent+descent` từ Pillow, hoặc `1.4 × size`) + `2·pad`. Làm tròn số chẵn (YUV420).
  - Căn theo `sub_center` (giống code hiện tại) hoặc theo đáy + `margin_v`, kẹp trong khung hình.
- Vì chỉ có một box suốt video, `render_final` dùng đúng nhánh của `custom` hiện tại: 1 crop, blur và overlay, không cần `enable`. **`render.py` không phải sửa.**
- Nếu `auto` mà tắt phụ đề: vẫn tính box từ `vi_text` để che sub Trung (khác hiện tại là `None`).

### 4.3 Preview (`layout_dialog.render_preview`)
- Đổi sang gọi `_compute_auto_blur` với `pad`, cùng hàm đo chữ, để preview giống hệt bản xuất.

### 4.4 Test
`test_layout.py` / `test_render_blur.py`:
- `auto` trả đúng 1 box phủ toàn thời lượng.
- Box chứa trọn chữ (độ rộng đo ≤ w − 2·pad).
- Box co giãn theo `pad` và cỡ chữ.
- Config cũ có `blur_on=False` thì ra `off`.

---

## 5. Viền chữ + chọn màu

### 5.1 `SubtitleStyle` / `_styles_section`
- Mặc định mới: `BorderStyle=1` (viền), `outline=2.0` (px ở 1080p, nhân `h/1080`), `shadow=0`.
- Thêm `box: bool = False`. Nếu bật thì giữ hộp tối như cũ. Một style của ASS không thể vừa có viền vừa có hộp, nên ghi thêm style `Box` (`BorderStyle=3`, chữ trong suốt `&HFF…`) ở `Layer 0`, còn chữ có viền ở `Layer 1`. Mỗi cue sinh 2 dòng `Dialogue`.
- Helper `hex_to_ass("#RRGGBB", alpha=0) -> "&HAABBGGRR"`, kèm test vì ASS đảo thứ tự màu.

### 5.2 GUI (tab "Phụ đề")

| Option | Control | Mặc định | Gợi ý |
|---|---|---|---|
| Màu chữ | nút màu → `QColorDialog` | `#FFFFFF` | — |
| Màu viền | nút màu → `QColorDialog` | `#000000` | "Viền tối + chữ sáng dễ đọc nhất trên mọi nền" |
| Độ dày viền | `QSpinBox` 0–8 px | 2 | "0 = không viền · dày quá trông nặng, lấp nét chữ" |
| Hộp nền tối | `QCheckBox` | tắt | "Thêm hộp đen mờ sau chữ (kiểu cũ)" |

- Nút màu hiển thị ô màu hiện tại (stylesheet `background`).
- `get_config`/`set_config` thêm `sub_color`, `sub_outline_color`, `sub_outline`, `sub_box`, đọc bằng `.get()` với giá trị mặc định cho config cũ. Thêm vào `_layout_kwargs`, sang `run_m5`, rồi sang `SubtitleStyle`.
- `LayoutDialog` / `render_preview`: nhận thêm các tham số style để nút "Xem trước trên video…" hiện đúng màu và viền.

---

## 6. Thanh loading khi nạp file + không khóa giao diện khi đang chạy

### 6.1 Hiện trạng (đã đối chiếu code)

| Chỗ nạp | Code hiện tại | Vấn đề |
|---|---|---|
| Thêm/chọn video | `MainWindow._add_files` chỉ thêm vào list. `_on_select` → `_show_source_info` gọi `probe_info()` (ffprobe) **ngay trên luồng GUI** | Video lớn, ổ mạng hoặc USB thì app đơ, không có báo hiệu |
| Khung xem trước / chọn vùng | `LayoutDialog._render` → `render_preview()` chạy ffmpeg **đồng bộ** | Dialog đứng hình mỗi lần render lại |
| Audio clone giọng | `VoiceCloneDialog.set_source` → `probe_duration()` đồng bộ. `_listen`/`_cut` → `prepare_reference()` (ffmpeg) đồng bộ. "Thử giọng"/"Lưu" đã chạy thread (`_Task` + `_set_busy`) nhưng chỉ có chữ trạng thái | Đơ khi chọn file và khi nghe thử, không có thanh chạy |
| Up script `.txt` (dịch thủ công) | `ManualTranslateDialog._pick` đóng dialog ngay. File được đọc trong worker (`parse_vietnamese`). Nếu lỗi thì dialog mở lại | Không thấy đang đọc hay kiểm tra file. Lỗi chỉ hiện sau khi dialog đóng rồi mở lại |
| Khi đang chạy | `_sync_panels()` và `_run_next()` gọi `self._opts.setEnabled(False)` / `self._export.setEnabled(False)`. `_export` là một tab trong `_opts` (`add_page`) | Khóa **cả `QTabWidget`, kể cả thanh tab** → không chuyển tab để xem cấu hình của video đang chạy |

### 6.2 Thành phần dùng chung — `dubber/app/tasks.py` (mới)
- Chuyển `_Task` (QObject chạy `fn()` trong `QThread`, trả `done`/`failed` về luồng GUI) từ `voice_dialog.py` sang đây. Thêm helper `run_task(parent, fn, on_done, on_fail)` để mọi dialog dùng chung, không mỗi nơi tự viết một kiểu.
- Widget `BusyBar`: `QProgressBar` dạng chạy vô hạn (`setRange(0, 0)`) + nhãn ("Đang đọc video…"). Gọi `start(text)` / `stop()`. Nếu có tiến độ thật thì `set_progress(i, n)` chuyển sang thanh %.

### 6.3 Nạp video
- `_add_files`: thêm item ngay với nhãn `⏳ tên_video — đang đọc…`, rồi probe **tất cả video mới trong một task nền**, lần lượt từng file. `BusyBar` ở khu hàng đợi hiện "Đang đọc video 2/5…".
- Probe xong một file: gán `job.info` / `job.info_error`, cập nhật nhãn item. Nếu đang chọn đúng video đó thì cập nhật `VideoInfoPanel`, `ExportPanel.set_source` và `set_trim_limit`.
- `_show_source_info`: nếu `job.info` chưa có thì **không** gọi ffprobe nữa. Panel thông tin hiện "Đang đọc thông tin video…", task nền điền vào sau.
- Nút **Bắt đầu**: video còn đang đọc thì đợi đọc xong (hoặc tự probe trong worker như hiện tại).
- `LayoutDialog._render`: chạy `render_preview` qua `run_task`. Canvas giữ ảnh cũ và phủ lên `BusyBar` "Đang dựng khung xem trước…". Bỏ qua kết quả cũ nếu đã có lần render mới hơn (đếm số lần render).

### 6.4 Audio clone giọng (`voice_dialog.py`)
- `set_source`: chạy `probe_duration` trong `run_task`. Trong lúc đọc thì `BusyBar` "Đang đọc file âm thanh…" và tắt các nút Nghe/Thử/Lưu.
- `_listen`: cắt đoạn (`prepare_reference`) trong task, xong mới phát.
- "Thử giọng" / "Lưu": giữ thread hiện tại, thêm `BusyBar` ("Đang tạo giọng thử…", "Đang lưu giọng…"). Tách embedding VieNeu có thể mất vài chục giây.

### 6.5 Up script `.txt` (`manual_dialog.py`)
- `_pick`: **không đóng dialog ngay**. Chạy `parse_vietnamese(path, count)` qua `run_task`, kèm `BusyBar` "Đang đọc và kiểm tra file…".
  - Hợp lệ: `self.answer = path` → `accept()`.
  - Lỗi (sai số dòng, sai encoding…): hiện lỗi **ngay trong dialog** và cho chọn lại. Không phải đợi worker trả về rồi mở lại.
- Worker vẫn parse lại để chắc chắn (giữ vòng lặp xử lý lỗi hiện có trong `_manual_translation`).

### 6.6 Khi đang chạy: chỉ khóa phần chỉnh sửa
- Thêm `OptionsPanel.set_read_only(ro: bool)`: **disable từng trang tab**, gồm `self._tabs.widget(i)` và trang "Xuất video". **Thanh tab vẫn bật** để chuyển tab xem cấu hình. Thêm một dòng banner ở đầu panel: "🔒 Video đang xử lý — chỉ xem, không sửa được".
- `_sync_panels()` và `_run_next()`: thay `self._opts.setEnabled(False)` / `self._export.setEnabled(False)` bằng `self._opts.set_read_only(...)`.
- Danh sách hàng đợi, nút Xóa/Chạy lại cho video khác, ô thả video **luôn bật** trong lúc chạy (hiện tại đã đúng, cần giữ và kiểm tra lại).
- Chọn video khác trong lúc chạy thì sửa cấu hình bình thường (`_commit_selected` đã bỏ qua video `RUNNING`).
- Kiểm tra thêm: các thao tác trên luồng GUI khi đang render (ví dụ `refresh_storage`, cập nhật tiến trình) không được làm đơ app khi bấm qua lại giữa các video.

### 6.7 Test
- `test_gui_queue.py` (pytest-qt / `QApplication`):
  - Chọn video đang chạy → `tabBar().isEnabled()` là True, trang tab bị disable.
  - Chọn video khác → sửa được.
  - `_add_files` không gọi `probe_info` trên luồng GUI (mock và kiểm tra luồng gọi).
- `manual_dialog`: file sai thì dialog vẫn mở và hiện lỗi. File đúng thì `accept()`.

---

## 7. Danh sách file thay đổi

| File | Thay đổi |
|---|---|
| `dubber/core/translate/normalize_vi.py` | **mới**: chuẩn hóa segment/cue, tên riêng |
| `dubber/core/video/text_metrics.py` | **mới**: đo độ rộng/chiều cao chữ bằng Pillow |
| `dubber/core/video/subtitles.py` | chia cue mới, 1 dòng, `WrapStyle 2`, `normalize_cue`, style viền/màu/hộp |
| `dubber/core/video/fonts.py` | thêm tên file `.ttf` cho từng font, đường dẫn Arial hệ thống |
| `dubber/core/translate/groq_translator.py` | prompt và parse `names` |
| `dubber/core/pipeline.py` | gọi `normalize_text` (cả nhánh cache), `_compute_auto_blur`, tham số mới cho `run_m5` |
| `dubber/app/gui.py` | combo che sub, lề mờ, màu chữ/viền, độ dày, hộp nền; cập nhật config và `_layout_kwargs` |
| `dubber/app/layout_dialog.py` | preview dùng style và box mới; render trong task nền + `BusyBar` |
| `dubber/app/tasks.py` | **mới**: `_Task`/`run_task` dùng chung + `BusyBar` |
| `dubber/app/gui.py` (hàng đợi) | probe video nền + nhãn đang đọc; `set_read_only` thay vì khóa cả panel |
| `dubber/app/voice_dialog.py` | đọc/cắt audio trong task + `BusyBar`; dùng `tasks.py` |
| `dubber/app/manual_dialog.py` | kiểm tra file `.txt` trong dialog (task + `BusyBar`), báo lỗi tại chỗ |
| `pyproject.toml`, `dubber.spec` | thêm Pillow |
| `tests/…` | `test_normalize_vi.py` mới; sửa `test_subtitles.py`, `test_layout.py`, `test_groq_translator.py`, `test_gui_queue.py` (config, read-only, probe nền) |

## 8. Thứ tự triển khai

1. `normalize_vi.py` + test, nối vào `run_m2` (cả nhánh cache).
2. `text_metrics.py` + Pillow + map font.
3. Bộ chia cue mới + 1 dòng + `normalize_cue` (`subtitles.py`) + test với 3 câu thật.
4. Style viền/màu/hộp (`SubtitleStyle`, `_styles_section`, layer).
5. `_compute_auto_blur` + `blur_pad`.
6. GUI: combo che sub, lề, màu, viền, hộp + config cũ vẫn load được + preview.
7. Groq `names` (độc lập, làm song song được).
8. `tasks.py` (`run_task` + `BusyBar`) → khóa chỉ-xem (`set_read_only`) → probe video nền → `LayoutDialog`, `voice_dialog`, `manual_dialog`. Độc lập với các bước 1–7, có thể làm trước.
9. Chạy lại video mẫu (dùng `translations/manual.json` có sẵn) và so sánh `subs.ass` + frame trước và sau. Chạy `pytest tests/`.

## 9. Tiêu chí hoàn thành
- `subs.ass` của video mẫu: không có `\N`, không cue nào kết thúc bằng `,` hay từ yếu, mọi cue bắt đầu bằng chữ hoa.
- 3 câu ví dụ ở mục 2.1 ra đúng như mong đợi.
- `auto`: vùng mờ hiện suốt video và bao trọn mọi dòng sub (kiểm tra frame ở 720p/1080p). `custom` chạy như cũ.
- Đổi màu chữ/viền/độ dày trong GUI thì preview và video xuất ra đúng như vậy.
- Thêm nhiều video, chọn file audio clone, up script `.txt`, mở xem trước: mỗi thao tác đều có thanh loading và app không đơ.
- Đang chạy video A: bấm sang video B và sửa cấu hình được. Bấm lại A: chuyển qua lại mọi tab để xem được, nhưng không sửa được.
- Config/queue cũ vẫn mở được. `pytest tests/` xanh.
