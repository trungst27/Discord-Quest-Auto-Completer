"""Quest slash commands: read-only /scan and the /quest control group.

Quest endpoints are user-scoped — `/quests/@me` answers 200 for a *user* token
and 401 for a bot token. The bot therefore reads `QUEST_TOKEN` (or
`USER_TOKEN`) from its environment and never falls back to `DISCORD_TOKEN`.

Without that variable the commands still register and explain what is missing,
so a deploy never ends up with a silent half-built menu.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

import discord
from discord import app_commands

log = logging.getLogger('bot.quests')

try:
    from main import DiscordAPI, QuestAutocompleter, fetch_latest_build_number
    MAIN_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - only on a broken checkout
    DiscordAPI = None  # type: ignore[assignment]
    QuestAutocompleter = None  # type: ignore[assignment]
    fetch_latest_build_number = None  # type: ignore[assignment]
    MAIN_IMPORT_ERROR = f'{type(exc).__name__}: {exc}'

STATUS_MARK = {'completed': '✓', 'enrolled': '▶', 'new': '○'}
MAX_QUEST_FIELDS = 25
DEFAULT_INTERVAL = 300


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


class QuestBridge:
    """Owns the blocking quest client and the background auto-scan task."""

    def __init__(self) -> None:
        self.token = (
            os.environ.get('QUEST_TOKEN') or os.environ.get('USER_TOKEN') or ''
        ).strip()
        self._completer = None
        self._lock = asyncio.Lock()
        self._task: Optional[asyncio.Task] = None
        self.interval = DEFAULT_INTERVAL
        self.started_at: Optional[discord.utils.utcnow] = None
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
            await asyncio.to_thread(self._build)
            summary = await asyncio.to_thread(self._completer.scan_once, dry_run)
            self.scans += 1
            self.last_at = discord.utils.utcnow()
            self.last_summary = summary
            self.last_error = summary.get('error')
            self.completed_total += len(summary.get('completed_now') or [])
            return summary

    # ── worker ─────────────────────────────────────────────────────────────────
    async def start_worker(self, interval: int, channel: Optional[discord.abc.Messageable]) -> None:
        self.interval = interval
        self.started_at = discord.utils.utcnow()
        self._task = asyncio.create_task(self._loop(channel), name='quest-worker')

    async def stop_worker(self) -> None:
        task, self._task = self._task, None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

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

    colour = discord.Colour.green() if not summary.get('error') else discord.Colour.red()
    embed = discord.Embed(
        title='Quest scan',
        colour=colour,
        description=(
            f"**{summary.get('total', 0)}** quest · "
            f"**{summary.get('enrolled', 0)}** đã nhận · "
            f"**{summary.get('completed', 0)}** hoàn thành · "
            f"**{summary.get('completable', 0)}** khả dụng"
        ),
        timestamp=discord.utils.utcnow(),
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
    embed.set_footer(text=' · '.join(footer) if footer else discord.utils.utcnow().strftime('%H:%M:%S'))
    return embed


def completed_embed(names: list) -> discord.Embed:
    return discord.Embed(
        title='Quest hoàn thành',
        description='\n'.join(f'✓ **{name}**' for name in names[:MAX_QUEST_FIELDS]),
        colour=discord.Colour.green(),
        timestamp=discord.utils.utcnow(),
    )


def status_embed(bridge: QuestBridge) -> discord.Embed:
    running = bridge.running
    colour = discord.Colour.green() if running else discord.Colour.greyple()
    embed = discord.Embed(title='Quest worker', colour=colour)

    state = 'đang chạy' if running else 'đã dừng'
    lines = [f'**Trạng thái:** {state}']
    if bridge.started_at and running:
        lines.append(f"**Bật lúc:** <t:{int(bridge.started_at.timestamp())}:R>")
        lines.append(f'**Nhịp quét:** {bridge.interval}s')
    lines.append(f"**Lần quét:** {bridge.scans}")
    lines.append(f"**Quest đã xong:** {bridge.completed_total}")
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
        description='Auto-complete quest — bật, tắt, xem trạng thái (owner)',
    )

    @quest.command(name='start', description='Bật auto-scan nền')
    @app_commands.describe(
        interval='Giây giữa các lần quét (60–3600, mặc định 300)',
        notify='Gửi thông báo vào channel này khi quest xong',
    )
    async def quest_start(
        interaction: discord.Interaction,
        interval: app_commands.Range[int, 60, 3600] = DEFAULT_INTERVAL,
        notify: bool = True,
    ):
        if not await gate(interaction) or await unavailable(interaction):
            return
        if bridge.running:
            await interaction.response.send_message(
                f'Worker đang chạy (nhịp {bridge.interval}s). Dừng trước bằng `/quest stop`.',
                ephemeral=True,
            )
            return
        channel = interaction.channel if notify else None
        await bridge.start_worker(int(interval), channel)
        await interaction.response.send_message(
            f'Đã bật auto-scan: quét mỗi **{int(interval)}s**, '
            f"thông báo {'bật' if notify else 'tắt'}. "
            'Quest dạng chơi/stream sẽ chạy đúng bằng thời lượng thật.',
            ephemeral=True,
        )

    @quest.command(name='stop', description='Dừng auto-scan nền')
    async def quest_stop(interaction: discord.Interaction):
        if not await gate(interaction):
            return
        if not bridge.running:
            await interaction.response.send_message('Worker không chạy.', ephemeral=True)
            return
        await bridge.stop_worker()
        await interaction.response.send_message('Đã dừng auto-scan.', ephemeral=True)

    @quest.command(name='status', description='Xem trạng thái auto-scan')
    async def quest_status(interaction: discord.Interaction):
        if not await gate(interaction):
            return
        await interaction.response.send_message(embed=status_embed(bridge), ephemeral=True)

    tree.add_command(quest)
    return bridge
