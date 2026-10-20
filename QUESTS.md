# Test quest tool — `main.py`

Tool này **khác** bot. Nó cần **USER token** (token tài khoản Discord của bạn), không phải bot token.

---

## 1. Hiểu đúng hai loại token

| | bot token | user token |
|---|---|---|
| Lấy ở đâu | Developer Portal → Bot | Trình duyệt, tab Network |
| Dùng header | `Authorization: Bot ...` | `Authorization: ...` (không có `Bot`) |
| Gọi được `/quests/@me` | ❌ 401 | ✅ |
| Dùng cho `main.py` | ❌ | ✅ |
| Dùng cho `bot.py` | ✅ | ❌ |

`main.py` giờ kiểm tra hộ: nếu bạn đưa bot token nó in ngay

```
Đây là BOT token, không dùng cho quest.
```

> **Rủi ro thật:** dùng user token qua HTTP là trái ToS Discord. Tài khoản có thể bị khoá. Chạy trên máy mình, đừng đưa lên host công khai, đừng chia sẻ token.

---

## 2. Lấy user token

**Cách A — DevTools (chuẩn nhất)**

1. Mở [discord.com/app](https://discord.com/app), đăng nhập
2. `Ctrl+Shift+I` → tab **Network**
3. Bấm vào bất kỳ channel nào để Discord gửi request
4. Chọn một request bất kỳ gửi tới `discord.com/api/v9/...`
5. Panel phải → **Request Headers** → copy giá trị `authorization`

Chuỗi đó dài ~70 ký tự, dạng `MTA....GhiJKL.abc...`

**Cách B — Console**

```js
(webpackChunkdiscord_app.push([[''],{},e=>{m=[];for(let c in e.c)m.push(e.c[c])}]),m).find(m=>m?.exports?.default?.getToken!==void 0).exports.default.getToken()
```

Copy kết quả. Với token lấy từ app chính thức, token đôi khi có tiền tố `MT`/`MTA` — giữ nguyên.

**Kiểm tra token sống:**

```powershell
curl.exe -H "Authorization: <DÁN_TOKEN>" https://discord.com/api/v10/users/@me
```

Ra JSON có `"username"` và **không** có `"bot": true` = đúng loại token.

---

## 3. Đặt token vào `.env`

```powershell
cd "D:\Bot Disscord"
notepad .env
```

Thêm dòng:

```
QUEST_TOKEN=<dán user token>
```

Đừng dùng `DISCORD_TOKEN` cho việc này — biến đó đã dành cho bot token, trộn vào là sai.

---

## 4. Test an toàn — dry-run trước

**Không bao giờ** chạy thẳng lần đầu. Quét thử trước:

```powershell
.\.venv\Scripts\python.exe main.py --once --dry-run
```

Nó chỉ đọc, in kế hoạch, **không nhận quest, không gửi progress**:

```
[INFO] Đăng nhập: tên_bạn (ID: ...)
[INFO] ── Quét lần #1 ──
[INFO] Tổng: 4 quest | Enrolled: 1 | Completed: 1 | Completable: 2
[INFO]   ✓ Quest A [WATCH_VIDEO]
[INFO]   ✓ Quest B [PLAY_ON_DESKTOP]
[INFO]   ▶ Quest C [WATCH_VIDEO]
[INFO]   ○ Quest D [PLAY_ON_DESKTOP]
[WARN] DRY RUN: sẽ nhận 1 quest:
[PROG]   + Quest D [PLAY_ON_DESKTOP]
[WARN] DRY RUN: sẽ hoàn thành 1 quest:
[PROG]   ~ Quest C (120/300s)
```

Không thấy gì thay đổi trên tài khoản = dry-run đúng.

---

## 5. Chạy thật

**Một lần rồi thoát:**

```powershell
.\.venv\Scripts\python.exe main.py --once
```

**Auto scan liên tục (mặc định 60 giây/lần):**

```powershell
.\.venv\Scripts\python.exe main.py
```

**Đổi nhịp quét:**

```powershell
.\.venv\Scripts\python.exe main.py --interval 180
```

`Ctrl+C` để dừng. Tiến độ ghi thẳng vào Discord, mở app lên xem quest nhảy số.

---

## 6. Các loại quest và cách nó xử lý

| Task | Cơ chế | Thời gian |
|---|---|---|
| `WATCH_VIDEO` | gửi `video-progress`, timestamp tăng ~7s/lần | nhanh, ~1 phút cho quest 5 phút |
| `WATCH_VIDEO_ON_MOBILE` | y như trên | nhanh |
| `PLAY_ON_DESKTOP` | `heartbeat` mỗi 20s | đúng bằng thời lượng quest |
| `STREAM_ON_DESKTOP` | `heartbeat` mỗi 20s | như trên |
| `PLAY_ACTIVITY` | `heartbeat` mỗi 20s | như trên |

Quest 15 phút = chờ đủ 15 phút. Không có đường tắt — Discord đếm theo thời gian thực.

---

## 7. Khi có lỗi

| Log | Nghĩa | Sửa |
|---|---|---|
| `Token không hợp lệ (status 401)` | token chết | lấy token mới, cập nhật `.env` |
| `Đây là BOT token` | nhầm loại | dùng user token |
| `Rate limited – chờ Ns` | bị chặn tạm | tool tự chờ, không làm gì |
| `Heartbeat lỗi (400)` | quest không còn hợp lệ | bỏ qua, tool tự sang quest khác |
| `Quest fetch lỗi (403)` | token bị hạn chế | đăng nhập lại trên trình duyệt, lấy token mới |
| Lặp mãi không xong | quest hết hạn giữa chừng | dừng, chạy lại |

---

## 8. Chạy nền trên Windows (tuỳ chọn)

```powershell
Start-Process -WindowStyle Hidden -FilePath ".venv\Scripts\python.exe" -ArgumentList "main.py","--interval","300" -WorkingDirectory "D:\Bot Disscord"
```

Dừng:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Không chạy `main.py` trên botkeep cùng bot — token khác nhau, nhưng panel chỉ 1 slot và user token không nên nằm trên host.

---

## 9. Thứ tự test đề nghị

```
1. curl /users/@me        -> token sống, không phải bot
2. main.py --dry-run      -> thấy danh sách quest, không thay đổi gì
3. main.py --once         -> một quest thật chạy xong
4. mở Discord kiểm tra    -> quest đã hoàn thành
5. main.py --interval 300 -> chạy dài, để đó
```

---

## 10. Dùng trong Discord — `/scan` và `/quest`

Bot có thêm hai lệnh owner-only gọi cùng API đó, để khỏi phải mở terminal.

### Bật

Thêm vào env của bot (local `.env` hoặc `Environment` trên botkeep):

```
QUEST_TOKEN=<user token>
```

Restart bot. Log đúng:

```
INFO  bot.quests: Quest client ready (client_build_number=641803).
INFO  bot: Quest commands enabled (QUEST_TOKEN present).
```

Thiếu biến đó log ra:

```
WARNING bot: Quest commands disabled: thiếu `QUEST_TOKEN`. /scan and /quest will explain the fix.
```

Lệnh vẫn đăng ký, chỉ in hướng dẫn khi bấm.

### `/scan`

Quét một lần, **chỉ đọc** — không nhận, không gửi progress. Trả embed:

```
Quest scan
4 quest · 1 đã nhận · 2 hoàn thành · 4 khả dụng

✓ Watch a video      WATCH_VIDEO · 300/300s
▶ Play a game        PLAY_ON_DESKTOP · 120/900s
○ New quest          STREAM_ON_DESKTOP · 0/300s
```

`show_completed: false` (mặc định) ẩn quest đã xong. Chỉ owner thấy — dữ liệu quest là của tài khoản bạn.

Giới hạn 25 field mỗi embed; nhiều hơn thì footer ghi `+N quest không hiển thị`.

### `/quest start`

Bật worker nền: quét → nhận → hoàn thành, lặp lại.

```
/quest start interval:300 notify:true
```

- `interval` 60–3600 giây, mặc định 300
- `notify: true` gửi embed `Quest hoàn thành` vào channel hiện tại mỗi khi xong quest
- Worker chỉ có một: `start` lần hai bị từ chối cho tới khi `stop`

### `/quest stop`

Huỷ task, đợi nó dọn xong. Gọi lại khi đang không chạy là no-op.

### `/quest status`

```
Quest worker
Trạng thái: đang chạy
Bật lúc: 5 phút trước
Nhịp quét: 300s
Lần quét: 12
Quest đã xong: 3
Quét gần nhất: 2 phút trước
Lỗi gần nhất: HTTP 401: {"message": "401: Unauthorized"}
```

### Khác biệt với `main.py`

| | `main.py` | `/scan`, `/quest` |
|---|---|---|
| Chạy ở đâu | máy bạn | botkeep / host bot |
| Token nằm đâu | máy bạn | env của host |
| Dừng thế nào | Ctrl+C | `/quest stop` |
| Rủi ro | thấp | host đọc được user token |

Cơ chế bên dưới giống hệt — bot gọi `QuestAutocompleter.scan_once()` trong worker thread, cùng hàm `main.py` dùng.

### Lỗi thường gặp

| Log / embed | Nghĩa | Sửa |
|---|---|---|
| `Quest chưa cấu hình — thiếu QUEST_TOKEN` | env trống | thêm biến, restart |
| `Lỗi fetch: HTTP 401` | token chết | lấy token mới |
| `Quest fetch lỗi (403)` | token bị hạn chế | đăng nhập lại trên web, lấy token mới |
| `Worker đang chạy` | start hai lần | `/quest stop` trước |
| Embed trống, `0 quest` | tài khoản không có quest khả dụng | bình thường, chờ quest mới |

> Quest `PLAY_ON_DESKTOP` / `STREAM_ON_DESKTOP` chạy đúng bằng thời lượng thật (15 phút = 15 phút). Trong lúc đó `/scan` sẽ chờ vì cùng một lock — `/quest status` thì vẫn trả lời ngay.
