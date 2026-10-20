"""Optional HTTP health endpoint so an uptime monitor can prove the bot is alive.

Disabled unless HEALTH_PORT is set: a hosting panel without an exposed port must
never lose the bot to a bind failure.
"""

import logging
import os
import time

from aiohttp import web

log = logging.getLogger('bot.health')

_STARTED = time.monotonic()


class HealthState:
    """Read-only view of the parts an external monitor cares about."""

    def __init__(self, bot, service=None):
        self.bot = bot
        self.service = service
        self.ready = False
        self.started_at = _STARTED

    def snapshot(self) -> dict:
        service = self.service
        users = 0
        workers = 0
        if service is not None:
            if service.store is not None:
                users = service.store.known_users()
            workers = sum(1 for s in service._sessions.values() if s.running)
        latency = self.bot.latency
        return {
            'status': 'ok' if self.ready else 'starting',
            'bot': str(self.bot.user) if self.bot.user else None,
            'bot_id': self.bot.user.id if self.bot.user else None,
            'guilds': len(self.bot.guilds),
            'latency_ms': round(latency * 1000) if latency else None,
            'users': users,
            'workers': workers,
            'uptime_s': int(time.monotonic() - self.started_at),
        }


async def start(bot, service=None, port=None) -> web.AppRunner:
    """Serve /health on 0.0.0.0:<port>. Raises OSError if the port is taken."""
    if port is None:
        raw = os.environ.get('HEALTH_PORT', '').strip()
        if not raw.isdigit():
            return None
        port = int(raw)

    state = HealthState(bot, service)

    async def handle(_request):
        payload = state.snapshot()
        return web.json_response(payload, status=200)

    app = web.Application()
    app.router.add_get('/health', handle)
    app.router.add_get('/', handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    bot.health_state = state
    log.info('Health endpoint listening on 0.0.0.0:%d (/health).', port)
    return runner
