"""Discord bot — slash commands, guild-scoped sync, cloud-ready.

Environment:
    DISCORD_TOKEN       required, bot token from the Developer Portal
    GUILD_ID            optional, sync slash commands to this guild instantly
                        (global sync can take up to 1 hour to propagate)
    DISCORD_GUILD_ID    alias accepted, used by botkeep.cloud panels
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import discord
from discord import app_commands
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / 'logs'

load_dotenv(BASE_DIR / '.env', override=False)


def setup_logging() -> logging.Logger:
    """Console always; rotating file when the filesystem allows it.

    Hosting panels often mount an ephemeral or read-only root, so a failed
    file handler must degrade to console-only instead of killing the bot.
    """
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)-8s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            LOG_DIR / 'discord.log', maxBytes=5 * 1024 * 1024, backupCount=3, encoding='utf-8'
        )
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning('File logging disabled (%s); console only.', exc)

    return logging.getLogger('bot')


log = setup_logging()

# Panels name this differently; accept both so a deploy never silently falls
# back to slow global sync.
GUILD_ID_RAW = (
    os.environ.get('GUILD_ID') or os.environ.get('DISCORD_GUILD_ID') or ''
).strip()

# Comma-separated Discord user IDs that may run owner-only commands.
# Empty means "nobody" — a missing config must never hand out admin rights.
OWNER_IDS = frozenset(
    int(part) for part in os.environ.get('OWNER_IDS', '').replace(' ', '').split(',')
    if part.isdigit()
)

STARTED_AT = None  # set in on_ready

# Commands that must never appear in the public help listing.
OWNER_COMMANDS = frozenset({'sync'})


def is_owner(user: discord.abc.User) -> bool:
    return user.id in OWNER_IDS


class DiscordBot(discord.Client):
    def __init__(self):
        # default() covers guilds + slash commands. Add members/message_content
        # the moment prefix commands or member events land.
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def _purge_global_commands(self) -> None:
        """Delete global commands so guild-scoped ones are not listed twice.

        Discord shows the union of global and guild command sets. Syncing to a
        guild while globals still exist renders every command twice, so the
        stale global set is wiped once at startup. The in-memory tree is left
        untouched, which keeps copy_global_to() working for new guilds.
        """
        try:
            existing = await self.http.get_global_commands(self.application_id)
            if not existing:
                return
            await self.http.bulk_upsert_global_commands(self.application_id, payload=[])
            log.info('Cleared %d stale global command(s).', len(existing))
        except discord.HTTPException as exc:
            log.warning('Global command purge skipped: %s', exc)

    async def setup_hook(self):
        """Guild sync is instant; global sync can lag up to an hour."""
        try:
            if GUILD_ID_RAW:
                await self._purge_global_commands()
                guild = discord.Object(id=int(GUILD_ID_RAW))
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info('Slash commands synced to guild %s: %d command(s).', GUILD_ID_RAW, len(synced))
            else:
                synced = await self.tree.sync()
                log.info(
                    'Slash commands synced globally: %d command(s). '
                    'Discord may take up to 1 hour to show them; set GUILD_ID for instant sync.',
                    len(synced),
                )
        except discord.HTTPException as exc:
            log.error('Slash command sync failed: %s', exc)

    async def on_ready(self):
        global STARTED_AT
        if STARTED_AT is None:
            STARTED_AT = discord.utils.utcnow()
        log.info('READY: %s (ID: %s) — %d guild(s)', self.user, self.user.id, len(self.guilds))
        for guild in self.guilds:
            log.info('  guild: %s (ID: %s)', guild.name, guild.id)
        if not OWNER_IDS:
            log.warning('OWNER_IDS is empty — owner-only commands are disabled for everyone.')
        if QUEST_SERVICE.configured:
            log.info(
                'Quest commands ready; %d user login(s) stored.',
                QUEST_SERVICE.store.known_users() if QUEST_SERVICE.store else 0,
            )
        else:
            log.warning(
                'Quest commands unavailable: %s. /scan and /quest will explain the fix.',
                QUEST_SERVICE.reason_unavailable(),
            )

    async def on_guild_join(self, guild: discord.Guild):
        log.info('Joined guild: %s (ID: %s)', guild.name, guild.id)
        try:
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            log.info('Slash commands synced to new guild %s.', guild.id)
        except discord.HTTPException as exc:
            log.error('Sync on join failed for %s: %s', guild.id, exc)

    async def on_disconnect(self):
        log.warning('Gateway disconnected. discord.py will try to resume.')


bot = DiscordBot()

# /scan and /quest live in their own module: each user stores their own user
# token, which is unrelated to the gateway this client runs on.
from bot_quests import register as register_quest_commands  # noqa: E402

QUEST_SERVICE = register_quest_commands(bot.tree, is_owner)


@bot.tree.command(name='ping', description='Check whether the bot is online')
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        f'Pong! Bot is online. Latency: {round(bot.latency * 1000)} ms',
        ephemeral=True,
    )


@bot.tree.command(name='info', description='Show bot and server details')
async def info(interaction: discord.Interaction):
    guild = interaction.guild
    lines = [
        f'**Bot:** {bot.user} (`{bot.user.id}`)',
        f'**Latency:** {round(bot.latency * 1000)} ms',
        f'**Servers:** {len(bot.guilds)}',
        f'**discord.py:** {discord.__version__}',
    ]
    if guild:
        lines.insert(0, f'**Server:** {guild.name} (`{guild.id}`) — {guild.member_count} members')
    await interaction.response.send_message('\n'.join(lines), ephemeral=True)


@bot.tree.command(name='help', description='List available commands')
async def help_command(interaction: discord.Interaction):
    public = sorted(cmd.name for cmd in bot.tree.get_commands() if cmd.name not in OWNER_COMMANDS)
    lines = ['**Commands**', *[f'`/{name}`' for name in public]]
    if is_owner(interaction.user):
        lines.append('\n**Owner only**')
        lines.extend(f'`/{name}`' for name in sorted(OWNER_COMMANDS))
    await interaction.response.send_message('\n'.join(lines), ephemeral=True)


@bot.tree.command(name='stats', description='Show uptime and server count')
async def stats(interaction: discord.Interaction):
    if STARTED_AT is None:
        uptime = 'starting'
    else:
        delta = discord.utils.utcnow() - STARTED_AT
        hours, rem = divmod(int(delta.total_seconds()), 3600)
        minutes, seconds = divmod(rem, 60)
        uptime = f'{hours}h {minutes}m {seconds}s'
    await interaction.response.send_message(
        f'**Uptime:** {uptime}\n'
        f'**Servers:** {len(bot.guilds)}\n'
        f'**Latency:** {round(bot.latency * 1000)} ms',
        ephemeral=True,
    )


@bot.tree.command(name='sync', description='Re-sync slash commands (owner only)')
async def sync(interaction: discord.Interaction):
    if not is_owner(interaction.user):
        await interaction.response.send_message('Owner only.', ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        if interaction.guild:
            bot.tree.copy_global_to(guild=interaction.guild)
            synced = await bot.tree.sync(guild=interaction.guild)
            scope = f'guild `{interaction.guild.id}`'
        else:
            synced = await bot.tree.sync()
            scope = 'global'
        log.info('Manual sync by %s: %d command(s) to %s.', interaction.user, len(synced), scope)
        await interaction.followup.send(f'Synced {len(synced)} command(s) to {scope}.', ephemeral=True)
    except discord.HTTPException as exc:
        log.error('Manual sync failed: %s', exc)
        await interaction.followup.send(f'Sync failed: `{exc}`', ephemeral=True)


def main() -> int:
    token = os.environ.get('DISCORD_TOKEN', '').strip()
    if not token:
        log.error(
            'Missing DISCORD_TOKEN. Set it in the hosting panel environment, '
            'or put DISCORD_TOKEN=... in %s', BASE_DIR / '.env'
        )
        return 1

    # Bot tokens are three dot-separated segments; user tokens are not.
    if token.count('.') != 2:
        log.error(
            'DISCORD_TOKEN does not look like a bot token (expected 3 segments). '
            'A user token yields 401 on every call.'
        )
        return 1

    try:
        bot.run(token, log_level=logging.INFO)
    except discord.LoginFailure:
        log.error('Discord rejected the token (LoginFailure/401). Regenerate it in the Developer Portal.')
        return 1
    except KeyboardInterrupt:
        log.info('Stopped by user.')
        return 0
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
