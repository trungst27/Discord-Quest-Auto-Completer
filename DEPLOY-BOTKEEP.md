# Deploy lên Botkeep.cloud

Botkeep Free: 1 server, 512 MB RAM, 50% CPU, 1 GB storage, chạy 24/7, không cần gia hạn tay.
512 MB đủ cho discord.py (bot chữ dùng ~80–120 MB).

---

## A. Chuẩn bị repo (làm một lần)

Botkeep deploy bằng **GitHub** hoặc **ZIP**. File tối thiểu để chạy:

```
bot.py                 bắt buộc
requirements-bot.txt   bắt buộc
.env.example           tham khảo, KHÔNG chứa token thật
```

`.env` **không** đưa lên repo — token nhập vào panel, mục **Environment / Secrets**.

### Đẩy lên GitHub

```powershell
cd "D:\Bot Disscord"
git add bot.py requirements-bot.txt .env.example .gitignore BOT-SETUP.md DEPLOY-BOTKEEP.md
git commit -m "feat: cloud-ready bot with guild sync"
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main
```

Kiểm tra `.gitignore` đã chặn `.env`, `.token`, `.bot-token.xml` trước khi push.

---

## B. Tạo workload trên Botkeep

1. Đăng nhập [botkeep.cloud](https://botkeep.cloud) → **Start free** → tạo app.
2. Chọn **Profile: Python** (hoặc *Applications & bots → Python*).
3. **Source**: GitHub → chọn repo + branch `main`.
   Hoặc **ZIP upload**: nén `bot.py` + `requirements-bot.txt` (đừng nén cả thư mục cha).
4. **Start command**:

```
python bot.py
```

5. **Install command** (nếu panel hỏi riêng):

```
pip install -r requirements-bot.txt
```

6. **Environment** — thêm hai biến:

| Key | Value |
|---|---|
| `DISCORD_TOKEN` | token bot thật (3 phần, cách nhau dấu chấm) |
| `GUILD_ID` | ID server của bạn (tùy chọn — xem mục D) |

7. Nhấn **Start / Deploy**. Theo dõi **Runtime output**.

### Log đúng trông như này

```
INFO  discord.client: logging in using static token
INFO  bot: Slash commands synced to guild 1234567890: 3 command(s).
INFO  discord.gateway: Shard ID None has connected to Gateway
INFO  bot: READY: MyBot#3521 (ID: ...) — 1 guild(s)
INFO  bot:   guild: My Server (ID: ...)
```

Thấy `READY` là xong. `LoginFailure` / `401` = token sai, quay lại mục C.

---

## C. Lỗi 401 trong panel cũ (ảnh chụp Pterodactyl)

Log bạn gửi:

```
[ OK] Build number: 641803
[DBG ] GET /users/@me
[DBG ]   -> 401
[ ERR] Token không hợp lệ (status 401)
```

Đó là `main.py` (quest tool) chạy trên panel Pterodactyl, với token bị từ chối. Ba nguyên nhân theo thứ tự thường gặp:

| Nguyên nhân | Cách xác nhận | Sửa |
|---|---|---|
| Token dán sai / dư khoảng trắng / dư dấu nháy | token có 1–2 dấu chấm thay vì 2 | copy lại, dán trần, không nháy |
| Token đã bị reset hoặc bị Discord revoke | token cũ đột nhiên 401 | **Bot → Reset Token** trong Developer Portal |
| Dùng bot token cho `main.py` | bot token luôn 401 với endpoint user (`/quests/@me`) | dùng user token cho quest tool — đây là endpoint user |

Điểm quan trọng: `main.py` gọi `/quests/@me` — endpoint **user**, không phải bot. Botkeep chạy `bot.py` (bot token), panel Pterodactyl kia chạy `main.py` (user token). Hai thứ khác nhau, đừng trộn token.

**Nếu panel Pterodactyl đó là để chạy bot:** đổi start command từ `python main.py` sang `python bot.py`, và đặt `DISCORD_TOKEN` là bot token.

---

## D. Slash command không hiện trong Discord

Thứ tự kiểm tra:

1. **Sync toàn cục chậm tới 1 giờ.** Đây là nguyên nhân số 1. Đặt `GUILD_ID` để sync tức thì:
   - Discord → chuột phải server → **Copy Server ID** (bật Developer Mode trước: Settings → Advanced → Developer Mode).
   - Thêm `GUILD_ID=<id>` vào Environment, restart workload.
   - Log phải ghi `synced to guild ...` thay vì `synced globally`.
2. **Thiếu scope `applications.commands`.** Mời lại bot bằng URL có cả `bot` và `applications.commands`:
   ```
   https://discord.com/oauth2/authorize?client_id=<APP_ID>&scope=bot+applications.commands&permissions=0
   ```
3. **Bot chưa online.** `/ping` không hiện nếu gateway chưa kết nối — xem log có `READY`.
4. **Client cache.** Ctrl+R trong Discord, hoặc thoát hẳn rồi mở lại.
5. **Bot không có quyền trong channel.** Slash command vẫn hiện nhưng bị ẩn nếu thiếu quyền xem channel.

Kiểm tra bằng API (không cần chờ):

```powershell
curl.exe -H "Authorization: Bot <TOKEN>" https://discord.com/api/v10/applications/<APP_ID>/guilds/<GUILD_ID>/commands
```

Ra JSON có 3 lệnh = sync thành công, vấn đề còn lại là client cache hoặc quyền.

---

## E. Chạy cả hai (bot + quest tool) cùng lúc

Botkeep Free chỉ cho **1 server slot**, nên:

- **Botkeep** → `bot.py` (bot token, 24/7, slash commands).
- **Quest tool** → chạy máy khác, hoặc dùng cùng panel nếu nâng slot. Quest tool cần **user token** và không cần 24/7 liên tục — chạy khi cần quét.

Không nhét cả hai vào một start command: hai tiến trình, một slot, dễ bị panel kill.

---

## F. Checklist trước khi bấm Deploy

- [ ] Repo có `bot.py` + `requirements-bot.txt`
- [ ] `.env` / `.bot-token.xml` **không** nằm trong repo
- [ ] Start command = `python bot.py`
- [ ] `DISCORD_TOKEN` = bot token 3 phần
- [ ] `GUILD_ID` đã đặt (sync tức thì)
- [ ] Bot đã mời với scope `bot` + `applications.commands`
- [ ] Sau deploy: log có `READY` và `synced to guild`
