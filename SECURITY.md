# Bảo mật — repo public, token không lộ

Repo này có thể để **public** an toàn. Token không bao giờ nằm trong code, nên không có gì để lộ khi `git push`.

---

## 1. Nguyên tắc duy nhất

> Code công khai, bí mật nằm ở môi trường chạy.

`bot.py` đọc token từ biến môi trường:

```python
token = os.environ.get('DISCORD_TOKEN', '').strip()
```

Không có chỗ nào trong code chứa token. Clone repo về máy khác → chạy không lên, vì thiếu env. Đó chính là điều mình muốn.

| Thứ | Nằm ở đâu | Có trong Git? |
|---|---|---|
| `bot.py`, docs, script | repo | ✅ công khai được |
| `DISCORD_TOKEN` | panel Environment / `.env` local | ❌ không bao giờ |
| `.bot-token.xml` | máy local, mã hoá DPAPI | ❌ đã chặn bởi `.gitignore` |
| `logs/*.log` | máy chạy | ❌ đã chặn |

---

## 2. Ba lớp chặn

**Lớp 1 — `.gitignore`**

```
.venv/  .tmp/  __pycache__/  logs/  .bot-token.xml  .token  .env
```

**Lớp 2 — pre-commit hook**

`tools/secret_scan.py` quét file đang stage trước mỗi commit. Bắt: bot token Discord, webhook URL, GitHub PAT, private key, AWS key, và mọi dòng `DISCORD_TOKEN=<giá trị>`.

Bật một lần sau khi clone:

```powershell
git config core.hooksPath .githooks
```

Test thử:

```powershell
.\venv\Scripts\python.exe tools\secret_scan.py
# secret-scan: clean (N file(s) checked)
```

Bị chặn thì commit **không** đi tiếp — script trả exit 1.

**Lớp 3 — Token scope**

Bot token chỉ có quyền trong server mà bot được mời. Nó **không** phải user token — không đọc được DM, không đụng được tài khoản, không đăng nhập thay bạn. Mất token = người khác điều khiển được bot, không phải tài khoản.

---

## 3. Nếu lỡ commit token

Xoá file rồi commit lại **không đủ** — token vẫn nằm trong lịch sử Git và repo public là đọc được.

Thứ tự bắt buộc:

1. **Reset token ngay** — Developer Portal → Bot → Reset Token. Token cũ chết ngay lập tức (đây là bước quan trọng nhất, làm trước).
2. Cập nhật token mới vào panel Environment / `.env` local.
3. Xoá khỏi lịch sử nếu muốn sạch:

```powershell
pip install git-filter-repo
git filter-repo --path .env --invert-paths
git push --force
```

4. Kiểm tra lại toàn bộ lịch sử:

```powershell
git log --all -p | Select-String -Pattern "MT[A-Za-z0-9_-]{20,}\."
```

Nếu repo đã public và token đã bị bot khác quét: coi như đã lộ, reset là bắt buộc.

---

## 4. Nhiều người dùng chung một bot

Mô hình đúng: **một bot, một token, do bạn giữ.**

| Ai | Làm được gì |
|---|---|
| Người dùng trong server | gõ `/ping`, `/info`, `/stats`, `/help` |
| Owner (`OWNER_IDS`) | thêm `/sync` — đồng bộ lại slash command |
| Bạn (chủ token, chủ panel) | mọi thứ: deploy, env, xoá bot, đọc log |

Người khác **không cần** token, không cần repo, không cần quyền gì — họ chỉ tương tác qua Discord.

Cấu hình quyền cao nhất:

```
OWNER_IDS=123456789012345678
```

Nhiều người thì cách nhau dấu phẩy: `OWNER_IDS=111...,222...`

**Để trống `OWNER_IDS` = không ai có quyền owner.** Đây là chủ ý: thiếu cấu hình thì không ai được cấp quyền, chứ không phải ai cũng được.

Lấy user ID: Discord → Settings → Advanced → Developer Mode → chuột phải tên mình → Copy User ID.

### Bot ở nhiều server

`on_guild_join` tự sync slash command cho server mới, nên mời bot vào server lạ là dùng được ngay. `GUILD_ID` chỉ nên đặt khi bạn muốn sync tức thì cho **một** server test; bỏ trống thì bot dùng sync global (chậm tới 1 giờ nhưng phủ mọi server).

---

## 5. Repo public: người khác làm gì được

| Việc | Được? |
|---|---|
| Đọc code, fork, học theo | ✅ |
| Tự dựng bot riêng với token của họ | ✅ (đúng ý — đó là mục đích) |
| Điều khiển bot của bạn | ❌ thiếu token |
| Đọc log, env, database của bạn | ❌ |
| Mở issue / PR vào repo | ✅ |

Muốn chặn PR từ người lạ: Settings → General → Features → tắt *Pull requests*.

---

## 6. Checklist trước khi bấm public

- [ ] `git log --all -p` không có token
- [ ] Không có `.env`, `.token`, `.bot-token.xml` trong repo
- [ ] `git config core.hooksPath .githooks` đã bật
- [ ] `DISCORD_TOKEN` chỉ nằm trong panel Environment
- [ ] Đã đặt `OWNER_IDS`
- [ ] Đã bật **2FA** cho tài khoản GitHub và Discord
