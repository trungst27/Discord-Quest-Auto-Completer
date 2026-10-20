#!/usr/bin/env python3
"""Delete global slash commands for a bot, leaving guild-scoped ones alone.

Discord renders the union of global and guild command sets, so a bot that once
synced globally then moved to guild sync shows every command twice. This wipes
the stale global set immediately, without waiting for a panel redeploy.

Usage:
    python tools/purge_global_commands.py <BOT_TOKEN>
    DISCORD_TOKEN=... python tools/purge_global_commands.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)

UA = 'DiscordBot maintenance (https://github.com/trungst27/Discord-Quest-Auto-Completer, 1.0)'
API = 'https://discord.com/api/v10'


def main() -> int:
    token = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get('DISCORD_TOKEN', '')).strip()
    if not token:
        print('FAIL: pass the bot token as an argument or set DISCORD_TOKEN.')
        return 2

    h = {'Authorization': f'Bot {token}', 'User-Agent': UA}

    who = requests.get(f'{API}/users/@me', headers=h, timeout=20)
    if who.status_code == 401:
        print('FAIL: token dead (401). Reset it in the Developer Portal.')
        return 1
    who.raise_for_status()
    me = who.json()
    if not me.get('bot'):
        print('FAIL: that is a user token, not a bot token.')
        return 1

    app = requests.get(f'{API}/oauth2/applications/@me', headers=h, timeout=20).json()
    app_id = app['id']
    print(f'bot        : {me["username"]} ({me["id"]})')
    print(f'application: {app.get("name")} ({app_id})')

    existing = requests.get(f'{API}/applications/{app_id}/commands', headers=h, timeout=20).json()
    if not isinstance(existing, list):
        print(f'FAIL: could not list global commands -> {existing}')
        return 1

    if not existing:
        print('global     : already empty (0 commands)')
    else:
        names = [c['name'] for c in existing]
        print(f'global     : {len(existing)} command(s) -> {names}')
        r = requests.put(f'{API}/applications/{app_id}/commands', headers=h, json=[], timeout=20)
        if r.status_code not in (200, 204):
            print(f'FAIL: purge returned {r.status_code} -> {r.text[:200]}')
            return 1
        print(f'global     : CLEARED ({len(existing)} removed)')

    guild_raw = (os.environ.get('GUILD_ID') or os.environ.get('DISCORD_GUILD_ID') or '').strip()
    if guild_raw:
        g = requests.get(f'{API}/applications/{app_id}/guilds/{guild_raw}/commands', headers=h, timeout=20).json()
        if isinstance(g, list):
            print(f'guild {guild_raw}: {len(g)} command(s) -> {sorted(c["name"] for c in g)}')

    print('verdict    : duplicate source removed. Ctrl+R in Discord.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
