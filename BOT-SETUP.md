# Chạy bot — từ số 0 đến đang chạy

Bot tự bật lại sau khi restart máy, tự retry khi crash, log ra file.

## 0. Chuẩn bị một lần

Trên Discord Developer Portal ([discord.com/developers/applications](https://discord.com/developers/applications)):

1. **New Application** → đặt tên → **Bot** → **Reset Token** → copy token (chỉ hiện một lần).
2. **Bot** → bật **Message Content Intent** nếu sau này thêm lệnh prefix (slash command không cần).
3. **OAuth2 → URL Generator** → scope `bot` + `applications.commands` → mời bot vào server.

Token dạng `MTIzNDU2Nzg5.GhiJKL.<38 ký tự>` — 3 phần, cách nhau bằng dấu chấm.

## 1. Cài venv (nếu chưa có)

```powershell
cd "D:\Bot Disscord"
python -m venv .venv
```

## 2. Chạy setup

```powershell
cd "D:\Bot Disscord"
.\setup-bot.ps1
```

Script sẽ:

| Bước | Việc làm |
|---|---|
| 1 | kiểm tra `.venv` |
| 2 | `pip install -r requirements-bot.txt` |
| 3 | tạo `logs\` + `.env` |
| 4 | hỏi token, mã hoá bằng DPAPI → `.bot-token.xml` |
| 5 | đăng ký Scheduled Task `DiscordBotKeep`, chạy ngay |

Token **không** nằm dạng plaintext: `.bot-token.xml` chỉ user Windows hiện tại giải mã được.

## 3. Kiểm tra

```powershell
.\status-bot.ps1
```

Phải thấy:

```
Task:   Running
Bot:    RUNNING (pid 12345)
--- logs\discord.log ---
... READY: MyBot#1234 (ID: ...) — 1 guild(s)
```

Trong Discord gõ `/ping` → bot trả `Pong! ... Latency: NN ms`.

## 4. Quản lý

| Lệnh | Tác dụng |
|---|---|
| `.\status-bot.ps1` | task state + process + 12 dòng log cuối |
| `.\teardown-bot.ps1` | unregister task, kill bot + supervisor |
| `Start-ScheduledTask -TaskName DiscordBotKeep` | bật lại sau teardown |

## Cấu trúc

```
bot.py                ← bot thật (slash commands, log xoay vòng 5MB x3)
run-bot.ps1           ← supervisor: mutex 1 instance, crash loop + backoff
setup-bot.ps1         ← cài đặt một lần
status-bot.ps1        ← xem trạng thái
teardown-bot.ps1      ← gỡ sạch
.env.example          ← mẫu; token có thể đặt ở đây thay vì XML
logs\discord.log      ← log của bot
logs\supervisor.log   ← log bật/tắt + mã thoát
```

## Lỗi thường gặp

| Triệu chứng | Nguyên nhân | Sửa |
|---|---|---|
| `Missing DISCORD_TOKEN` | chưa chạy setup, hoặc XML hỏng | chạy lại `setup-bot.ps1` |
| `LoginFailure` | token sai / đã reset | lấy token mới, chạy lại setup (chọn `y` để ghi đè) |
| `does not look like a bot token` | dán user token thay vì bot token | lấy lại ở mục **Bot** |
| `/ping` không hiện | chưa sync hoặc thiếu scope `applications.commands` | mời lại bot, xem log `Slash commands synced` |
| Task chạy nhưng bot chết liên tục | xem `logs\supervisor.log`, mã thoát ghi ở đó | sửa theo log |
| Bot im lặng sau khi đổi máy | DPAPI gắn với user+máy | chạy lại setup trên máy mới |

## Ghi chú

- `run-bot.ps1` giữ mutex `Local\DiscordBotKeep` — chạy tay 2 lần không tạo 2 bot.
- Backoff: crash nhanh (<300s) thì chờ 30s → 60 → 120 … tối đa 600s. Chạy ổn >300s thì reset về 30s.
- `main.py` (quest auto-completer) là tool riêng, không liên quan task này: `python main.py <TOKEN>`.
