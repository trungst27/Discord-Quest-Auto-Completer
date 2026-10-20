"""Quest slash commands: /scan plus the /quest control group.

Quest endpoints are user-scoped — `/quests/@me` answers 200 for a *user* token
and 401 for a bot token. The bot therefore reads `QUEST_TOKEN` (or
`USER_TOKEN`) from its environment and never falls back to `DISCORD_TOKEN`.

Runtime knobs mirror the constants documented in README.md:

    POLL_INTERVAL       -> /quest set interval
    HEARTBEAT_INTERVAL  -> /quest set heartbeat
    AUTO_ACCEPT         -> /quest set auto_accept
    LOG_PROGRESS        -> /quest set log_progress
    DEBUG               -> /quest set debug

They are written to main.py's module globals before every cycle, because those
functions read the globals at call time (not at import), and persisted to
quest-settings.json so a restart keeps them.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Optional

import discord
from discord import app_commands

log = logging.getLogger('bot.quests')

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_PATH = BASE_DIR / 'quest-settings.json'

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

DEFAULT_SETTINGS = {
    'interval': 300,
    'heartbeat': 20,
    'auto_accept': True,
    'log_progress': False,
    'debug': False,
}

# key -> (kind, min, max). Bounds keep a typo from hammering the API.
SETTING_BOUNDS = {
    'interval': ('int', 60, 3600),
    'heartbeat': ('int', 5, 60),
    'auto_accept': ('bool', None, None),
    'log_progress': ('bool', None, None),
    'debug': ('bool', None, None),
}

TRUTHY = {'1', 'true', 'on', 'yes', 'bat', 'bật'}
FALSY = {'0', 'false', 'off', 'no', 'tat', 'tắt'}


class QuestNotConfigured(RuntimeError):
    """Raised when the quest client cannot be built from the environment."""


def _not_configured(reason: str) -> str:
    return (
        f'**Quest chưa cấu hình** — {reason}\n\n'
        'Cần **user token** trong biến môi trường `QUEST_TOKEN`:\n'
        '```\nQUEST_TOKEN=<user token>\n```\n'
        'Bot token không đọc được `/quests/@me` (401), nên đừng dùng `DISCORD_TOKEN`.\n'
        'Lấy user token: `discord.com/app` → Ctrl+Shift+I → Network → header `authorization`.'
    )


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        raw = json.loads(SETTINGS_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return settings
    if not isinstance(raw, dict):
        return settings
    for key, value in raw.items():
        if key not in SETTING_BOUNDS:
            continue
        kind, low, high = SETTING_BOUNDS[key]
        if kind == 'bool' and isinstance(value, bool):
            settings[key] = value
        elif kind == 'int' and isinstance(value, int) and low <= value <= high:
            settings[key] = value
    return settings


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_PATH.write_text(json.dumps(settings, indent=2), encoding='utf-8')
    except OSError as exc:
        log.warning('Could not persist quest settings: %s', exc)


def parse_setting(key: str, raw: str) -> tuple[bool, object]:
    """Return (ok, value_or_error_message)."""
    kind, low, high = SETTING_BOUNDS[key]
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
    """Push settings into main.py's module globals (read at call time)."""
    if quest_main is None:
        return
    quest_main.POLL_INTERVAL = settings['interval']
    quest_main.HEARTBEAT_INTERVAL = settings['heartbeat']
    quest_main.AUTO_ACCEPT = settings['auto_accept']
    quest_main.LOG_PROGRESS = settings['log_progress']
    quest_main.DEBUG = settings['debug']


class QuestBridge:
    """Owns the blocking quest client, settings, and the background worker."""

    def __init__(self) -> None:
        self.token = (
            os.environ.get('QUEST_TOKEN') or os.environ.get('USER_TOKEN') or ''
        ).strip()
        self.settings = load_settings()
        apply_settings(self.settings)
        self._completer = None
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self._oneshot: Optional[asyncio.Task] = None
        self.started_at = None
        self.scans = 0
        self.completed_total = 0
        self.last_at = None
        self.last_error: Optional[str] = None
        self.last_summary: Optional[dict] = None

    # ── availability ───────────────────────────────────────────────────────────
    def reason_unavailable(self) -> Optional[str]:
        if MAIN_IMPORT_ERROR:
            return f'không import được `main.py` ({MAIN_IMPORT_ERROR})'
        if not self.token:
            return 'thiếu `QUEST_TOKEN`'
        return None

    @property
    def configured(self) -> bool:
        return self.reason_unavailable() is None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def busy(self) -> bool:
        if self._lock.locked():
            return True
        return self._oneshot is not None and not self._oneshot.done()

    @property
    def interval(self) -> int:
        return self.settings['interval']

    # ── settings ───────────────────────────────────────────────────────────────
    def update_setting(self, key: str, raw: str) -> str:
        ok, value = parse_setting(key, raw)
        if not ok:
            raise ValueError(str(value))
        self.settings[key] = value
        save_settings(self.settings)
        apply_settings(self.settings)
        return f'`{key}` = **{value}**'

    # ── client ─────────────────────────────────────────────────────────────────
    def _build(self) -> None:
        if self._completer is not None:
            return
        build = fetch_latest_build_number()
        self._completer = QuestAutocompleter(DiscordAPI(self.token, build))
        log.info('Quest client ready (client_build_number=%s).', build)

    async def scan(self, dry_run: bool) -> dict:
        reason = self.reason_unavailable()
        if reason:
            raise QuestNotConfigured(reason)
        async with self._lock:
            apply_settings(self.settings)
            await asyncio.to_thread(self._build)
            summary = await asyncio.to_thread(self._completer.scan_once, dry_run)
            self.scans += 1
            self.last_at = discord.utils.utcnow()
            self.last_summary = summary
            self.last_error = summary.get('error')
            self.completed_total += len(summary.get('completed_now') or [])
            return summary

    async def identity(self) -> dict:
        """Cheap credential probe: build number + /users/@me."""
        reason = self.reason_unavailable()
        if reason:
            raise QuestNotConfigured(reason)
        await asyncio.to_thread(self._build)
        build = await asyncio.to_thread(fetch_latest_build_number)
        response = await asyncio.to_thread(self._completer.api.get, '/users/@me')
        return {
            'build': build,
            'status': response.status_code,
            'user': response.json() if response.status_code == 200 else None,
        }

    # ── worker ─────────────────────────────────────────────────────────────────
    async def start_worker(self, channel: Optional[discord.abc.Messageable]) -> None:
        self.started_at = discord.utils.utcnow()
        self._task = asyncio.create_task(self._loop(channel), name='quest-worker')

    async def stop_worker(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        oneshot, self._oneshot = self._oneshot, None
        if oneshot is not None and not oneshot.done():
            oneshot.cancel()
            try:
                await oneshot
            except (asyncio.CancelledError, Exception):
                pass

    def start_oneshot(self, callback) -> None:
        async def runner():
            try:
                summary = await self.scan(dry_run=False)
                await callback(summary)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f'{type(exc).__name__}: {exc}'
                log.warning('One-shot quest run failed: %s', exc)
                await callback(None)
        self._oneshot = asyncio.create_task(runner(), name='quest-oneshot')

    async def _loop(self, channel: Optional[discord.abc.Messageable]) -> None:
        log.info('Quest worker started (interval %ss).', self.interval)
        try:
            while True:
                try:
                    summary = await self.scan(dry_run=False)
                    done = summary.get('completed_now') or []
                    if done and channel is not None:
                        await channel.send(embed=completed_embed(done))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = f'{type(exc).__name__}: {exc}'
                    log.warning('Quest cycle failed: %s', exc)
                await asyncio.sleep(self.interval)
        finally:
            log.info('Quest worker stopped.')


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


def completed_embed(names: list) -> discord.Embed:
    return discord.Embed(
        title='Quest hoàn thành',
        description='\n'.join(f'✓ **{name}**' for name in names[:MAX_QUEST_FIELDS]),
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )


def settings_embed(bridge: QuestBridge) -> discord.Embed:
    s = bridge.settings
    embed = discord.Embed(
        title='Quest settings',
        colour=discord.Colour.blurple(),
        description=(
            f"`interval` = **{s['interval']}** — giây giữa các lần quét\n"
            f"`heartbeat` = **{s['heartbeat']}** — giây giữa heartbeat\n"
            f"`auto_accept` = **{'on' if s['auto_accept'] else 'off'}** — tự nhận quest mới\n"
            f"`log_progress` = **{'on' if s['log_progress'] else 'off'}** — log tiến độ ra console\n"
            f"`debug` = **{'on' if s['debug'] else 'off'}** — log HTTP chi tiết"
        ),
    )
    embed.set_footer(text='Sửa bằng /quest set · lưu vào quest-settings.json')
    return embed


def status_embed(bridge: QuestBridge) -> discord.Embed:
    running = bridge.running
    colour = discord.Colour.green() if running else discord.Colour.greyple()
    embed = discord.Embed(title='Quest worker', colour=colour)

    state = 'đang chạy' if running else 'đã dừng'
    lines = [f'**Trạng thái:** {state}']
    if bridge.started_at and running:
        lines.append(f"**Bật lúc:** <t:{int(bridge.started_at.timestamp())}:R>")
        lines.append(f"**Nhịp quét:** {bridge.interval}s")
    lines.append(f'**Lần quét:** {bridge.scans}')
    lines.append(f'**Quest đã xong:** {bridge.completed_total}')
    lines.append(f"**Auto-accept:** {'on' if bridge.settings['auto_accept'] else 'off'}")
    if bridge.last_at:
        lines.append(f"**Quét gần nhất:** <t:{int(bridge.last_at.timestamp())}:R>")
    if bridge.last_error:
        lines.append(f"**Lỗi gần nhất:** `{str(bridge.last_error)[:300]}`")
    embed.description = '\n'.join(lines)

    summary = bridge.last_summary
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
    if not bridge.configured:
        embed.add_field(name='Cấu hình', value=bridge.reason_unavailable() or '', inline=False)
    return embed


def build_embed(result: dict, token: str) -> discord.Embed:
    user = result.get('user') or {}
    ok = result.get('status') == 200 and not user.get('bot', True)
    embed = discord.Embed(
        title='Quest credentials',
        colour=discord.Colour.green() if ok else discord.Colour.red(),
    )
    embed.add_field(name='client_build_number', value=f"`{result.get('build')}`", inline=True)
    embed.add_field(name='GET /users/@me', value=f"`{result.get('status')}`", inline=True)
    embed.add_field(name='Token', value=f'`{len(token)} ký tự`', inline=True)
    if user:
        embed.add_field(
            name='Đăng nhập',
            value=f"**{user.get('global_name') or user.get('username')}** "
                  f"(`{user.get('username')}` · ID `{user.get('id')}`)",
            inline=False,
        )
        if user.get('bot'):
            embed.add_field(
                name='Sai loại token',
                value='Đây là **bot token**. Quest cần user token.',
                inline=False,
            )
    else:
        embed.add_field(
            name='Lỗi',
            value='Token chết hoặc bị đăng xuất. Lấy token mới từ trình duyệt.',
            inline=False,
        )
    return embed


# ── registration ───────────────────────────────────────────────────────────────
def register(tree: app_commands.CommandTree, owner_check) -> QuestBridge:
    """Attach /scan and /quest to the tree and return the shared bridge."""
    bridge = QuestBridge()

    async def gate(interaction: discord.Interaction) -> bool:
        if owner_check(interaction.user):
            return True
        await interaction.response.send_message('Owner only.', ephemeral=True)
        return False

    async def unavailable(interaction: discord.Interaction) -> bool:
        reason = bridge.reason_unavailable()
        if reason is None:
            return False
        await interaction.response.send_message(_not_configured(reason), ephemeral=True)
        return True

    @tree.command(name='scan', description='Quét quest một lần — chỉ đọc, không nhận (owner)')
    @app_commands.describe(show_completed='Hiện cả quest đã hoàn thành')
    async def scan(interaction: discord.Interaction, show_completed: bool = False):
        if not await gate(interaction) or await unavailable(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            summary = await bridge.scan(dry_run=True)
        except Exception as exc:
            log.warning('/scan failed: %s', exc)
            await interaction.followup.send(f'Scan lỗi: `{exc}`', ephemeral=True)
            return
        await interaction.followup.send(
            embed=scan_embed(summary, show_completed=show_completed), ephemeral=True
        )

    quest = app_commands.Group(
        name='quest',
        description='Auto-complete quest — chạy, cấu hình, chẩn đoán (owner)',
    )

    @quest.command(name='start', description='Bật auto-scan nền: quét → nhận → hoàn thành')
    @app_commands.describe(notify='Gửi thông báo vào channel này khi quest xong')
    async def quest_start(interaction: discord.Interaction, notify: bool = True):
        if not await gate(interaction) or await unavailable(interaction):
            return
        if bridge.running:
            await interaction.response.send_message(
                f'Worker đang chạy (nhịp {bridge.interval}s). Dừng trước bằng `/quest stop`.',
                ephemeral=True,
            )
            return
        channel = interaction.channel if notify else None
        await bridge.start_worker(channel)
        await interaction.response.send_message(
            f'Đã bật auto-scan: quét mỗi **{bridge.interval}s**, '
            f"auto-accept **{'on' if bridge.settings['auto_accept'] else 'off'}**, "
            f"thông báo **{'on' if notify else 'off'}**.\n"
            'Quest chơi/stream chạy đúng bằng thời lượng thật.',
            ephemeral=True,
        )

    @quest.command(name='stop', description='Dừng auto-scan nền')
    async def quest_stop(interaction: discord.Interaction):
        if not await gate(interaction):
            return
        if not bridge.running and not bridge.busy:
            await interaction.response.send_message('Worker không chạy.', ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await bridge.stop_worker()
        await interaction.followup.send('Đã dừng auto-scan.', ephemeral=True)

    @quest.command(name='status', description='Xem trạng thái auto-scan')
    async def quest_status(interaction: discord.Interaction):
        if not await gate(interaction):
            return
        await interaction.response.send_message(embed=status_embed(bridge), ephemeral=True)

    @quest.command(name='run', description='Chạy một chu kỳ đầy đủ ngay: nhận + hoàn thành')
    async def quest_run(interaction: discord.Interaction):
        if not await gate(interaction) or await unavailable(interaction):
            return
        if bridge.busy:
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
                    await holder.edit(content=None, embed=scan_embed(summary, show_completed=True))
            except discord.HTTPException as exc:
                log.warning('One-shot report failed: %s', exc)

        bridge.start_oneshot(report)

    @quest.command(name='config', description='Xem cấu hình hiện tại')
    async def quest_config(interaction: discord.Interaction):
        if not await gate(interaction):
            return
        await interaction.response.send_message(embed=settings_embed(bridge), ephemeral=True)

    @quest.command(name='set', description='Sửa một cấu hình')
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
        if not await gate(interaction):
            return
        try:
            changed = bridge.update_setting(key, value)
        except ValueError as exc:
            await interaction.response.send_message(
                f'Giá trị không hợp lệ cho `{key}`: {exc}', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f'Đã đặt {changed}. Worker đang chạy sẽ dùng ngay ở chu kỳ sau.', ephemeral=True
        )

    @quest.command(name='build', description='Kiểm tra user token và client build number')
    async def quest_build(interaction: discord.Interaction):
        if not await gate(interaction) or await unavailable(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            result = await bridge.identity()
        except Exception as exc:
            await interaction.followup.send(f'Kiểm tra lỗi: `{exc}`', ephemeral=True)
            return
        await interaction.followup.send(
            embed=build_embed(result, bridge.token), ephemeral=True
        )

    tree.add_command(quest)
    return bridge
