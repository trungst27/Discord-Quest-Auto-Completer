# Discord Quest Auto-Completer

Công cụ tự động quét, nhận và hoàn thành các Quest trên Discord.

> **Server cộng đồng:** [Tham gia Discord để nghịch bot auto](https://t.me/dothanh1110/88)

---

## Repo này có hai thành phần

| Thành phần | File | Token cần | Chạy ở đâu |
|---|---|---|---|
| **Discord bot** (slash commands) | `bot.py` | **bot token** | máy local / [botkeep.cloud](https://botkeep.cloud) |
| **Quest tool** (auto-complete) | `main.py` | **user token** | máy local / panel VPS |

Hai loại token khác nhau, đừng trộn: bot token gọi `/users/@me` với header `Bot ...`, user token gọi `/quests/@me`.

### Bắt đầu nhanh với bot

```powershell
.\setup-bot.ps1      # cài đặt một lần: venv, deps, token, scheduled task
.\status-bot.ps1     # xem trạng thái
.\teardown-bot.ps1   # gỡ sạch
```

| Tài liệu | Nội dung |
|---|---|
| [BOT-SETUP.md](BOT-SETUP.md) | chạy bot trên Windows, tự bật lại sau restart |
| [DEPLOY-BOTKEEP.md](DEPLOY-BOTKEEP.md) | deploy lên botkeep.cloud, sửa lỗi slash command |
| [SECURITY.md](SECURITY.md) | repo public mà token không lộ, phân quyền owner |

### Lệnh của bot

| Lệnh | Ai dùng được |
|---|---|
| `/ping` | mọi người |
| `/info` | mọi người |
| `/stats` | mọi người |
| `/help` | mọi người |
| `/sync` | chỉ owner (`OWNER_IDS`) |
| `/quest login` | mọi người — dán user token của chính mình |
| `/quest logout` | mọi người — xoá token đã lưu |
| `/quest whoami` | mọi người — kiểm token + build number |
| `/scan` | mọi người — quét quest một lần, chỉ đọc |
| `/quest start` | mọi người — bật worker nền: quét → nhận → hoàn thành |
| `/quest stop` | mọi người — dừng worker |
| `/quest status` | mọi người — trạng thái, số lần quét, lỗi |
| `/quest run` | mọi người — chạy một chu kỳ đầy đủ ngay |
| `/quest config` | mọi người — xem cấu hình |
| `/quest set` | mọi người — sửa `interval`, `heartbeat`, `auto_accept`, `log_progress`, `debug` |
| `/quest users` | chỉ owner — số người đã đăng nhập |

Quest API là **user-scoped**: `/quests/@me` trả 200 cho user token, 401 cho bot token. Nên mỗi người tự đăng nhập bằng token của mình qua form riêng tư (`/quest login`), bot mã hoá rồi lưu vào `quest-tokens.json`. Không cần `QUEST_TOKEN` trong env nữa.

Token đi qua `quest_store.py`: Fernet, khoá từ `QUEST_SECRET` hoặc file `quest-secret.key` tự sinh. Cả hai file đều gitignored. Token không bao giờ hiện lại trong chat, không nằm trong embed, không ghi log.

Tính năng trong bảng dưới đây chạy được cả hai cách: `main.py` trong terminal, hoặc lệnh Discord.

| Tính năng | `main.py` | Discord |
|---|---|---|
| Auto Scan | `run()` + `POLL_INTERVAL` | `/quest start`, `/quest set interval` |
| Auto Accept | `AUTO_ACCEPT` | `/quest set auto_accept on\|off` |
| Auto Complete | `process_quest()` | `/quest start`, `/quest run` |
| Rate Limit Handling | tự chờ 429 | tự động, lỗi ở `/quest status` |
| Build Number Fetch | `fetch_latest_build_number()` | `/quest whoami` |

Cấu hình `/quest set` lưu theo từng user và áp vào `main.py` trước mỗi chu kỳ:

| Key | Mặc định | Khoảng | Nghĩa |
|---|---|---|---|
| `interval` | 300 | 60–3600 | giây giữa các lần quét |
| `heartbeat` | 20 | 5–60 | giây giữa heartbeat |
| `auto_accept` | on | on/off | tự nhận quest mới |
| `log_progress` | off | on/off | log tiến độ |
| `debug` | off | on/off | log HTTP |

> **Rủi ro:** token được mã hoá khi lưu, nhưng khoá nằm trên cùng máy chủ — ai chạy được tiến trình bot hoặc đọc được `quest-secret.key` thì giải mã được. Dùng user token qua HTTP cũng là trái ToS Discord, tài khoản có thể bị khoá. Không muốn đặt token lên host thì chạy `main.py` trên máy mình.

> **Token không nằm trong repo.** Bot đọc `DISCORD_TOKEN` từ biến môi trường. Bật chặn commit lộ secret:
> ```powershell
> git config core.hooksPath .githooks
> ```

### Kiểm tra token trước khi deploy

```powershell
.\.venv\Scripts\python.exe tools\check_token.py <TOKEN>
```

Ra `TOKEN VALID` + danh sách lệnh = token đúng. Ra `401 Unauthorized` = token đã chết, phải reset.

### Chạy quest tool

```powershell
python main.py <USER_TOKEN>
```

### Giám sát uptime

Bot tự bật lại sau khi restart và worker tự resume, nhưng không ai biết nếu tiến trình chết hẳn. Bật endpoint để dịch vụ ngoài dò:

```env
HEALTH_PORT=8080
```

Rồi trỏ UptimeRobot / BetterStack vào:

```
http://<host-panel>:<port>/health
```

Trả về:

```json
{"status":"ok","bot":"SBLT Auto Quest#3521","guilds":1,"latency_ms":42,
 "users":1,"workers":1,"uptime_s":3600}
```

`status:ok` = gateway đã READY. Gắn `HEALTH_PORT` mà panel không mở port thì bot ghi warning và chạy tiếp — không sập vì health.

**Host không mở port?** Dùng nhịp tim gọi ra, không cần expose gì:

```env
HEALTHCHECK_URL=https://hc-ping.com/<uuid>
HEALTHCHECK_INTERVAL=300
```

Bot POST trạng thái lên đó mỗi 5 phút. healthchecks.io im lặng quá 10 phút là gửi mail/Telegram. Chạy được sau mọi NAT, firewall, panel không cấp port.

---

## Mục lục

- [Tính năng](#tính-năng)
- [Yêu cầu](#yêu-cầu)
- [Cách sử dụng](#cách-sử-dụng)
- [Cấu hình](#cấu-hình)
- [Kiến trúc mã nguồn](#kiến-trúc-mã-nguồn)
  - [Hằng số & Cấu hình](#hằng-số--cấu-hình)
  - [Logging](#logging)
  - [Build Number](#build-number)
  - [DiscordAPI](#discordapi)
  - [Quest Helpers](#quest-helpers)
  - [QuestAutocompleter](#questautocompleter)
- [Luồng hoạt động](#luồng-hoạt-động)
- [Các loại task hỗ trợ](#các-loại-task-hỗ-trợ)

---

## Tính năng

| Tính năng | Mô tả |
|---|---|
| **Auto Scan** | Tự động quét quest mới theo chu kỳ (`POLL_INTERVAL`) |
| **Auto Accept** | Tự động đăng ký (enroll) các quest chưa nhận |
| **Auto Complete** | Tự động hoàn thành quest bằng cách gửi progress/heartbeat |
| **Rate Limit Handling** | Tự động chờ và retry khi bị rate limit (429) |
| **Build Number Fetch** | Tự động lấy `client_build_number` mới nhất từ Discord web app |

---

## Yêu cầu

- Python 3.7+
- Thư viện: `requests`

```bash
pip install requests
```

---

## Cách sử dụng

### 1. Truyền token qua argument

```bash
python3 main.py <DISCORD_TOKEN>
```

### 2. Lưu token vào file `.token`

Tạo file `.token` trong cùng thư mục chứa token, sau đó chạy:

```bash
python3 main.py
```

### 3. Nhập token thủ công

Chạy không có argument, chương trình sẽ yêu cầu nhập token:

```bash
python3 main.py
```

> **Lưu ý:** Dùng `Ctrl+C` để dừng chương trình.

---

## Cấu hình

Các hằng số cấu hình nằm ở đầu file `main.py`:

| Biến | Mặc định | Mô tả |
|---|---|---|
| `API_BASE` | `https://discord.com/api/v9` | Base URL của Discord API |
| `POLL_INTERVAL` | `60` | Số giây giữa các lần quét quest |
| `HEARTBEAT_INTERVAL` | `20` | Số giây giữa các lần gửi heartbeat |
| `AUTO_ACCEPT` | `True` | Tự động nhận tất cả quest khả dụng |
| `LOG_PROGRESS` | `True` | Hiển thị log tiến độ |
| `DEBUG` | `True` | Bật log debug chi tiết |

---

## Kiến trúc mã nguồn

### Hằng số & Cấu hình

- `SUPPORTED_TASKS` — Danh sách các loại task mà tool hỗ trợ hoàn thành tự động.

### Logging

**`Colors`** — Class chứa mã màu ANSI cho terminal output.

**`log(msg, level)`** — Hàm log với các mức:

| Level | Ý nghĩa |
|---|---|
| `info` | Thông tin chung |
| `ok` | Thành công |
| `warn` | Cảnh báo |
| `error` | Lỗi |
| `progress` | Tiến độ (có thể tắt qua `LOG_PROGRESS`) |
| `debug` | Debug (có thể tắt qua `DEBUG`) |

### Build Number

- **`fetch_latest_build_number()`** — Scrape trang Discord web app để lấy `client_build_number` mới nhất. Fallback về giá trị mặc định nếu thất bại.
- **`make_super_properties(build_number)`** — Tạo header `X-Super-Properties` dạng base64, giả lập Discord Desktop Client.

### DiscordAPI

Class quản lý HTTP session với Discord API.

| Method | Mô tả |
|---|---|
| `__init__(token, build_number)` | Khởi tạo session với headers giả lập Discord Client |
| `get(path)` | Gửi GET request |
| `post(path, payload)` | Gửi POST request |
| `validate_token()` | Kiểm tra token hợp lệ, in thông tin user |

### Quest Helpers

Các hàm tiện ích để trích xuất dữ liệu từ quest object (hỗ trợ cả camelCase và snake_case):

| Hàm | Mô tả |
|---|---|
| `_get(d, *keys)` | Lấy giá trị từ dict với nhiều key thay thế |
| `get_task_config(quest)` | Lấy task config từ quest |
| `get_quest_name(quest)` | Lấy tên quest (fallback qua nhiều field) |
| `get_expires_at(quest)` | Lấy thời gian hết hạn |
| `get_user_status(quest)` | Lấy trạng thái user cho quest |
| `is_completable(quest)` | Kiểm tra quest có thể hoàn thành không |
| `is_enrolled(quest)` | Kiểm tra đã đăng ký quest chưa |
| `is_completed(quest)` | Kiểm tra đã hoàn thành quest chưa |
| `get_task_type(quest)` | Lấy loại task của quest |
| `get_seconds_needed(quest)` | Tổng số giây cần để hoàn thành |
| `get_seconds_done(quest)` | Số giây đã hoàn thành |
| `get_enrolled_at(quest)` | Thời điểm đăng ký quest |

### QuestAutocompleter

Class chính điều khiển toàn bộ logic auto-complete.

| Method | Mô tả |
|---|---|
| `fetch_quests()` | Gọi API lấy danh sách quest, xử lý rate limit |
| `enroll_quest(quest)` | Đăng ký một quest (retry tối đa 3 lần) |
| `auto_accept(quests)` | Tự động đăng ký tất cả quest chưa nhận |
| `complete_video(quest)` | Hoàn thành quest dạng xem video (gửi `video-progress`) |
| `complete_heartbeat(quest)` | Hoàn thành quest dạng chơi/stream game (gửi `heartbeat`) |
| `complete_activity(quest)` | Hoàn thành quest dạng activity (gửi `heartbeat`) |
| `process_quest(quest)` | Xử lý một quest — chọn phương thức phù hợp |
| `run()` | Vòng lặp chính: quét → nhận → hoàn thành → chờ → lặp lại |

---

## Luồng hoạt động

```
Khởi động
  │
  ├─ Đọc token (argument / .token / nhập tay)
  ├─ Lấy build number từ Discord
  ├─ Validate token
  │
  └─ Vòng lặp chính (mỗi POLL_INTERVAL giây):
       │
       ├─ Fetch danh sách quest
       ├─ Hiển thị tổng quan (tổng / enrolled / completed)
       ├─ Auto-accept các quest chưa nhận
       ├─ Lọc quest cần hoàn thành
       │
       └─ Với mỗi quest cần hoàn thành:
            │
            ├─ WATCH_VIDEO / WATCH_VIDEO_ON_MOBILE
            │     └─ Gửi POST /quests/{id}/video-progress liên tục
            │
            ├─ PLAY_ON_DESKTOP / STREAM_ON_DESKTOP
            │     └─ Gửi POST /quests/{id}/heartbeat mỗi 20s
            │
            └─ PLAY_ACTIVITY
                  └─ Gửi POST /quests/{id}/heartbeat mỗi 20s
```

---

## Các loại task hỗ trợ

| Task | API Endpoint | Cơ chế |
|---|---|---|
| `WATCH_VIDEO` | `/quests/{id}/video-progress` | Gửi timestamp tăng dần, tốc độ ~7s/lần |
| `WATCH_VIDEO_ON_MOBILE` | `/quests/{id}/video-progress` | Tương tự `WATCH_VIDEO` |
| `PLAY_ON_DESKTOP` | `/quests/{id}/heartbeat` | Heartbeat mỗi 20s với `stream_key` ngẫu nhiên |
| `STREAM_ON_DESKTOP` | `/quests/{id}/heartbeat` | Tương tự `PLAY_ON_DESKTOP` |
| `PLAY_ACTIVITY` | `/quests/{id}/heartbeat` | Heartbeat mỗi 20s với `stream_key` cố định |
