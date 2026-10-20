"""Per-user quest commands: /scan plus the /quest control group.

Every user supplies their own USER token through `/quest login`; it is encrypted
at rest by `quest_store.UserStore` and never echoed back. Quest endpoints are
user-scoped — `/quests/@me` answers 200 for a user token and 401 for a bot token,
so `DISCORD_TOKEN` is never used here.

Runtime knobs mirror the constants documented in README.md:

    POLL_INTERVAL       -> /quest set interval
    HEARTBEAT_INTERVAL  -> /quest set heartbeat
    AUTO_ACCEPT         -> /quest set auto_accept
    LOG_PROGRESS        -> /quest set log_progress
    DEBUG               -> /quest set debug

Those are module globals in main.py, read at call time. Because several users
can run at once and each has their own settings, every API cycle is serialised
through one service-wide lock so the globals never belong to two users at once.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands

import quest_store

log = logging.getLogger('bot.quests')

try:
    import main as quest_main
    from main import DiscordAPI, QuestAutocompleter, fetch_latest_build_number
    MAIN_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - only on a broken checkout
    quest_main = None  # type: ignore[assignment]
    DiscordAPI = None  # type: ignore[assignment]
    QuestAutocompleter = None  # type: ignore[assignment]
    fetch_latest_build_number = None  # type: ignore[assignment]
    MAIN_IMPORT_ERROR = f'{type(exc).__name__}: {exc}'

STATUS_MARK = {'completed': '✓', 'enrolled': '▶', 'new': '○'}
MAX_QUEST_FIELDS = 25

TRUTHY = {'1', 'true', 'on', 'yes', 'bat', 'bật'}
FALSY = {'0', 'false', 'off', 'no', 'tat', 'tắt'}


class QuestNotConfigured(RuntimeError):
    """The bot cannot reach the quest API at all."""


class QuestAuthError(RuntimeError):
    """The supplied token was rejected."""


def _not_configured(reason: str) -> str:
    return (
        f'**Quest chưa dùng được** — {reason}\n\n'
        'Cần thư viện `cryptography` để mã hoá token đã lưu:\n'
        '```\npip install cryptography\n```'
    )


def _no_login() -> str:
    return (
        '**Bạn chưa đăng nhập quest.**\n\n'
        'Dùng `/quest login` rồi dán **user token** của bạn vào ô hiện ra.\n'
        'Token được mã hoá khi lưu, không hiện lại trong chat, và chỉ áp dụng cho tài khoản của bạn.\n'
        'Lấy token: `discord.com/app` → Ctrl+Shift+I → Network → header `authorization`.'
    )


def parse_setting(key: str, raw: str) -> tuple[bool, object]:
    kind, low, high = quest_store.SETTING_BOUNDS[key]
    text = raw.strip().lower()
    if kind == 'bool':
        if text in TRUTHY:
            return True, True
        if text in FALSY:
            return True, False
        return False, 'cần `on` hoặc `off`'
    try:
        number = int(text)
    except ValueError:
        return False, 'cần một số nguyên'
    if not (low <= number <= high):
        return False, f'phải trong khoảng {low}–{high}'
    return True, number


def apply_settings(settings: dict) -> None:
    """Push one user's settings into main.py's module globals."""
    if quest_main is None:
        return
    quest_main.POLL_INTERVAL = settings['interval']
    quest_main.HEARTBEAT_INTERVAL = settings['heartbeat']
    quest_main.AUTO_ACCEPT = settings['auto_accept']
    quest_main.LOG_PROGRESS = settings['log_progress']
    quest_main.DEBUG = settings['debug']


class UserSession:
    """One user's token, settings, and background worker."""

    def __init__(self, user_id: int, token: str, settings: dict, on_settings_change) -> None:
        self.user_id = user_id
        self.token = token
        self.settings = settings
        self._on_settings_change = on_settings_change
        self._completer = None
        self._worker: Optional[asyncio.Task] = None
        self._oneshot: Optional[asyncio.Task] = None
        self.started_at = None
        self.scans = 0
        self.completed_total = 0
        self.last_at = None
        self.last_error: Optional[str] = None
        self.last_summary: Optional[dict] = None

    @property
    def running(self) -> bool:
        return self._worker is not None and not self._worker.done()

    @property
    def busy(self) -> bool:
        return self._oneshot is not None and not self._oneshot.done()

    @property
    def interval(self) -> int:
        return self.settings['interval']

    def update_setting(self, key: str, raw: str) -> str:
        ok, value = parse_setting(key, raw)
        if not ok:
            raise ValueError(str(value))
        self.settings[key] = value
        self._on_settings_change(self)
        return f'`{key}` = **{value}**'

    def build(self) -> None:
        if self._completer is not None:
            return
        build_number = fetch_latest_build_number()
        self._completer = QuestAutocompleter(DiscordAPI(self.token, build_number))
        log.info('Quest client ready for user %s (build %s).', self.user_id, build_number)

    @property
    def completer(self):
        if self._completer is None:
            self.build()
        return self._completer


class QuestService:
    """Owns storage, sessions, and the single serialisation lock."""

    def __init__(self) -> None:
        self.store: Optional[quest_store.UserStore] = None
        self.store_error: Optional[str] = None
        try:
            self.store = quest_store.UserStore()
        except Exception as exc:
            self.store_error = str(exc)
            log.error('Quest storage unavailable: %s', exc)
        self.api_lock = asyncio.Lock()
        self._sessions: dict[int, UserSession] = {}

    # ── availability ───────────────────────────────────────────────────────────
    def reason_unavailable(self) -> Optional[str]:
        if MAIN_IMPORT_ERROR:
            return f'không import được `main.py` ({MAIN_IMPORT_ERROR})'
        if self.store_error:
            return f'storage lỗi ({self.store_error})'
        return None

    @property
    def configured(self) -> bool:
        return self.reason_unavailable() is None

    def session(self, user_id: int, username: str = '') -> Optional[UserSession]:
        if self.reason_unavailable():
            return None
        existing = self._sessions.get(user_id)
        if existing is not None:
            return existing

        token = self.store.get_token(user_id)
        if not token:
            return None

        settings = self.store.get_settings(user_id)
        session = UserSession(
            user_id,
            token,
            settings,
            lambda s: self.store.set_settings(s.user_id, s.settings),
        )
        self._sessions[user_id] = session
        log.info('Quest session opened for %s (%s).', username or 'user', user_id)
        return session

    # ── auth ───────────────────────────────────────────────────────────────────
    async def login(self, user_id: int, raw_token: str) -> dict:
        reason = self.reason_unavailable()
        if reason:
            raise QuestNotConfigured(reason)

        token = raw_token.strip()
        if token.lower().startswith('bot '):
            raise QuestAuthError('đó là header bot token, bỏ chữ `Bot ` và dùng user token')
        if token.count('.') != 2 or len(token) < 40:
            raise QuestAuthError('token không đúng định dạng (phải có 3 phần, dài ~70 ký tự)')

        build_number = await asyncio.to_thread(fetch_latest_build_number)
        api = DiscordAPI(token, build_number)
        response = await asyncio.to_thread(api.get, '/users/@me')
        if response.status_code != 200:
            raise QuestAuthError(
                f'Discord trả `{response.status_code}` — token chết hoặc sai. '
                'Đăng xuất web, đăng nhập lại, lấy token mới.'
            )
        user = response.json()
        if user.get('bot'):
            raise QuestAuthError('đó là bot token, quest cần user token')
        if str(user.get('id')) != str(user_id):
            raise QuestAuthError(
                'token này thuộc về tài khoản khác. Chỉ được lưu token của chính bạn.'
            )

        await self.logout(user_id, notify=False)
        self.store.put_token(user_id, token, name=str(user.get('username') or ''))
        log.info('Quest login stored for user %s.', user_id)
        return user

    async def logout(self, user_id: int, notify: bool = True) -> bool:
        session = self._sessions.pop(user_id, None)
        if session is not None:
            await _cancel(session._worker)
            await _cancel(session._oneshot)
            session._worker = None
            session._oneshot = None
        removed = self.store.remove(user_id) if self.store else False
        if notify and removed:
            log.info('Quest login removed for user %s.', user_id)
        return removed

    # ── work ───────────────────────────────────────────────────────────────────
    async def scan(self, session: UserSession, dry_run: bool) -> dict:
        async with self.api_lock:
            apply_settings(session.settings)
            await asyncio.to_thread(session.build)
            summary = await asyncio.to_thread(session.completer.scan_once, dry_run)
            session.scans += 1
            session.last_at = discord.utils.utcnow()
            session.last_summary = summary
            session.last_error = summary.get('error')
            session.completed_total += len(summary.get('completed_now') or [])
            return summary

    async def identity(self, session: UserSession) -> dict:
        async with self.api_lock:
            await asyncio.to_thread(session.build)
            build_number = await asyncio.to_thread(fetch_latest_build_number)
            response = await asyncio.to_thread(session.completer.api.get, '/users/@me')
            return {
                'build': build_number,
                'status': response.status_code,
                'user': response.json() if response.status_code == 200 else None,
            }

    # ── resume after restart ───────────────────────────────────────────────────
    async def resume(self, client: discord.Client) -> int:
        """Restart workers for logins that had one running before the restart."""
        if self.reason_unavailable():
            return 0
        started = 0
        for user_id, channel_id in self.store.auto_users():
            session = self.session(user_id)
            if session is None or session.running:
                continue
            channel = client.get_channel(channel_id) if channel_id else None
            owner = client.get_user(user_id) or f'<@{user_id}>'
            session.started_at = discord.utils.utcnow()
            session._worker = asyncio.create_task(
                _worker_loop(self, session, channel, owner),
                name=f'quest-worker-{user_id}',
            )
            started += 1
        if started:
            log.info('Resumed %d quest worker(s) after restart.', started)
        return started


async def _cancel(task: Optional[asyncio.Task]) -> None:
    if task is None or task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        pass


# ── embeds ─────────────────────────────────────────────────────────────────────
def scan_embed(summary: dict, show_completed: bool = True) -> discord.Embed:
    items = summary.get('items') or []
    if not show_completed:
        items = [item for item in items if item['status'] != 'completed']

    colour = discord.Colour.red() if summary.get('error') else discord.Colour.green()
    embed = discord.Embed(
        title='Quest scan',
        colour=colour,
        description=(
            f"**{summary.get('total', 0)}** quest · "
            f"**{summary.get('enrolled', 0)}** đã nhận · "
            f"**{summary.get('completed', 0)}** hoàn thành · "
            f"**{summary.get('completable', 0)}** khả dụng"
        ),
    )

    if summary.get('error'):
        embed.add_field(
            name='Lỗi fetch',
            value=f"```{str(summary['error'])[:900]}```",
            inline=False,
        )

    for item in items[:MAX_QUEST_FIELDS]:
        mark = STATUS_MARK.get(item['status'], '·')
        progress = f"{item['done']}/{item['need']}s" if item['need'] else 'không giới hạn'
        embed.add_field(
            name=f"{mark} {item['name']}"[:256],
            value=f"`{item['task']}` · {progress}",
            inline=False,
        )

    hidden = len(items) - MAX_QUEST_FIELDS
    footer = []
    if hidden > 0:
        footer.append(f'+{hidden} quest không hiển thị')
    if summary.get('dry_run'):
        footer.append('dry run — không nhận, không hoàn thành')
    if summary.get('completed_now'):
        footer.append(f"{len(summary['completed_now'])} vừa hoàn thành")
    if footer:
        embed.set_footer(text=' · '.join(footer))
    return embed


def completed_embed(names: list, owner=None) -> discord.Embed:
    embed = discord.Embed(
        title='Quest hoàn thành',
        description='\n'.join(f'✓ **{name}**' for name in names[:MAX_QUEST_FIELDS]),
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )
    if owner is not None:
        embed.set_footer(text=f'của {owner}')
    return embed


def settings_embed(session: UserSession) -> discord.Embed:
    s = session.settings
    embed = discord.Embed(
        title='Quest settings',
        colour=discord.Colour.blurple(),
        description=(
            f"`interval` = **{s['interval']}** — giây giữa các lần quét\n"
            f"`heartbeat` = **{s['heartbeat']}** — giây giữa heartbeat\n"
            f"`auto_accept` = **{'on' if s['auto_accept'] else 'off'}** — tự nhận quest mới\n"
            f"`log_progress` = **{'on' if s['log_progress'] else 'off'}** — log tiến độ\n"
            f"`debug` = **{'on' if s['debug'] else 'off'}** — log HTTP chi tiết"
        ),
    )
    embed.set_footer(text='Sửa bằng /quest set · cấu hình riêng của bạn')
    return embed


def status_embed(session: UserSession, auto_resume: bool = False) -> discord.Embed:
    running = session.running
    colour = discord.Colour.green() if running else discord.Colour.greyple()
    embed = discord.Embed(title='Quest worker', colour=colour)

    lines = [f"**Trạng thái:** {'đang chạy' if running else 'đã dừng'}"]
    if session.started_at and running:
        lines.append(f"**Bật lúc:** <t:{int(session.started_at.timestamp())}:R>")
        lines.append(f'**Nhịp quét:** {session.interval}s')
    lines.append(f'**Lần quét:** {session.scans}')
    lines.append(f'**Quest đã xong:** {session.completed_total}')
    lines.append(f"**Auto-accept:** {'on' if session.settings['auto_accept'] else 'off'}")
    lines.append(f"**Tự bật lại sau restart:** {'on' if auto_resume else 'off'}")
    if session.last_at:
        lines.append(f"**Quét gần nhất:** <t:{int(session.last_at.timestamp())}:R>")
    if session.last_error:
        lines.append(f"**Lỗi gần nhất:** `{str(session.last_error)[:300]}`")
    embed.description = '\n'.join(lines)

    summary = session.last_summary
    if summary:
        embed.add_field(
            name='Lần quét cuối',
            value=(
                f"**{summary.get('total', 0)}** quest · "
                f"**{summary.get('completed', 0)}** xong · "
                f"**{len(summary.get('completed_now') or [])}** vừa hoàn thành"
            ),
            inline=False,
        )
    return embed


def identity_embed(result: dict, token: str) -> discord.Embed:
    user = result.get('user') or {}
    ok = result.get('status') == 200 and not user.get('bot', True)
    embed = discord.Embed(
        title='Quest credentials',
        colour=discord.Colour.green() if ok else discord.Colour.red(),
    )
    embed.add_field(name='client_build_number', value=f"`{result.get('build')}`", inline=True)
    embed.add_field(name='GET /users/@me', value=f"`{result.get('status')}`", inline=True)
    embed.add_field(name='Token đã lưu', value=f'`{len(token)} ký tự`', inline=True)
    if user:
        embed.add_field(
            name='Đăng nhập',
            value=f"**{user.get('global_name') or user.get('username')}** "
                  f"(`{user.get('username')}` · ID `{user.get('id')}`)",
            inline=False,
        )
    else:
        embed.add_field(
            name='Lỗi',
            value='Token chết hoặc bị đăng xuất. `/quest login` lại để cập nhật.',
            inline=False,
        )
    return embed


# ── modal ─────────────────────────────────────────────────────────────────────
class LoginModal(discord.ui.Modal, title='Đăng nhập quest bằng user token'):
    def __init__(self, service: QuestService) -> None:
        super().__init__(timeout=300)
        self.service = service
        self.token_input = discord.ui.TextInput(
            style=discord.TextStyle.short,
            placeholder='MTA... (DevTools → Network → authorization)',
            min_length=40,
            max_length=200,
            required=True,
        )
        # Label is the current modal wrapper; TextInput on its own is deprecated
        # in discord.py 2.7.
        self.add_item(
            discord.ui.Label(
                text='User token',
                description='Chỉ dùng token của chính bạn. Bot mã hoá khi lưu.',
                component=self.token_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            user = await self.service.login(interaction.user.id, str(self.token_input.value))
        except QuestAuthError as exc:
            await interaction.followup.send(f'Đăng nhập thất bại: {exc}', ephemeral=True)
            return
        except Exception as exc:
            log.warning('Quest login failed for %s: %s', interaction.user.id, exc)
            await interaction.followup.send(f'Đăng nhập lỗi: `{exc}`', ephemeral=True)
            return
        await interaction.followup.send(
            f"Đã lưu token cho **{user.get('global_name') or user.get('username')}**.\n"
            'Dùng `/scan` để xem quest, `/quest start` để chạy nền.',
            ephemeral=True,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.warning('Login modal error: %s', error)
        if interaction.response.is_done():
            await interaction.followup.send('Lỗi xử lý form. Thử `/quest login` lại.', ephemeral=True)
        else:
            await interaction.response.send_message('Lỗi xử lý form.', ephemeral=True)


# ── registration ───────────────────────────────────────────────────────────────
def register(tree: app_commands.CommandTree, owner_check) -> QuestService:
    """Attach /scan and the /quest group; returns the shared service."""
    service = QuestService()

    async def get_session(interaction: discord.Interaction) -> Optional[UserSession]:
        reason = service.reason_unavailable()
        if reason:
            await interaction.response.send_message(_not_configured(reason), ephemeral=True)
            return None
        session = service.session(interaction.user.id, str(interaction.user))
        if session is None:
            await interaction.response.send_message(_no_login(), ephemeral=True)
            return None
        return session

    @tree.command(name='scan', description='Quét quest của bạn một lần — chỉ đọc, không nhận')
    @app_commands.describe(show_completed='Hiện cả quest đã hoàn thành')
    async def scan(interaction: discord.Interaction, show_completed: bool = False):
        session = await get_session(interaction)
        if session is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            summary = await service.scan(session, dry_run=True)
        except Exception as exc:
            log.warning('/scan failed for %s: %s', interaction.user.id, exc)
            await interaction.followup.send(f'Scan lỗi: `{exc}`', ephemeral=True)
            return
        await interaction.followup.send(
            embed=scan_embed(summary, show_completed=show_completed), ephemeral=True
        )

    quest = app_commands.Group(
        name='quest',
        description='Quest của bạn: đăng nhập, chạy, cấu hình, chẩn đoán',
    )

    @quest.command(name='login', description='Lưu user token của bạn (mã hoá, không hiện lại)')
    async def quest_login(interaction: discord.Interaction):
        reason = service.reason_unavailable()
        if reason:
            await interaction.response.send_message(_not_configured(reason), ephemeral=True)
            return
        await interaction.response.send_modal(LoginModal(service))

    @quest.command(name='logout', description='Xoá token đã lưu và dừng worker của bạn')
    async def quest_logout(interaction: discord.Interaction):
        if service.reason_unavailable():
            await interaction.response.send_message(
                _not_configured(service.reason_unavailable()), ephemeral=True
            )
            return
        await interaction.response.defer(ephemeral=True)
        removed = await service.logout(interaction.user.id)
        await interaction.followup.send(
            'Đã xoá token và dừng worker.' if removed else 'Bạn chưa đăng nhập.',
            ephemeral=True,
        )

    @quest.command(name='whoami', description='Kiểm tra token đã lưu và build number')
    async def quest_whoami(interaction: discord.Interaction):
        session = await get_session(interaction)
        if session is None:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await service.identity(session)
        except Exception as exc:
            await interaction.followup.send(f'Kiểm tra lỗi: `{exc}`', ephemeral=True)
            return
        await interaction.followup.send(
            embed=identity_embed(result, session.token), ephemeral=True
        )

    @quest.command(name='start', description='Bật auto-scan nền cho tài khoản của bạn')
    @app_commands.describe(notify='Gửi thông báo vào channel này khi quest xong')
    async def quest_start(interaction: discord.Interaction, notify: bool = True):
        session = await get_session(interaction)
        if session is None:
            return
        if session.running:
            await interaction.response.send_message(
                f'Worker đang chạy (nhịp {session.interval}s). Dừng trước bằng `/quest stop`.',
                ephemeral=True,
            )
            return
        channel = interaction.channel if notify else None
        session.started_at = discord.utils.utcnow()
        session._worker = asyncio.create_task(
            _worker_loop(service, session, channel, interaction.user),
            name=f'quest-worker-{session.user_id}',
        )
        service.store.set_auto(
            session.user_id,
            True,
            getattr(interaction.channel, 'id', None),
        )
        await interaction.response.send_message(
            f'Đã bật auto-scan: quét mỗi **{session.interval}s**, '
            f"auto-accept **{'on' if session.settings['auto_accept'] else 'off'}**, "
            f"thông báo **{'on' if notify else 'off'}**.\n"
            'Worker tự bật lại sau khi bot restart.',
            ephemeral=True,
        )

    @quest.command(name='stop', description='Dừng auto-scan của bạn')
    async def quest_stop(interaction: discord.Interaction):
        session = await get_session(interaction)
        if session is None:
            return
        if not session.running and not session.busy:
            await interaction.response.send_message('Worker không chạy.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await _cancel(session._worker)
        await _cancel(session._oneshot)
        session._worker = None
        session._oneshot = None
        if service.store is not None:
            service.store.set_auto(session.user_id, False)
        await interaction.followup.send('Đã dừng auto-scan.', ephemeral=True)

    @quest.command(name='status', description='Xem trạng thái auto-scan của bạn')
    async def quest_status(interaction: discord.Interaction):
        session = await get_session(interaction)
        if session is None:
            return
        await interaction.response.send_message(
            embed=status_embed(
                session,
                auto_resume=service.store.get_auto(session.user_id) if service.store else False,
            ),
            ephemeral=True,
        )

    @quest.command(name='run', description='Chạy một chu kỳ đầy đủ ngay: nhận + hoàn thành')
    async def quest_run(interaction: discord.Interaction):
        session = await get_session(interaction)
        if session is None:
            return
        if session.busy or session.running:
            await interaction.response.send_message(
                'Đang có lượt chạy khác. `/quest status` để xem, hoặc `/quest stop`.',
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True)
        holder = await interaction.followup.send('Đang chạy một chu kỳ…', ephemeral=True)

        async def report(summary):
            try:
                if summary is None:
                    await holder.edit(content='Chu kỳ lỗi. `/quest status` để xem chi tiết.')
                else:
                    await holder.edit(
                        content=None, embed=scan_embed(summary, show_completed=True)
                    )
            except discord.HTTPException as exc:
                log.warning('One-shot report failed: %s', exc)

        async def runner():
            try:
                summary = await service.scan(session, dry_run=False)
                await report(summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.last_error = f'{type(exc).__name__}: {exc}'
                log.warning('One-shot quest run failed: %s', exc)
                await report(None)

        session._oneshot = asyncio.create_task(runner(), name=f'quest-oneshot-{session.user_id}')

    @quest.command(name='config', description='Xem cấu hình của bạn')
    async def quest_config(interaction: discord.Interaction):
        session = await get_session(interaction)
        if session is None:
            return
        await interaction.response.send_message(embed=settings_embed(session), ephemeral=True)

    @quest.command(name='set', description='Sửa một cấu hình của bạn')
    @app_commands.describe(key='Tên cấu hình', value='Giá trị: số, hoặc on/off')
    @app_commands.choices(
        key=[
            app_commands.Choice(name='interval — giây giữa các lần quét', value='interval'),
            app_commands.Choice(name='heartbeat — giây giữa heartbeat', value='heartbeat'),
            app_commands.Choice(name='auto_accept — tự nhận quest mới', value='auto_accept'),
            app_commands.Choice(name='log_progress — log tiến độ', value='log_progress'),
            app_commands.Choice(name='debug — log HTTP chi tiết', value='debug'),
        ]
    )
    async def quest_set(interaction: discord.Interaction, key: str, value: str):
        session = await get_session(interaction)
        if session is None:
            return
        try:
            changed = session.update_setting(key, value)
        except ValueError as exc:
            await interaction.response.send_message(
                f'Giá trị không hợp lệ cho `{key}`: {exc}', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f'Đã đặt {changed}. Worker đang chạy sẽ dùng ngay ở chu kỳ sau.', ephemeral=True
        )

    @quest.command(name='users', description='Số người đã lưu token (owner)')
    async def quest_users(interaction: discord.Interaction):
        if not owner_check(interaction.user):
            await interaction.response.send_message('Owner only.', ephemeral=True)
            return
        if service.store is None:
            await interaction.response.send_message(
                _not_configured(service.store_error or 'storage lỗi'), ephemeral=True
            )
            return
        await interaction.response.send_message(
            f'**{service.store.known_users()}** người đã lưu token · '
            f'**{len(service._sessions)}** session đang mở.',
            ephemeral=True,
        )

    tree.add_command(quest)
    return service


async def _worker_loop(service: QuestService, session: UserSession, channel, owner) -> None:
    log.info('Quest worker started for %s (interval %ss).', session.user_id, session.interval)
    try:
        while True:
            try:
                summary = await service.scan(session, dry_run=False)
                done = summary.get('completed_now') or []
                if done and channel is not None:
                    await channel.send(embed=completed_embed(done, owner))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                session.last_error = f'{type(exc).__name__}: {exc}'
                log.warning('Quest cycle failed for %s: %s', session.user_id, exc)
            await asyncio.sleep(session.interval)
    finally:
        log.info('Quest worker stopped for %s.', session.user_id)
