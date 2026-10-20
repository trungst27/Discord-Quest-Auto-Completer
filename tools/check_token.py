#!/usr/bin/env python3
"""Verify a bot token and its slash-command registration.

Usage:
    python tools/check_token.py                 # token from DISCORD_TOKEN / .env
    python tools/check_token.py <TOKEN>         # token as argument

Exit codes: 0 ok, 1 bad token, 2 missing token.

The same token the panel holds is what this checks, so a clean run here means
the deployment's credential is valid. It does not prove the panel process is
alive — only its console shows READY.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)

UA = 'DiscordBot checker (https://github.com/trungst27/Discord-Quest-Auto-Completer, 1.0)'
GUILD_RAW = (os.environ.get('GUILD_ID') or os.environ.get('DISCORD_GUILD_ID') or '').strip()


def get_token() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1].strip()
    return os.environ.get('DISCORD_TOKEN', '').strip()


def main() -> int:
    token = get_token()
    if not token:
        print('FAIL: no token. Pass one as an argument or set DISCORD_TOKEN.')
        return 2

    segments = token.count('.') + 1
    print(f'token      : {len(token)} chars, {segments} segments', end='')
    if segments != 3:
        print('  <- WRONG SHAPE (expected 3 segments: id.signature.hmac)')
        return 1
    print()

    h = {'Authorization': f'Bot {token}', 'User-Agent': UA}
    r = requests.get('https://discord.com/api/v10/users/@me', headers=h, timeout=20)
    if r.status_code == 401:
        print('status     : 401 Unauthorized')
        print('verdict    : TOKEN DEAD - reset it in Developer Portal -> Bot')
        return 1
    r.raise_for_status()

    me = r.json()
    if not me.get('bot'):
        print('verdict    : this is a USER token, not a bot token')
        return 1

    print(f'identity   : {me["username"]} (id {me["id"]})')
    app = requests.get('https://discord.com/api/v10/oauth2/applications/@me', headers=h, timeout=20).json()
    print(f'application: {app.get("name")} (id {app.get("id")})')

    guilds = requests.get('https://discord.com/api/v10/users/@me/guilds', headers=h, timeout=20).json()
    if isinstance(guilds, list):
        print(f'guilds     : {len(guilds)}')
        for g in guilds:
            print(f'  - {g["name"]} ({g["id"]})')

    if GUILD_RAW:
        cmds = requests.get(
            f'https://discord.com/api/v10/applications/{app["id"]}/guilds/{GUILD_RAW}/commands',
            headers=h, timeout=20,
        ).json()
        if isinstance(cmds, list):
            names = sorted(c['name'] for c in cmds)
            print(f'commands   : {len(names)} in guild {GUILD_RAW} -> {names}')
        else:
            print(f'commands   : error {cmds}')
    else:
        cmds = requests.get(
            f'https://discord.com/api/v10/applications/{app["id"]}/commands',
            headers=h, timeout=20,
        ).json()
        names = sorted(c['name'] for c in cmds) if isinstance(cmds, list) else []
        print(f'commands   : {len(names)} global -> {names}')

    print('verdict    : TOKEN VALID')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
