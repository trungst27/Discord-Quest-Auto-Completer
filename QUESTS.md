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

## 10. Dùng trong Discord — mỗi người tự đăng nhập

Bot không giữ token trong env nữa. Mỗi người dùng `/quest login`, dán **user token của chính mình**, bot mã hoá rồi lưu. Lệnh quest chạy trên đúng tài khoản của người gọi lệnh.

### Đăng nhập

```
/quest login
```

Discord mở form riêng tư. Dán user token vào ô. Bot kiểm tra với Discord trước khi lưu:

- `GET /users/@me` trả 200 và `bot: false` → nhận
- token thuộc tài khoản khác với người gọi → từ chối
- token chết → từ chối, kèm mã lỗi

Thành công:

```
Đã lưu token cho SUJILAY.
Dùng /scan để xem quest, /quest start để chạy nền.
```

Token **không bao giờ** hiện lại trong chat, không nằm trong embed, không ghi ra log.

### Lưu ở đâu

```
quest-tokens.json    token đã mã hoá (Fernet) + cấu hình
quest-secret.key     khoá, tạo tự động nếu thiếu QUEST_SECRET
```

Cả hai nằm trong `.gitignore`. Muốn khoá do mình giữ thì đặt env:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Dán kết quả vào `QUEST_SECRET`. Mất khoá = mọi token đã lưu không giải mã được, người dùng `/quest login` lại.

### Bảng lệnh

| Lệnh | Việc |
|---|---|
| `/quest login` | mở form dán user token |
| `/quest logout` | xoá token, dừng worker của bạn |
| `/quest whoami` | kiểm token + build number |
| `/scan [show_completed]` | quét một lần, chỉ đọc |
| `/quest start [notify]` | bật worker nền: quét → nhận → hoàn thành |
| `/quest stop` | dừng worker, huỷ one-shot đang chạy |
| `/quest status` | trạng thái, số lần quét, lỗi gần nhất |
| `/quest run` | chạy một chu kỳ đầy đủ ngay |
| `/quest config` | xem cấu hình |
| `/quest set key value` | sửa cấu hình (lưu theo tài khoản) |
| `/quest users` | số người đã đăng nhập (owner) |

Mỗi người có cấu hình riêng. Nhiều người chạy cùng lúc thì các chu kỳ API xếp hàng qua một lock chung — `main.py` dùng biến global nên không thể phục vụ hai cấu hình cùng một lúc.

### Tính năng README ↔ lệnh

| Tính năng | `main.py` | Trong Discord |
|---|---|---|
| **Auto Scan** | `run()` + `POLL_INTERVAL` | `/quest start`, `/quest set interval` |
| **Auto Accept** | `AUTO_ACCEPT` | `/quest set auto_accept on\|off` |
| **Auto Complete** | `process_quest()` | `/quest start`, `/quest run` |
| **Rate Limit Handling** | tự chờ 429 | tự động, lỗi ở `/quest status` |
| **Build Number Fetch** | `fetch_latest_build_number()` | `/quest whoami` |

### Cấu hình

| Key | Mặc định | Khoảng | Nghĩa |
|---|---|---|---|
| `interval` | 300 | 60–3600 | giây giữa các lần quét |
| `heartbeat` | 20 | 5–60 | giây giữa heartbeat |
| `auto_accept` | on | on/off | tự nhận quest mới |
| `log_progress` | off | on/off | log tiến độ |
| `debug` | off | on/off | log HTTP |

Cấu hình lưu trong `quest-tokens.json` theo từng user.

### `/quest whoami` — chẩn đoán

```
Quest credentials
client_build_number   641803
GET /users/@me        200
Token đã lưu          72 ký tự

Đăng nhập: SUJILAY (stchan06 · ID 1482005776469069927)
```

Token chết → ô đỏ, `401`, kèm nhắc `/quest login` lại.

### Lỗi thường gặp

| Log / embed | Nghĩa | Sửa |
|---|---|---|
| `Bạn chưa đăng nhập quest` | chưa có token | `/quest login` |
| `Đăng nhập thất bại: Discord trả 401` | token chết | lấy token mới từ trình duyệt |
| `token này thuộc về tài khoản khác` | dán token người khác | dùng token của chính mình |
| `đó là bot token` | nhầm loại | lấy user token |
| `thiếu thư viện cryptography` | host chưa cài | thêm vào `requirements.txt`, redeploy |
| `Lỗi fetch: HTTP 403` | token bị hạn chế | đăng nhập lại web, lấy token mới |
| Embed `0 quest` | không có quest khả dụng | bình thường |

> Quest `PLAY_ON_DESKTOP` / `STREAM_ON_DESKTOP` chạy đúng bằng thời lượng thật.
> `/quest status` vẫn trả lời ngay vì đọc state, không lấy lock.

### Rủi ro còn lại

Token được mã hoá khi lưu, nhưng khoá nằm trên cùng máy chủ — ai chạy được tiến trình bot hoặc đọc được `quest-secret.key` thì giải mã được. Và dùng user token qua HTTP là trái ToS Discord, tài khoản có thể bị khoá. Ai không muốn đặt token lên host thì cứ chạy `main.py` trên máy mình.
