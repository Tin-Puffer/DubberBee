# Hướng dẫn sử dụng Dubber Bee

Dubber Bee tự động **lồng tiếng Việt cho video tiếng Trung**: nghe tiếng Trung trong video, dịch
sang tiếng Việt, đọc lại bằng giọng tiếng Việt, rồi xuất ra video mới có giọng Việt và phụ đề
Việt. Phần lớn chạy ngay trên máy của bạn, không cần mạng (trừ khi dịch bằng Groq).

Tài liệu kỹ thuật cho người phát triển: xem [TECHNICAL.md](TECHNICAL.md).

---

## Mục lục

1. [Mở ứng dụng](#1-mở-ứng-dụng)
2. [Làm quen với màn hình](#2-làm-quen-với-màn-hình)
3. [Lồng tiếng nhanh trong 5 bước](#3-lồng-tiếng-nhanh-trong-5-bước)
4. [Thêm video và hàng đợi](#4-thêm-video-và-hàng-đợi)
5. [Tab "Âm thanh"](#5-tab-âm-thanh)
6. [Tab "Giọng & dịch"](#6-tab-giọng--dịch)
7. [Tab "Phụ đề"](#7-tab-phụ-đề)
8. [Tab "Xuất video"](#8-tab-xuất-video)
9. [Lưu video kết quả & ⚙ Cài đặt](#9-lưu-video-kết-quả---cài-đặt)
10. [Chạy, theo dõi, dừng, hủy, chạy lại](#10-chạy-theo-dõi-dừng-hủy-chạy-lại)
11. [Các hộp thoại xuất hiện khi đang chạy](#11-các-hộp-thoại-xuất-hiện-khi-đang-chạy)
12. [Khi có lỗi](#12-khi-có-lỗi)
13. [Dữ liệu được lưu ở đâu](#13-dữ-liệu-được-lưu-ở-đâu)
14. [Mẹo để có kết quả tốt](#14-mẹo-để-có-kết-quả-tốt)
15. [Câu hỏi thường gặp & xử lý sự cố](#15-câu-hỏi-thường-gặp--xử-lý-sự-cố)

---

## 1. Mở ứng dụng

### Bản đã đóng gói (Dubber.app / Dubber.exe)

- **macOS:** giải nén `Dubber-macos.zip`, kéo `Dubber.app` vào thư mục *Applications*.
  Lần đầu mở: **chuột phải vào Dubber.app → Mở (Open) → Mở** (app chưa được Apple chứng nhận,
  nên bấm đúp lần đầu sẽ bị chặn). Từ lần sau bấm đúp bình thường.
- **Windows:** giải nén `Dubber-windows.zip` vào thư mục ngắn (vd. `C:\Dubber`), mở `Dubber.exe`
  trong thư mục `Dubber`. Nếu SmartScreen chặn: **More info → Run anyway** (app chưa ký số).

**Lần đầu mở:** app hỏi tải dữ liệu (nhận dạng giọng, giọng đọc, tách nhạc… khoảng 1,8 GB) — bấm
**Tải về** và chờ. Chỉ tải một lần; mất mạng giữa chừng thì mở lại app sẽ tải tiếp phần còn lại.

**Cập nhật:** khi có bản mới, app tự hỏi lúc mở → **Cập nhật ngay**: app tự tải, đóng lại và mở bản
mới (không cần xóa bản cũ, cài đặt và dữ liệu giữ nguyên). Kiểm tra bằng tay hoặc tắt tự kiểm tra:
**⚙ Cài đặt → Cập nhật**. Trên macOS hãy để `Dubber.app` trong *Applications* thì mới tự cập nhật được.

Bản đóng gói đã có sẵn mọi model cần thiết (nhận diện, tách nhạc, giọng đọc), chạy được khi
không có mạng.

### Chạy từ mã nguồn (bản dev)

Lần đầu trên máy mới (macOS cần [Homebrew](https://brew.sh); tải ~2 GB model):

```bash
git clone https://github.com/Tin-Puffer/DubberBee.git
cd DubberBee
bash scripts/setup_mac.sh --all
# Windows: powershell -ExecutionPolicy Bypass -File setup_dev.ps1 -All
```

Những lần sau:

```bash
cd DubberBee
source .venv/bin/activate
python -m dubber
```

### Groq API key (chỉ cần nếu dịch bằng Groq)

Groq là dịch vụ dịch online, chất lượng tốt nhất, miễn phí có giới hạn.

1. Lấy khóa tại [console.groq.com/keys](https://console.groq.com/keys) → *Create API Key* →
   sao chép khóa (dạng `gsk_…`).
2. Trong Dubber Bee: bấm **⚙ Cài đặt** (góc trên bên phải) → mục **Groq API key** → **🔑 Nhập API key…**
3. Dán khóa vào ô (bấm **Hiện** để xem lại), rồi **Lưu**.
4. Bấm **Kiểm tra** cạnh khóa: app gửi thử một yêu cầu dịch rất nhỏ tới Groq. Đèn **xanh** = khóa
   chạy được; **đỏ** = lỗi, lý do hiện ngay bên cạnh (khóa sai, hết hạn mức, mất mạng…).

Xong — chỉ cần làm **một lần**, app nhớ khóa cho mọi lần mở sau. Mục *Groq API key* hiện
**"✓ Đã lưu (gsk_…abcd)"** (chỉ hiện 4 ký tự đầu và cuối). Nút **Groq ●** ở góc trên cùng
có màu theo đèn (xám = chưa kiểm tra), bấm vào để mở Cài đặt.

- Khóa được cất trong **kho mật khẩu của máy** (macOS Keychain / Windows Credential Manager),
  không nằm trong file nào của Dubber Bee, không ghi vào log hay báo cáo lỗi.
- Muốn bỏ khóa: mở lại hộp thoại → **Xóa khóa đã lưu**.
- Người dùng nâng cao vẫn có thể đặt biến môi trường `GROQ_API_KEY`; khóa lưu trong app được
  ưu tiên hơn.

---

## 2. Làm quen với màn hình

```
┌───────────────────── Dubber Bee · Lồng tiếng Trung → Việt ─────────────────────┐
│                                                             [Groq ●] [Cài đặt] │
├──────────────────────────────────────────────────┬─────────────────────────────┤
│ [ + Thêm video — kéo thả hoặc bấm để chọn ]      │ Cấu hình cho <video>        │
│ Hàng đợi   3 video · 1 xong · 1 đang chạy …      │ [Âm thanh][Giọng & dịch]    │
│  ▶ video1.mp4         2:00 · 1080p · 22 MB       │ [Phụ đề][Xuất video]        │
│    Tạo giọng Việt · tổng 45% · còn ~6 phút       │   … các tùy chọn …          │
│  ✓ video2.mp4   [Xem][TikTok][Thư mục][↻]        │                             │
│ [Áp dụng cho tất cả][Chạy lại][Xóa][Dọn]         │ Tiến trình · 45% · còn …    │
│ Thông tin video gốc       [ảnh + phụ đề mẫu]     │  ✓ Tách nhạc nền       0:42 │
│ Lưu video kết quả                                │  ◉ Tạo giọng Việt      1:10 │
├──────────────────────────────────────────────────┴─────────────────────────────┤
│ [▶ Chạy hàng đợi] [Dừng sau video này] [Hủy video này] [Mở thư mục]            │
│ dòng trạng thái                                                                │
└────────────────────────────────────────────────────────────────────────────────┘
```

- **Góc trên:** **⚙ Cài đặt** (Groq API key, thông báo, prompt AI, cache) và nút
  **Groq ●** cho biết Groq API key còn chạy được không.
- **Bên trái:** thêm video, hàng đợi (mỗi video một thẻ), thông tin + ảnh xem trước của video
  đang chọn, nơi lưu kết quả.
- **Bên phải:** cấu hình của **video đang chọn** (4 tab) và tiến trình chạy.
- **Dưới cùng:** nút chạy / dừng / hủy / mở thư mục kết quả, và dòng trạng thái.

App **nhớ** kích thước cửa sổ, tab đang mở và **hàng đợi** khi tắt; mở lại là thấy như cũ (video
đang chạy dở được đưa về "chờ chạy"; video đã bị xóa / di chuyển thì bỏ qua).

Dưới mỗi lựa chọn có **một dòng chữ nhỏ** giải thích chọn như vậy được gì, mất gì. Rê chuột
lên lựa chọn cũng thấy giải thích đó.

**Lăn chuột không đổi giá trị** của ô chọn, ô số hay thanh trượt (tránh đổi nhầm khi cuộn
trang) — lăn chuột chỉ cuộn trang. Muốn đổi: bấm chọn, gõ số, dùng ▲▼ hoặc kéo thanh trượt.

Ký hiệu trong hàng đợi: **⏳** đang chờ · **▶** đang chạy · **✋** đang chờ bạn (duyệt bản
dịch, chọn file…) · **✓** xong · **✗** lỗi · **⊘** đã hủy.

---

## 3. Lồng tiếng nhanh trong 5 bước

1. **Kéo thả video** vào ô "Kéo thả video vào đây" (hoặc bấm vào ô để chọn file).
2. Tab **Giọng & dịch**: chọn **TTS engine** và **Giọng đọc** (bấm **▶ Nghe thử** để nghe), chọn
   **Dịch** (mặc định "Tự động").
3. Tab **Âm thanh**: chỉnh độ to giọng Việt / nhạc nền / tiếng Trung nếu muốn
   (mặc định: giọng Việt 100%, nhạc nền 40%, tiếng Trung 20%).
4. Bấm **▶ Chạy hàng đợi** và theo dõi phần **Tiến trình**.
5. Xong, bấm **📂 Mở thư mục kết quả** để thấy video `<tên gốc>_vi.mp4`.

---

## 4. Thêm video và hàng đợi

### Thêm video

- Kéo thả **một hoặc nhiều** video vào ô "Kéo thả video vào đây", hoặc bấm vào ô để chọn.
- Định dạng: `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`.
- Video mới thêm dùng **cấu hình đang hiện ở bên phải** lúc thêm.
- Thêm lại một video đã có trong hàng đợi:
  - nếu video đó **đang chờ** → bị bỏ qua (không thêm trùng);
  - nếu video đó **đã xong / lỗi / đã hủy** → được đưa lại vào hàng đợi để chạy lần nữa.

### Mỗi video một cấu hình riêng

- Bấm vào một video trong hàng đợi → bên phải hiện "Cấu hình cho **<tên video>**". Mọi thay
  đổi chỉ áp dụng cho video đó.
- **Áp dụng cho tất cả:** chép cấu hình đang hiện cho mọi video **đang chờ**.
- Video **đang chạy** không sửa được cấu hình. Video đã xong / lỗi / đã hủy vẫn sửa được.

### Thẻ video

Mỗi video là một thẻ 2 dòng: tên + thời lượng · độ phân giải · dung lượng, rồi trạng thái và
**các nút hợp với trạng thái đó**:

| Trạng thái | Dòng thứ hai | Nút trên thẻ |
|---|---|---|
| ⏳ Chờ chạy | bộ dịch · giọng đọc (· duyệt bản dịch) | ✕ xóa |
| ▶ Đang chạy | bước đang làm · % tổng · đã chạy · còn bao lâu | ⊘ Hủy |
| ✋ Chờ bạn | việc đang chờ bạn | — |
| ✓ Xong | giờ xong · chạy hết bao lâu · dung lượng | ▶ Xem · 📋 TikTok · 📂 · ↻ |
| ✗ Lỗi | thông báo lỗi | Xem lỗi · ↻ Thử lại |

- **📋 TikTok** chép sẵn prompt TikTok kèm lời thoại vào clipboard — dán thẳng vào ChatGPT.
- **Chuột phải** vào thẻ: Xem video, Copy prompt TikTok, Mở file .txt, Hiện trong Finder, Mở
  thư mục tạm, Chạy lại, **Nhân bản** (chạy cùng video với cấu hình khác, giữ bản cũ), Áp cấu
  hình cho các video đã chọn, Xóa.
- **Chọn nhiều:** giữ Cmd (Windows: Ctrl) hoặc Shift khi bấm → Xóa / Chạy lại / áp cấu hình
  cho cả nhóm.
- **Kéo thả thẻ** lên / xuống để đổi thứ tự chạy.

### Các nút dưới hàng đợi

| Nút | Tác dụng |
|---|---|
| **Áp dụng cho tất cả** | Chép cấu hình cho mọi video đang chờ |
| **↻ Chạy lại** | Đưa các video đang chọn (đã xong / lỗi / đã hủy) về hàng đợi |
| **Xóa** | Bỏ các video đang chọn khỏi hàng đợi (không xóa file gốc) |
| **Dọn video đã xong** | Bỏ khỏi danh sách mọi video đã xong / lỗi / đã hủy |

### Thông tin video gốc

Khi chọn một video: độ phân giải, số khung hình/giây, thời lượng, dung lượng, mã hóa video
và âm thanh của file gốc. Bên phải là **một khung hình của video với phụ đề mẫu** vẽ theo cài
đặt ở tab Phụ đề (font, cỡ, màu, viền, vị trí, vùng mờ) — đổi gì thấy ngay. Đây là bản xem
nhanh **gần đúng**; muốn xem chính xác: tab Phụ đề → **Xem trước trên video…**.

---

## 5. Tab "Âm thanh"

Quyết định video kết quả gồm những âm thanh nào và to nhỏ ra sao.

| Thanh | Mặc định | Ý nghĩa |
|---|---|---|
| **Giọng Việt** | 100% | Độ to giọng đọc tiếng Việt (0–200%; trên 100% hiện màu cam vì có thể bị rè) |
| **Nhạc nền** | 40% | Nhạc / tiếng động gốc của video. Bỏ dấu tích để tắt hẳn |
| **Tiếng Trung** | 20% | Giọng nói tiếng Trung gốc. Bỏ dấu tích để tắt hẳn |

- 100% = giữ nguyên độ to của âm thanh gốc.
- Nếu **nhạc nền và tiếng Trung khác mức nhau** (hoặc chỉ giữ một trong hai), app phải **tách
  nhạc khỏi giọng bằng AI** (bước "Tách nhạc nền" trong Tiến trình) — chậm hơn một chút.
  Hai mức bằng nhau thì dùng thẳng âm thanh gốc, không cần tách.

---

## 6. Tab "Giọng & dịch"

### 6.1 TTS engine

| TTS engine | Ưu | Nhược |
|---|---|---|
| **Piper** | Nhanh, gần như tức thì; tối đa 16 giọng (tùy bản cài) | Giọng hơi đều, máy móc |
| **VieNeu-TTS** | Giọng tự nhiên hơn; 25 giọng có sẵn; **clone được giọng riêng** | Xử lý lâu hơn (video 1 phút ≈ 15–20 giây đọc trên Mac M-series) |

Nếu VieNeu-TTS hiện "— chưa cài" và không chọn được: bản cài của bạn thiếu VieNeu (xem §15).

### 6.2 Giọng đọc

- **Piper:** chọn một trong các giọng (mặc định *Thanh Nien Tu Tin Medium*).
- **VieNeu-TTS:** danh sách chia hai nhóm **— Giọng có sẵn —** và **— Giọng đã clone —**, cuối
  danh sách có **➕ Clone giọng mới…**.
- **▶ Nghe thử:** đọc câu mẫu *"Xin chào, tôi là Dubber, trợ lý lồng tiếng tự động…"* bằng
  giọng đang chọn. Lần đầu mất vài giây để tạo (lần đầu dùng VieNeu lâu hơn vì phải nạp
  model); các lần sau phát ngay.
- App nhớ **bộ đọc và giọng chọn lần cuối** cho lần mở sau.

### 6.3 Clone giọng mới (VieNeu-TTS)

Tạo giọng đọc giống giọng một người từ một đoạn ghi âm ngắn.

1. Chọn **TTS engine: VieNeu-TTS**, rồi trong ô Giọng đọc chọn **➕ Clone giọng mới…**
2. Bấm **Chọn…** và chọn file **ghi âm hoặc video** (`.wav .mp3 .m4a .flac .ogg .mp4 .mkv
   .mov .avi .webm`). Hộp thoại hiện **Thời lượng**.
3. **Bắt đầu từ:** nếu file dài hơn 8 giây, chọn giây bắt đầu của đoạn mẫu. App chỉ dùng
   **8 giây** tính từ mốc này (VieNeu chỉ dùng tối đa 8 giây — đưa dài hơn không tốt hơn).
   Ngắn hơn 3 giây sẽ có cảnh báo "Đoạn quá ngắn, giọng sẽ kém giống".
4. **▶ Nghe thử:** nghe đúng đoạn 8 giây sẽ dùng.
5. **🔊 Nghe thử:** nghe câu *"Xin chào, đây là giọng đọc thử nghiệm."* bằng giọng mới
   (vài giây; lần đầu lâu hơn).
6. Đặt **Tên giọng** (không trùng tên giọng khác) → **Lưu**. Giọng mới được chọn ngay.

**Chọn đoạn mẫu tốt** (quan trọng hơn độ dài):
- 6–8 giây, **một người nói**, nói tiếng Việt tự nhiên, liền mạch;
- **không nhạc nền, không ồn** (app có khử ồn nhưng mẫu sạch vẫn tốt hơn nhiều);
- cảm xúc giống thứ bạn muốn đọc (mẫu điềm tĩnh → bản đọc điềm tĩnh).

Giọng đã clone được lưu vĩnh viễn trên máy, không mất khi tắt app hay cập nhật app.

### 6.4 Đổi tên / xóa giọng đã clone

Chọn một giọng trong nhóm **Giọng đã clone** → bên cạnh xuất hiện:
- **✎** đổi tên;
- **🗑** xóa (có hỏi xác nhận, không hoàn tác được).

Giọng có sẵn của VieNeu không xóa được. Nếu một video trong hàng đợi đang dùng giọng vừa bị
xóa, ô giọng của video đó hiện **"⚠ Giọng đã bị xóa"** và khi chạy sẽ báo lỗi rõ ràng — chọn
giọng khác cho video đó.

### 6.5 Dịch

| Lựa chọn | Khi nào dùng |
|---|---|
| **Tự động (Groq → offline)** | Mặc định. Dùng Groq; mất mạng / không có khóa / lỗi thì tự chuyển sang MarianMT |
| **Groq (online)** | Dịch hay nhất. Cần mạng và Groq API key (**⚙ Cài đặt → 🔑 Nhập API key…**, xem §1) |
| **Offline — MarianMT (~300 MB)** | Không cần mạng, nhanh, dịch kém hơn |
| **Offline — NLLB 1.3B (~5.5 GB)** | Không cần mạng, chậm, lần đầu tải ~5,5 GB, cần ~6 GB RAM. Chất lượng thực tế vẫn kém Groq rõ rệt |

### 6.6 Duyệt bản dịch & nhờ chatbot sửa

Ô **Duyệt bản dịch: "Bỏ qua (chạy tự động, không dừng)"** được **tích sẵn**: app chạy một mạch
từ đầu tới cuối — hợp với video dài (2–3 tiếng) bạn không ngồi canh.

**Bỏ tích** để dịch xong thì app **dừng lại** mở bảng **"Duyệt bản dịch"** (kèm thông báo nếu
bạn đang ở cửa sổ khác), rồi mới đọc tiếng Việt:

- Bảng gồm số câu, thời gian, câu Trung, câu Việt và cột **⚠ Cần chú ý** (câu trống, còn chữ
  Hán, hoặc **quá dài** so với thời gian được nói). *Chỉ hiện câu cần chú ý* / ô **Tìm** giúp
  lọc nhanh.
- **Bấm đúp** ô tiếng Việt để sửa. **↶ Hoàn tác** (Cmd+Z) bỏ thay đổi gần nhất.
- **▶ Nghe câu gốc** phát đoạn tiếng Trung của câu đang chọn; **🔊 Nghe thử** đọc câu tiếng Việt
  bằng giọng của video đó.
- **📋 Copy cho AI**: chép lời dặn + các câu (tất cả / câu đang chọn / câu cần chú ý /
  *phần tiếp theo* 150 câu một lần) → dán vào ChatGPT / Claude / Gemini.
- **📥 Dán kết quả AI**: dán câu trả lời của chatbot (dạng `[12] câu tiếng Việt`), xem
  trước *cũ → mới*, bỏ tích câu không muốn, **Áp dụng**. Câu chatbot không nhắc tới giữ nguyên.
- **✎ Prompt…** (hoặc *⚙ Cài đặt → Chatbot*): sửa lời dặn đặt ở đầu khi sao chép; app
  nhớ cho lần sau. Xóa trắng = dùng lời dặn mặc định.
- **Tiếp tục ▶**: đọc tiếng Việt và xuất video. Câu không sửa dùng lại giọng đã đọc (nhanh).
  Bản đã sửa được nhớ: chạy lại cùng video với cùng bộ dịch sẽ bắt đầu từ bản đã sửa.
- **Hủy video này**: bỏ video (có hỏi lại nếu bạn đã sửa).

Thời gian bạn duyệt **không tính** vào thời gian chạy / thời gian còn lại.

### 6.7 Nhận dạng — "Lọc nhạc nền trước khi nhận dạng"

Bật (mặc định): app tách giọng nói khỏi nhạc nền trước khi nghe tiếng Trung → **nghe chính xác
hơn với video có nhạc**, chậm hơn một chút (~25 giây với video 2 phút). Nên để bật.

### 6.8 Trim — chỉ xử lý một đoạn

Dùng để thử nhanh trên một đoạn ngắn trước khi chạy cả video.

1. Tích **Chỉ xử lý một đoạn**.
2. **Thanh kéo** dài đúng bằng video: kéo **đầu trái** (bắt đầu) và **đầu phải** (kết thúc).
3. Hoặc **gõ số giây** vào hai ô (gõ `12.5` hay `12,5` đều được), nhấn **Enter**. Thanh kéo
   chạy theo số bạn nhập, số chạy theo thanh kéo.
4. Dòng bên dưới cho biết đoạn đã chọn, ví dụ `00:12.5 → 01:05.0 · dài 52.5s`.

**Tự sửa giá trị không hợp lý** (khi bạn nhấn Enter hoặc rời ô):

| Bạn nhập | App sửa thành |
|---|---|
| Bắt đầu sau (hoặc bằng) kết thúc | Bắt đầu = kết thúc − 0,5 giây |
| Kết thúc trước (hoặc bằng) bắt đầu | Kết thúc = bắt đầu + 0,5 giây |
| Kết thúc vượt độ dài video | Kết thúc = cuối video |
| Bắt đầu vượt độ dài video | Sát cuối video |
| Số âm | 0 |

Đoạn luôn dài ít nhất 0,5 giây. Đổi sang video khác, thanh kéo tự co giãn theo độ dài video đó.
Kết quả có tên như bình thường (`<tên>_vi.mp4`) nhưng chỉ gồm đoạn đã chọn.

---

## 7. Tab "Phụ đề"

| Tùy chọn | Ý nghĩa |
|---|---|
| **Chèn phụ đề tiếng Việt vào video** | Chèn phụ đề Việt vào hình (luôn hiện, không tắt được khi xem) |
| **Font chữ** | Arial, Be Vietnam Pro, Roboto, Montserrat, Nunito, Lexend, Noto Sans |
| **Cỡ chữ** | 20–120 (tính trên khung 1080p, tự co theo độ phân giải video) |
| **Xem trước trên video…** | Mở khung xem trước đúng như video xuất ra |
| **Vị trí** | *Mặc định (giữa, sát đáy)* hoặc *Tự chọn trên khung hình* → **Chọn vị trí…** |
| **Màu chữ / Màu viền** | Bấm để chọn màu (mặc định chữ trắng, viền đen) |
| **Độ dày viền** | 0–8 px (0 = không viền) |
| **Nền chữ** | *Hộp nền tối sau chữ* — thêm hộp đen mờ sau chữ như kiểu cũ |
| **Che phụ đề gốc** | Chọn **một** trong ba: *Tắt*; *Tự động* — vùng mờ ôm dòng phụ đề Việt dài nhất (cộng **Lề vùng làm mờ**), hiện suốt video; *Thủ công* → **Chọn vùng…** — tự kéo một vùng cố định suốt video |
| **Lề vùng làm mờ** | 0–40 px mỗi phía quanh dòng phụ đề (chỉ cho chế độ *Tự động*) |

Phụ đề luôn hiện **một dòng**: câu dài được cắt thành nhiều dòng nối tiếp, ở chỗ ngắt tự nhiên
(dấu phẩy, trước "chính là", "nhưng", "để"…), không cắt giữa một từ. Mỗi dòng viết hoa chữ đầu và
không kết thúc bằng dấu phẩy.

Khi một video đang chạy, cấu hình của nó chỉ xem được (vẫn chuyển tab được); các video khác vẫn
chọn và sửa bình thường.

### Khung "Xem trước phụ đề & vùng mờ"

(Mở bằng **Xem trước trên video…**, **Chọn vị trí…** hoặc **Chọn vùng…** — cần chọn một video
trong hàng đợi trước.)

- **Khung hình:** kéo để chọn thời điểm trong video làm ảnh xem trước.
- **Font / Cỡ chữ:** đổi và xem ngay.
- **Kéo chọn vùng làm mờ:** kéo chuột trên ảnh để vẽ vùng mờ (khung đỏ).
- **Bấm chọn tâm phụ đề:** bấm (hoặc kéo) để đặt vị trí phụ đề (khung xanh).
- **Xóa vùng mờ** / **Phụ đề về mặc định**.
- **Xong** để lưu, **Hủy** để bỏ.

Ảnh xem trước được vẽ giống hệt video xuất ra.

---

## 8. Tab "Xuất video"

| Tùy chọn | Gợi ý |
|---|---|
| **Độ phân giải** | *Gốc* giữ nguyên. Thấp hơn (1080p/720p/480p/360p) = file nhỏ hơn, xuất nhanh hơn |
| **FPS** | Để *Gốc* nếu không cần đổi |
| **Chất lượng** | Cao (CRF 18) · Tốt (CRF 23, mặc định) · Cân bằng (CRF 28) · Nhỏ gọn (CRF 35). Cao = nét hơn nhưng file nặng hơn |
| **Encoder preset** | Rất nhanh → Chậm. Nhanh = xuất lẹ nhưng file nặng hơn; chậm = file nhẹ hơn |
| **Audio bitrate** | 96–320 kbps; 192 kbps (mặc định) là đủ cho giọng nói |

---

## 9. Lưu video kết quả & ⚙ Cài đặt

### Nơi lưu video

- **Lưu cạnh video gốc** (mặc định): kết quả nằm cùng thư mục với video gốc.
- Bỏ tích → **Chọn…** một thư mục khác. App nhớ lựa chọn này.
- Tên file: **`<tên gốc>_vi.mp4`** (kèm **`<tên gốc>_vi.txt`** — prompt TikTok, xem §10). Nếu
  đã có file trùng tên, app đặt `… (2).mp4`, `… (3).mp4` — **không bao giờ ghi đè**.

### ⚙ Cài đặt

Nút **⚙ Cài đặt** ở góc trên bên phải (hoặc bấm chip **Groq ●**) gồm những thiết lập dùng chung
cho cả app; mọi thay đổi được **lưu lại** cho các lần mở sau:

| Mục | Nội dung |
|---|---|
| **Groq API key** | Trạng thái key, **Kiểm tra** (đèn xanh / đỏ), **🔑 Nhập API key…** (xem §1) |
| **Thông báo** | Bật / tắt thông báo khi video xong, lỗi hoặc cần bạn |
| **Prompt AI** | Hai prompt bạn có thể tùy chỉnh theo phong cách riêng (xem bên dưới) |
| **Cache** | Dung lượng cache, **📁 Workspace…**, **Dọn cache** |

#### Prompt AI

| Prompt | Dùng khi nào |
|---|---|
| **Prompt sửa bản dịch** | Đặt ở đầu khi bấm **📋 Copy cho AI** trong cửa sổ *Duyệt bản dịch* (§6.6) |
| **Prompt TikTok (tiêu đề, mô tả, hashtag)** | Ghi vào file `<tên gốc>_vi.txt` cạnh video và chép khi bấm **📋 TikTok** trên thẻ video |

- **✎ Sửa…** mở trình soạn thảo; **Lưu** để áp dụng. Bên cạnh tên hiện **mặc định** hoặc
  **đã tùy chỉnh**.
- **↺ Mặc định** (hoặc *↺ Khôi phục mặc định* trong trình soạn thảo) đưa prompt về bản gốc
  của app.
- Prompt TikTok: dòng `<<< DÁN LỜI THOẠI VÀO ĐÂY >>>` là chỗ app chèn lời thoại. Nếu bạn xóa
  dòng này, lời thoại được thêm vào cuối prompt.
- Prompt sửa bản dịch: nên giữ yêu cầu AI trả lời dạng `[12] câu tiếng Việt` để **📥 Dán kết quả
  AI** đọc được.

### Cache

Nằm trong **⚙ Cài đặt → Cache**. Dòng **"Cache: … MB · không tự xóa"** cho biết dung lượng
cache (file tạm của từng video). Cache giúp **chạy lại cùng video nhanh hơn** (không phải nhận
dạng / dịch lại).

- **Không tự xóa:** app giữ nguyên cache cho tới khi bạn bấm **Dọn cache**. Video dài có thể
  chiếm vài GB mỗi video — nhớ dọn khi ổ đĩa sắp đầy.
- **Dọn cache:** xóa toàn bộ cache (có hỏi xác nhận). Không ảnh hưởng video đã xuất và giọng
  đã clone. Nút bị khóa khi đang có video chạy.

### 📁 Workspace… (trong ⚙ Cài đặt → Cache)

Mở hộp thoại cho biết Dubber Bee lưu file ở đâu:

| Dòng | Nút |
|---|---|
| **Cache của video** | **Mở**, **Đổi…**, **Mặc định** |
| **Log & báo cáo lỗi** | **Mở**, **Báo cáo lỗi**, **Đổi…**, **Mặc định** |
| **Giọng đã clone** | **Mở** |
| **Model** | **Mở** |

- **Mở:** mở thư mục trong Finder / Explorer để xem hoặc xóa bằng tay.
- **Đổi…:** chuyển sang thư mục khác (ví dụ ổ ngoài nếu ổ máy sắp đầy). Khi đổi chỗ để cache,
  app hỏi có chuyển cache hiện có sang không.
- **Mặc định:** quay về chỗ cũ.
- Không đổi được khi đang có video chạy.

> An toàn: nếu bạn chọn một thư mục có sẵn đồ (ví dụ *Desktop*), Dubber Bee chỉ tạo và làm việc
> trong thư mục con riêng **`Dubber workspace`** / **`Dubber logs`**. Nút "Dọn cache" không
> bao giờ xóa file khác của bạn.

---

## 10. Chạy, theo dõi, dừng, hủy, chạy lại

### Chạy

Bấm **▶ Chạy hàng đợi**: các video **đang chờ** chạy lần lượt từ trên xuống.

### Tiến trình

| Bước | Việc đang làm |
|---|---|
| **Tách nhạc nền** | Tách giọng khỏi nhạc (bỏ qua nếu không cần) |
| **Nhận dạng giọng nói** | Nghe tiếng Trung, chia câu |
| **Dịch** | Dịch sang tiếng Việt |
| **Tạo giọng Việt (TTS)** | Đọc từng câu tiếng Việt (có % tiến độ) |
| **Ghép âm thanh** | Ghép giọng Việt theo đúng thời điểm |
| **Render video** | Trộn âm, chèn phụ đề, làm mờ, xuất file (có % tiến độ) |

- Tiêu đề khung *Tiến trình* hiện **% tổng**, **thời gian đã chạy** và **thời gian còn lại
  (ước tính)**; mỗi bước có thời gian đã chạy ở cuối dòng. Ước tính chính xác dần sau vài phút.
- Thời gian **chờ bạn** (duyệt bản dịch, chọn file) không tính vào thời gian chạy.
- Thẻ của video đang chạy cũng hiện bước, % tổng và thời gian còn lại.

### Thông báo

Khi video **xong**, **lỗi**, hoặc **cần bạn** (duyệt bản dịch, chọn file, Groq giới hạn), app
hiện thông báo của hệ thống và biểu tượng nhảy trên Dock (Windows: nháy trên thanh tác vụ) —
chỉ khi bạn đang ở cửa sổ khác. Tắt / bật trong **⚙ Cài đặt → Thông báo**.

### Dừng và hủy

| Nút | Tác dụng |
|---|---|
| **⏹ Dừng sau video này** | Chạy nốt video hiện tại rồi dừng hàng đợi |
| **✖ Hủy video này** | **Dừng ngay** video đang chạy (khoảng 1 giây; nếu đang tách nhạc thì đợi bước này xong), **xóa các file tạm** của video đó, và dừng hàng đợi. Video chuyển sang ⊘ "đã hủy" |

### Chạy lại

- Bấm vào video đã xong / lỗi / đã hủy → sửa cấu hình nếu muốn → **↻ Chạy lại** →
  **▶ Chạy hàng đợi**. Không cần tắt mở lại app.
- Chạy lại **cùng video, cùng đoạn cắt** sẽ nhanh hơn nhiều: app dùng lại phần đã nhận dạng,
  bản dịch (mỗi bộ dịch nhớ một bản riêng) và giọng đã đọc. Ví dụ đổi giọng đọc rồi chạy lại
  một đoạn 15 giây: 15 giây → 5 giây.

### Mở kết quả

**📂 Mở thư mục kết quả** mở nơi chứa video kết quả (của video đang chọn, hoặc video vừa xong). Mỗi
thẻ video xong còn có **▶ Xem**, **📋 TikTok**, **📂** riêng (xem *Thẻ video*, §4).

Cạnh mỗi video `<tên gốc>_vi.mp4` có file **`<tên gốc>_vi.txt`**: prompt viết sẵn cho ChatGPT
(**tiêu đề + mô tả + đúng 5 hashtag** để đăng TikTok) kèm toàn bộ lời thoại tiếng Việt. Mở file,
chép hết, dán vào ChatGPT — hoặc bấm **📋 TikTok** trên thẻ cho nhanh.

---

## 11. Các hộp thoại xuất hiện khi đang chạy

### Duyệt bản dịch

Chỉ hiện khi bạn bỏ tích *Bỏ qua* ở **Duyệt bản dịch** — xem
[§6.6](#66-duyệt-bản-dịch--nhờ-chatbot-sửa).

### "Groq rate limit"

Groq miễn phí giới hạn số lần dịch mỗi phút. Khi cả hai model của Groq đều bị chặn, app hỏi:

- **Tiếp tục Groq (đợi ~N giây):** đợi đúng thời gian Groq yêu cầu rồi dịch tiếp. Nếu sau đó
  Groq vẫn từ chối **4 lần liên tiếp**, app hỏi lại.
- **Dịch phần còn lại bằng MarianMT:** giữ các câu Groq đã dịch, phần còn lại dịch offline
  (nhanh, chất lượng kém hơn).
- **Hủy video này.**

Hộp thoại ghi rõ đã dịch được bao nhiêu câu, ví dụ *"Đã dịch 30/74 câu"*.

---

## 12. Khi có lỗi

1. Video bị lỗi chuyển sang **✗**, và một **cửa sổ lỗi** hiện ra (không chặn app, hàng đợi vẫn
   chạy tiếp các video khác):
   - dòng đỏ: lỗi là gì;
   - khung dưới: **báo cáo đầy đủ** (có thể bôi đen để chép);
   - **Copy lỗi:** chép toàn bộ báo cáo — dán vào tin nhắn gửi người hỗ trợ;
   - **Mở báo cáo lỗi:** mở file báo cáo đã lưu.
2. Đóng cửa sổ rồi muốn xem lại: **bấm đúp vào video bị lỗi** trong hàng đợi.
3. Mỗi lỗi được lưu thành một file `.txt` trong thư mục **Báo cáo lỗi**
   (*📁 Workspace… → Báo cáo lỗi*). File gồm thông báo lỗi, chi tiết kỹ thuật, cấu hình
   video đã chạy và nhật ký gần nhất — **không chứa Groq API key**. App giữ lại mọi báo cáo (xóa tay trong thư mục khi cần).

Sau khi xử lý nguyên nhân, bấm **↻ Chạy lại**.

---

## 13. Dữ liệu được lưu ở đâu

Cách nhanh nhất: **⚙ Cài đặt → 📁 Workspace…** → **Mở**. Đường dẫn mặc định:

| Dữ liệu | macOS | Windows |
|---|---|---|
| Video kết quả `_vi.mp4` + prompt TikTok `_vi.txt` | Cạnh video gốc hoặc thư mục bạn chọn | như macOS |
| File tạm | `~/Library/Application Support/dubber/jobs` | `C:\Users\<tên>\AppData\Local\dubber\dubber\jobs` |
| Giọng đã clone | `~/Library/Application Support/dubber/voices/vieneu` | `…\AppData\Local\dubber\dubber\voices\vieneu` |
| Log, báo cáo lỗi | `~/Library/Logs/dubber` | `…\AppData\Local\dubber\dubber\Logs` |
| Câu nghe thử | `~/Library/Caches/dubber/voice-demos` | `…\AppData\Local\dubber\dubber\Cache` |
| Model dịch offline (NLLB, MarianMT) | `~/.cache/huggingface/hub` | `C:\Users\<tên>\.cache\huggingface\hub` |
| Cấu hình app | `~/Library/Preferences/dubber/settings.json` | `…\AppData\Local\dubber\dubber\settings.json` |

macOS: trong Finder bấm **Cmd + Shift + G**, dán đường dẫn để mở nhanh.

---

## 14. Mẹo để có kết quả tốt

- **Thử trước một đoạn ngắn** (§6.8, 20–30 giây) để chọn giọng, âm lượng, vị trí phụ đề, rồi
  mới chạy cả video.
- Video **có nhạc to**: để bật *Lọc nhạc trước khi nhận diện*.
- **Groq** cho bản dịch hay nhất; video dài hay dịch nhiều → có thể gặp giới hạn (§11).
- Muốn giọng **tự nhiên**: VieNeu-TTS; muốn **nhanh**: Piper. Bấm **▶ Nghe thử** trước khi chọn.
- Clone giọng: **6–8 giây sạch, một người nói** quan trọng hơn file dài.
- Video có **phụ đề Trung sẵn** trong hình: chọn *Che phụ đề gốc → Tự động* và đặt phụ đề Việt đè lên
  vùng chữ Trung (tăng *Lề vùng làm mờ* nếu còn lộ chữ), hoặc dùng *Thủ công* để tự kéo vùng che.
- Giọng Việt bị lấn: giảm nhạc nền / tiếng Trung, đừng tăng giọng Việt quá 100% (dễ rè).

---

## 15. Câu hỏi thường gặp & xử lý sự cố

**"Chưa có Groq API key" / dòng Groq API key báo "Chưa có"**
Đặt khóa: **⚙ Cài đặt → Groq API key → 🔑 Nhập API key…** (§1). Bấm **Kiểm tra**: nếu báo "Groq từ chối
khóa này" thì khóa sai hoặc đã bị thu hồi — tạo khóa mới trên console.groq.com.

**Hộp thoại báo "Không lưu được vào kho mật khẩu…"**
Kho mật khẩu của máy đang bị khóa / không truy cập được. Khóa vẫn dùng được tới khi tắt app;
mở khóa Keychain (macOS) rồi lưu lại.

**Lần đầu chạy rất chậm**
Lần đầu dùng một model, app phải nạp (và có thể tải) model: VieNeu (~610 MB nếu bản cài chưa
có), NLLB (~5,5 GB). Các lần sau nhanh hơn nhiều.

**Không chọn được VieNeu-TTS ("— chưa cài")**
Bản cài thiếu VieNeu. Bản dev: `pip install -e ".[vieneu]"`. Bản đóng gói đầy đủ đã có sẵn.

**"Lọc nhạc trước khi nhận diện" bị mờ, không bật được**
Thiếu bộ tách nhạc (audio-separator). Bản dev: `pip install -e ".[separation]"`. App vẫn chạy,
chỉ nhận diện trên âm thanh gốc.

**"Đoạn cắt không hợp lệ…"**
Đoạn cắt sai (bắt đầu sau kết thúc, hoặc ngoài độ dài video). Mở tab Giọng & dịch, kiểm tra ô
*Chỉ xử lý một đoạn* (thanh kéo sẽ tự sửa khi bạn nhấn Enter), hoặc bỏ tích để chạy cả video.

**Giọng Việt đọc nhanh / bị đè lên câu sau**
Câu tiếng Việt dài hơn khoảng thời gian câu tiếng Trung. App tự tăng tốc tối đa 1,3× và đẩy câu
sau tối đa 0,5 giây. Bỏ tích *Bỏ qua* ở **Duyệt bản dịch** rồi rút gọn các câu ⚠ dài (hoặc
nhờ chatbot rút gọn) sẽ đỡ hơn.

**Phụ đề Việt đè chữ Trung / lệch chỗ**
Dùng *Chọn vị trí…* và *Chọn vùng…* trong tab Phụ đề (§7).

**Bấm "▶ Nghe thử" không nghe thấy gì**
Kiểm tra âm lượng máy / tai nghe. Nếu báo "Không tạo được câu mẫu", xem nội dung lỗi; với
VieNeu lần đầu có thể cần mạng để tải model.

**Ổ đĩa sắp đầy**
Bấm **Dọn cache**, hoặc dùng *📁 Workspace… → Đổi…* chuyển file tạm sang ổ khác. Model
dịch offline NLLB (~5,5 GB) nằm trong `~/.cache/huggingface/hub` — xóa được nếu không dùng NLLB
(lần sau chọn NLLB sẽ tải lại).

**Tắt / mở lại app đúng cách**
Tắt bằng **Cmd + Q** (macOS) hoặc nút đóng cửa sổ. Nên đợi cửa sổ đóng hẳn rồi mới mở lại, để
không có hai cửa sổ Dubber Bee chạy cùng lúc. Nếu đang có video chạy, nên **⏹ Dừng sau video này**
hoặc **✖ Hủy video này** trước khi tắt.

**App không tắt khi bị đóng từ bên ngoài (Terminal `kill`, công cụ khác)**
App không phản hồi lệnh tắt gửi từ bên ngoài khi đang mở cửa sổ. Hãy tắt bằng Cmd + Q. Nếu app
bị treo hẳn: macOS **Cmd + Option + Esc → chọn Python / Dubber Bee → Buộc thoát**; Windows
**Ctrl + Shift + Esc → Task Manager → End task**. Video đang chạy dở sẽ phải chạy lại.

**Lỗi khác**
Mở cửa sổ lỗi (bấm đúp vào video ✗), bấm **Copy lỗi** và gửi cho người hỗ trợ kỹ thuật.
