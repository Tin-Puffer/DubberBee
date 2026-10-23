# Hướng dẫn sử dụng Dubber

Dubber tự động **lồng tiếng Việt cho video tiếng Trung**: nghe tiếng Trung trong video, dịch
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
9. [Lưu video kết quả, bộ nhớ tạm, thư mục làm việc](#9-lưu-video-kết-quả-bộ-nhớ-tạm-thư-mục-làm-việc)
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

### Khóa Groq (chỉ cần nếu dịch bằng Groq)

Groq là dịch vụ dịch online, chất lượng tốt nhất, miễn phí có giới hạn.

1. Lấy khóa tại [console.groq.com/keys](https://console.groq.com/keys) → *Create API Key* →
   sao chép khóa (dạng `gsk_…`).
2. Trong Dubber: tab **Giọng & dịch** → dòng **Khóa Groq** → bấm **🔑 Đặt khóa Groq…**
3. Dán khóa vào ô (bấm **Hiện** để xem lại), bấm **Kiểm tra** để chắc khóa dùng được, rồi **Lưu**.

Xong — chỉ cần làm **một lần**, app nhớ khóa cho mọi lần mở sau. Dòng *Khóa Groq* hiện
**"✓ Đã lưu (gsk_…abcd)"** (chỉ hiện 4 ký tự đầu và cuối).

- Khóa được cất trong **kho mật khẩu của máy** (macOS Keychain / Windows Credential Manager),
  không nằm trong file nào của Dubber, không ghi vào log hay báo cáo lỗi.
- Muốn bỏ khóa: mở lại hộp thoại → **Xóa khóa đã lưu**.
- Người dùng nâng cao vẫn có thể đặt biến môi trường `GROQ_API_KEY`; khóa lưu trong app được
  ưu tiên hơn.

---

## 2. Làm quen với màn hình

```
┌────────────────────────────── Dubber  ZH → VI ──────────────────────────────┐
│ [ Kéo thả video vào đây … hoặc click để chọn ]  │  Cấu hình cho <video>      │
│                                                 │  [Âm thanh][Giọng & dịch]  │
│ Hàng đợi                                        │  [Phụ đề][Xuất video]      │
│  ⏳ video1.mp4                                   │   … các tùy chọn …         │
│  ✓ video2.mp4                                   │                            │
│ [Áp dụng cho tất cả][↻ Chạy lại][Xoá][Xoá xong] │  Tiến trình                │
│                                                 │   ○ Tách nhạc/giọng        │
│ Thông tin video gốc                             │   ○ Nhận dạng giọng nói    │
│ Lưu video kết quả                               │   ○ Dịch thuật …           │
│  Bộ nhớ tạm … [📁 Thư mục làm việc…][Dọn ngay]  │                            │
├─────────────────────────────────────────────────┴────────────────────────────┤
│ [▶ Chạy hàng đợi] [⏹ Dừng sau video này] [✖ Hủy video này] [📂 Mở thư mục]    │
│ dòng trạng thái                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

- **Bên trái:** thêm video, danh sách hàng đợi, thông tin video đang chọn, nơi lưu kết quả.
- **Bên phải:** cấu hình của **video đang chọn** (4 tab) và tiến trình chạy.
- **Dưới cùng:** nút chạy / dừng / hủy / mở thư mục kết quả, và dòng trạng thái.

Dưới mỗi lựa chọn có **một dòng chữ nhỏ** giải thích chọn như vậy được gì, mất gì. Rê chuột
lên lựa chọn cũng thấy giải thích đó.

Ký hiệu trong hàng đợi: **⏳** đang chờ · **▶** đang chạy · **✓** xong · **✗** lỗi · **⊘** đã hủy.

---

## 3. Lồng tiếng nhanh trong 5 bước

1. **Kéo thả video** vào ô "Kéo thả video vào đây" (hoặc bấm vào ô để chọn file).
2. Tab **Giọng & dịch**: chọn **Bộ đọc** và **Giọng đọc** (bấm **▶ Nghe thử** để nghe), chọn
   **Dịch thuật** (mặc định "Tự động").
3. Tab **Âm thanh**: chỉnh độ to giọng Việt / nhạc nền / tiếng Trung nếu muốn
   (mặc định: giọng Việt 100%, nhạc nền 40%, tiếng Trung 20%).
4. Bấm **▶ Chạy hàng đợi** và theo dõi phần **Tiến trình**.
5. Xong, bấm **📂 Mở thư mục** để thấy video `<tên gốc>_vi.mp4`.

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
- **Áp dụng cấu hình này cho tất cả:** chép cấu hình đang hiện cho mọi video **đang chờ**.
- Video **đang chạy** không sửa được cấu hình. Video đã xong / lỗi / đã hủy vẫn sửa được.

### Các nút dưới hàng đợi

| Nút | Tác dụng |
|---|---|
| **Áp dụng cấu hình này cho tất cả** | Chép cấu hình cho mọi video đang chờ |
| **↻ Chạy lại** | Đưa video đang chọn (đã xong / lỗi / đã hủy) về hàng đợi |
| **Xoá** | Bỏ video đang chọn khỏi hàng đợi (không xóa file gốc) |
| **Xoá xong/lỗi** | Bỏ khỏi danh sách mọi video đã xong / lỗi / đã hủy |

### Thông tin video gốc

Khi chọn một video: độ phân giải, số khung hình/giây, thời lượng, dung lượng, mã hóa video
và âm thanh của file gốc.

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
  nhạc khỏi giọng bằng AI** (bước "Tách nhạc/giọng" trong Tiến trình) — chậm hơn một chút.
  Hai mức bằng nhau thì dùng thẳng âm thanh gốc, không cần tách.

---

## 6. Tab "Giọng & dịch"

### 6.1 Bộ đọc

| Bộ đọc | Ưu | Nhược |
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

1. Chọn **Bộ đọc: VieNeu-TTS**, rồi trong ô Giọng đọc chọn **➕ Clone giọng mới…**
2. Bấm **Chọn…** và chọn file **ghi âm hoặc video** (`.wav .mp3 .m4a .flac .ogg .mp4 .mkv
   .mov .avi .webm`). Hộp thoại hiện **Thời lượng**.
3. **Bắt đầu từ:** nếu file dài hơn 8 giây, chọn giây bắt đầu của đoạn mẫu. App chỉ dùng
   **8 giây** tính từ mốc này (VieNeu chỉ dùng tối đa 8 giây — đưa dài hơn không tốt hơn).
   Ngắn hơn 3 giây sẽ có cảnh báo "Đoạn quá ngắn, giọng sẽ kém giống".
4. **▶ Nghe thử:** nghe đúng đoạn 8 giây sẽ dùng.
5. **🔊 Đọc thử:** nghe câu *"Xin chào, đây là giọng đọc thử nghiệm."* bằng giọng mới
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

### 6.5 Dịch thuật

| Lựa chọn | Khi nào dùng |
|---|---|
| **Tự động (Groq → offline)** | Mặc định. Dùng Groq; mất mạng / không có khóa / lỗi thì tự chuyển sang MarianMT |
| **Groq (online)** | Dịch hay nhất. Cần mạng và khóa Groq (dòng **Khóa Groq** → **🔑 Đặt khóa Groq…**, xem §1) |
| **Offline — MarianMT (~300 MB)** | Không cần mạng, nhanh, dịch kém hơn |
| **Offline — NLLB 1.3B (~5.5 GB)** | Không cần mạng, chậm, lần đầu tải ~5,5 GB, cần ~6 GB RAM. Chất lượng thực tế vẫn kém Groq rõ rệt |
| **Thủ công — tự dịch file** | Bạn tự dịch (hoặc nhờ người / công cụ khác), app đọc theo bản dịch của bạn |

### 6.6 Dịch thủ công — từng bước

1. Chọn **Dịch thuật: Thủ công — tự dịch file**, rồi **▶ Chạy hàng đợi**.
2. Sau bước nhận dạng giọng nói, app xuất file **`<tên video>_zh.txt`** vào **nơi lưu video
   kết quả** (mặc định: cạnh video gốc) và hiện hộp thoại **"Dịch thủ công"**:
   - **Mở file:** mở file tiếng Trung bằng TextEdit / Notepad.
   - **Mở thư mục:** mở nơi chứa file.
3. File tiếng Trung có **mỗi dòng một câu**. Dịch sang tiếng Việt và lưu thành một file
   **`.txt` khác**, **mỗi dòng một câu, đúng thứ tự, đủ số dòng** (dòng 1 là câu 1…).
   Ví dụ file tiếng Trung có 21 dòng thì file tiếng Việt cũng phải có 21 dòng (dòng trống
   không tính).
   - TextEdit (macOS): trước khi lưu chọn **Định dạng → Tạo văn bản thường**, nếu không
     TextEdit sẽ lưu `.rtf`.
   - Có thể đánh số đầu dòng ("1. …", "2) …") — app tự bỏ số.
4. Trong hộp thoại bấm **Chọn file tiếng Việt…** và chọn file vừa lưu. App chạy tiếp.
5. Nếu file sai số dòng, hộp thoại hiện lại với dòng chữ đỏ, ví dụ *"File có 19 dòng, cần đúng
   21 dòng"* — sửa file rồi chọn lại.
6. Lần sau chạy lại **cùng video, cùng đoạn cắt**, hộp thoại có thêm nút **Dùng lại bản dịch
   trước**.
7. Muốn bỏ video này: bấm **Hủy video này**.

> Trong lúc hộp thoại đang mở, video đứng chờ bạn; bạn có thể dịch bao lâu cũng được.

### 6.7 Nhận diện — "Lọc nhạc trước khi nhận diện"

Bật (mặc định): app tách giọng nói khỏi nhạc nền trước khi nghe tiếng Trung → **nghe chính xác
hơn với video có nhạc**, chậm hơn một chút (~25 giây với video 2 phút). Nên để bật.

### 6.8 Cắt video — chỉ xử lý một đoạn

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
| **Đốt phụ đề tiếng Việt vào video** | Chèn phụ đề Việt vào hình (luôn hiện, không tắt được khi xem) |
| **Font chữ** | Arial, Be Vietnam Pro, Roboto, Montserrat, Nunito, Lexend, Noto Sans |
| **Cỡ chữ** | 20–120 (tính trên khung 1080p, tự co theo độ phân giải video) |
| **Xem trước trên video…** | Mở khung xem trước đúng như video xuất ra |
| **Vị trí** | *Mặc định (giữa, sát đáy)* hoặc *Tự chọn trên khung hình* → **Chọn vị trí…** |
| **Màu chữ / Màu viền** | Bấm để chọn màu (mặc định chữ trắng, viền đen) |
| **Độ dày viền** | 0–8 px (0 = không viền) |
| **Nền chữ** | *Hộp nền tối sau chữ* — thêm hộp đen mờ sau chữ như kiểu cũ |
| **Che sub gốc** | Chọn **một** trong ba: *Tắt*; *Tự động* — vùng mờ ôm dòng phụ đề Việt dài nhất (cộng **Lề vùng mờ**), hiện suốt video; *Thủ công* → **Chọn vùng…** — tự kéo một vùng cố định suốt video |
| **Lề vùng mờ** | 0–40 px mỗi phía quanh dòng phụ đề (chỉ cho chế độ *Tự động*) |

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
- **Xoá vùng mờ** / **Phụ đề về mặc định**.
- **Xong** để lưu, **Huỷ** để bỏ.

Ảnh xem trước được vẽ giống hệt video xuất ra.

---

## 8. Tab "Xuất video"

| Tùy chọn | Gợi ý |
|---|---|
| **Độ phân giải** | *Gốc* giữ nguyên. Thấp hơn (1080p/720p/480p/360p) = file nhỏ hơn, xuất nhanh hơn |
| **Khung hình/giây** | Để *Gốc* nếu không cần đổi |
| **Chất lượng** | Cao (CRF 18) · Tốt (CRF 23, mặc định) · Cân bằng (CRF 28) · Nhỏ gọn (CRF 35). Cao = nét hơn nhưng file nặng hơn |
| **Tốc độ mã hoá** | Rất nhanh → Chậm. Nhanh = xuất lẹ nhưng file nặng hơn; chậm = file nhẹ hơn |
| **Âm thanh** | 96–320 kbps; 192 kbps (mặc định) là đủ cho giọng nói |

---

## 9. Lưu video kết quả, bộ nhớ tạm, thư mục làm việc

### Nơi lưu video

- **Lưu cạnh video gốc** (mặc định): kết quả nằm cùng thư mục với video gốc.
- Bỏ tích → **Chọn…** một thư mục khác. App nhớ lựa chọn này.
- Tên file: **`<tên gốc>_vi.mp4`**. Nếu đã có file trùng tên, app đặt `… (2).mp4`, `… (3).mp4`
  — **không bao giờ ghi đè**.

### Bộ nhớ tạm

Dòng **"Bộ nhớ tạm: … MB · tự dọn sau 7 ngày / 5 GB"** cho biết dung lượng file tạm. File tạm
giúp **chạy lại cùng video nhanh hơn** (không phải nhận dạng / dịch lại).

- **Tự động dọn:** sau mỗi lần chạy app tự xóa phần lớn file tạm (video 2 phút: ~200 MB còn
  ~50 MB), và chỉ giữ tối đa **10 video gần nhất, tổng ≤ 5 GB, không quá 7 ngày**.
- **Dọn ngay:** xóa toàn bộ file tạm (có hỏi xác nhận). Không ảnh hưởng video đã xuất và giọng
  đã clone. Nút bị khóa khi đang có video chạy.

### 📁 Thư mục làm việc…

Mở hộp thoại cho biết Dubber lưu file ở đâu:

| Dòng | Nút |
|---|---|
| **File tạm của video** | **Mở**, **Đổi…**, **Mặc định** |
| **Log & báo cáo lỗi** | **Mở**, **Báo cáo lỗi**, **Đổi…**, **Mặc định** |
| **Giọng đã clone** | **Mở** |
| **Model đã tải** | **Mở** |

- **Mở:** mở thư mục trong Finder / Explorer để xem hoặc xóa bằng tay.
- **Đổi…:** chuyển sang thư mục khác (ví dụ ổ ngoài nếu ổ máy sắp đầy). Khi đổi chỗ để file
  tạm, app hỏi có chuyển các file tạm hiện có sang không.
- **Mặc định:** quay về chỗ cũ.
- Không đổi được khi đang có video chạy.

> An toàn: nếu bạn chọn một thư mục có sẵn đồ (ví dụ *Desktop*), Dubber chỉ tạo và làm việc
> trong thư mục con riêng **`Dubber workspace`** / **`Dubber logs`**. Việc tự dọn và nút
> "Dọn ngay" không bao giờ xóa file khác của bạn.

---

## 10. Chạy, theo dõi, dừng, hủy, chạy lại

### Chạy

Bấm **▶ Chạy hàng đợi**: các video **đang chờ** chạy lần lượt từ trên xuống.

### Tiến trình

| Bước | Việc đang làm |
|---|---|
| **Tách nhạc/giọng** | Tách giọng khỏi nhạc (bỏ qua nếu không cần) |
| **Nhận dạng giọng nói** | Nghe tiếng Trung, chia câu |
| **Dịch thuật** | Dịch sang tiếng Việt |
| **Tổng hợp giọng Việt** | Đọc từng câu tiếng Việt (có % tiến độ) |
| **Ghép âm thanh** | Ghép giọng Việt theo đúng thời điểm |
| **Xuất video cuối** | Trộn âm, chèn phụ đề, làm mờ, xuất file (có % tiến độ) |

Dòng trạng thái dưới cùng cho biết video nào đang ở bước nào.

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

**📂 Mở thư mục** mở nơi chứa video kết quả (của video đang chọn, hoặc video vừa xong).

---

## 11. Các hộp thoại xuất hiện khi đang chạy

### Dịch thủ công

Xem [§6.6](#66-dịch-thủ-công--từng-bước).

### "Groq đang giới hạn số request"

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
   - **Sao chép lỗi:** chép toàn bộ báo cáo — dán vào tin nhắn gửi người hỗ trợ;
   - **Mở file lỗi:** mở file báo cáo đã lưu.
2. Đóng cửa sổ rồi muốn xem lại: **bấm đúp vào video bị lỗi** trong hàng đợi.
3. Mỗi lỗi được lưu thành một file `.txt` trong thư mục **Báo cáo lỗi**
   (*📁 Thư mục làm việc… → Báo cáo lỗi*). File gồm thông báo lỗi, chi tiết kỹ thuật, cấu hình
   video đã chạy và nhật ký gần nhất — **không chứa khóa Groq**. App giữ 30 báo cáo mới nhất.

Sau khi xử lý nguyên nhân, bấm **↻ Chạy lại**.

---

## 13. Dữ liệu được lưu ở đâu

Cách nhanh nhất: **📁 Thư mục làm việc…** → **Mở**. Đường dẫn mặc định:

| Dữ liệu | macOS | Windows |
|---|---|---|
| Video kết quả, file `_zh.txt` | Cạnh video gốc hoặc thư mục bạn chọn | như macOS |
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
- Video có **phụ đề Trung sẵn** trong hình: chọn *Che sub gốc → Tự động* và đặt phụ đề Việt đè lên
  vùng chữ Trung (tăng *Lề vùng mờ* nếu còn lộ chữ), hoặc dùng *Thủ công* để tự kéo vùng che.
- Giọng Việt bị lấn: giảm nhạc nền / tiếng Trung, đừng tăng giọng Việt quá 100% (dễ rè).

---

## 15. Câu hỏi thường gặp & xử lý sự cố

**"Chưa có khóa Groq" / dòng Khóa Groq báo "Chưa có"**
Đặt khóa: tab **Giọng & dịch → 🔑 Đặt khóa Groq…** (§1). Bấm **Kiểm tra**: nếu báo "Groq từ chối
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

**Dịch thủ công báo "File có X dòng, cần đúng Y dòng"**
File tiếng Việt phải có đúng số dòng như file `_zh.txt` (dòng trống không tính). Kiểm tra có
gộp / tách nhầm câu không, có lưu đúng dạng văn bản thường (`.txt`) không.

**Giọng Việt đọc nhanh / bị đè lên câu sau**
Câu tiếng Việt dài hơn khoảng thời gian câu tiếng Trung. App tự tăng tốc tối đa 1,3× và đẩy câu
sau tối đa 0,5 giây. Với dịch thủ công, viết câu ngắn gọn hơn sẽ đỡ hơn.

**Phụ đề Việt đè chữ Trung / lệch chỗ**
Dùng *Chọn vị trí…* và *Chọn vùng…* trong tab Phụ đề (§7).

**Bấm "▶ Nghe thử" không nghe thấy gì**
Kiểm tra âm lượng máy / tai nghe. Nếu báo "Không tạo được câu mẫu", xem nội dung lỗi; với
VieNeu lần đầu có thể cần mạng để tải model.

**Ổ đĩa sắp đầy**
Bấm **Dọn ngay**, hoặc dùng *📁 Thư mục làm việc… → Đổi…* chuyển file tạm sang ổ khác. Model
dịch offline NLLB (~5,5 GB) nằm trong `~/.cache/huggingface/hub` — xóa được nếu không dùng NLLB
(lần sau chọn NLLB sẽ tải lại).

**Tắt / mở lại app đúng cách**
Tắt bằng **Cmd + Q** (macOS) hoặc nút đóng cửa sổ. Nên đợi cửa sổ đóng hẳn rồi mới mở lại, để
không có hai cửa sổ Dubber chạy cùng lúc. Nếu đang có video chạy, nên **⏹ Dừng sau video này**
hoặc **✖ Hủy video này** trước khi tắt.

**App không tắt khi bị đóng từ bên ngoài (Terminal `kill`, công cụ khác)**
App không phản hồi lệnh tắt gửi từ bên ngoài khi đang mở cửa sổ. Hãy tắt bằng Cmd + Q. Nếu app
bị treo hẳn: macOS **Cmd + Option + Esc → chọn Python / Dubber → Buộc thoát**; Windows
**Ctrl + Shift + Esc → Task Manager → End task**. Video đang chạy dở sẽ phải chạy lại.

**Lỗi khác**
Mở cửa sổ lỗi (bấm đúp vào video ✗), bấm **Sao chép lỗi** và gửi cho người hỗ trợ kỹ thuật.
