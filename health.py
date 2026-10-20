"""Optional HTTP health endpoint so an uptime monitor can prove the bot is alive.

Disabled unless HEALTH_PORT is set: a hosting panel without an exposed port must
never lose the bot to a bind failure.
"""

import asyncio
import logging
import os
import time

import aiohttp
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


async def start_ping(bot, service=None):
    """Outbound heartbeat for hosts that never expose an inbound port.

    HEALTHCHECK_URL comes from healthchecks.io (or any endpoint that records a
    GET). A failed ping only logs; it must never take the bot down.
    """
    url = os.environ.get('HEALTHCHECK_URL', '').strip()
    if not url:
        return None

    raw = os.environ.get('HEALTHCHECK_INTERVAL', '').strip()
    interval = int(raw) if raw.isdigit() and int(raw) >= 1 else 300

    state = getattr(bot, 'health_state', None) or HealthState(bot, service)
    bot.health_state = state
    timeout = aiohttp.ClientTimeout(total=15)

    async def loop():
        while True:
            await asyncio.sleep(interval)
            payload = state.snapshot()
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(url, json=payload) as response:
                        if response.status >= 400:
                            log.warning('Healthcheck ping returned %s.', response.status)
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning('Healthcheck ping failed: %s', exc)

    task = asyncio.create_task(loop(), name='healthcheck-ping')
    log.info('Outbound healthcheck enabled: %s every %ss.', url.split('?')[0], interval)
    return task
