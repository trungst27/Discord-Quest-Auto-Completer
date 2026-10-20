"""Encrypted per-user quest token storage.

A user token grants full account access, so it never lands in a plaintext file
and never goes back out through Discord. Tokens are encrypted with Fernet using
a key from `QUEST_SECRET`, or a generated `quest-secret.key` beside the data file
when that variable is absent.

Both files are gitignored. Losing `quest-secret.key` makes stored tokens
unreadable — the user simply logs in again.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger('bot.quests')

BASE_DIR = Path(__file__).resolve().parent
DATA_PATH = BASE_DIR / 'quest-tokens.json'
KEY_PATH = BASE_DIR / 'quest-secret.key'

try:
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTO_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover - dependency missing on host
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment,misc]
    CRYPTO_ERROR = f'{type(exc).__name__}: {exc}'

DEFAULT_SETTINGS = {
    'interval': 300,
    'heartbeat': 20,
    'auto_accept': True,
    'log_progress': False,
    'debug': False,
}

SETTING_BOUNDS = {
    'interval': ('int', 60, 3600),
    'heartbeat': ('int', 5, 60),
    'auto_accept': ('bool', None, None),
    'log_progress': ('bool', None, None),
    'debug': ('bool', None, None),
}


def _read_key() -> bytes:
    env_key = os.environ.get('QUEST_SECRET', '').strip()
    if env_key:
        return env_key.encode()
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    log.warning('QUEST_SECRET not set; generated %s. Back it up or tokens become unreadable.', KEY_PATH.name)
    return key


def sanitize_settings(raw) -> dict:
    settings = dict(DEFAULT_SETTINGS)
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


class VaultError(RuntimeError):
    """Raised when encryption is unavailable or the key is unusable."""


class TokenVault:
    def __init__(self) -> None:
        if CRYPTO_ERROR or Fernet is None:
            raise VaultError(
                f'thiếu thư viện `cryptography` ({CRYPTO_ERROR}). '
                'Cài: pip install cryptography hoặc thêm vào requirements.txt'
            )
        try:
            self._fernet = Fernet(_read_key())
        except Exception as exc:
            raise VaultError(f'QUEST_SECRET không hợp lệ: {exc}') from exc

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> Optional[str]:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken:
            return None


class UserStore:
    """Per-user tokens plus their settings, persisted as one JSON file."""

    def __init__(self, path: Path = DATA_PATH) -> None:
        self.path = path
        self.vault = TokenVault()
        self._users: dict[str, dict] = {}
        self._load()

    # ── persistence ────────────────────────────────────────────────────────────
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.error('Could not read %s (%s); starting empty.', self.path.name, exc)
            return
        users = raw.get('users') if isinstance(raw, dict) else None
        if isinstance(users, dict):
            self._users = users
        log.info('Loaded %d quest login(s) from %s.', len(self._users), self.path.name)

    def _flush(self) -> None:
        payload = {'version': 1, 'users': self._users}
        tmp = self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(payload, indent=2), encoding='utf-8')
            os.replace(tmp, self.path)
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass
        except OSError as exc:
            log.error('Could not write %s: %s', self.path.name, exc)

    # ── users ──────────────────────────────────────────────────────────────────
    def has(self, user_id: int) -> bool:
        return str(user_id) in self._users

    def get_token(self, user_id: int) -> Optional[str]:
        record = self._users.get(str(user_id))
        if not record:
            return None
        ciphertext = record.get('token')
        if not ciphertext:
            return None
        token = self.vault.decrypt(ciphertext)
        if token is None:
            log.warning('Stored token for user %s cannot be decrypted (key changed).', user_id)
        return token

    def put_token(self, user_id: int, token: str, name: str = '') -> None:
        key = str(user_id)
        record = self._users.setdefault(key, {})
        record['token'] = self.vault.encrypt(token)
        record['name'] = name[:100]
        record['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        record.setdefault('settings', dict(DEFAULT_SETTINGS))
        self._flush()

    def remove(self, user_id: int) -> bool:
        removed = self._users.pop(str(user_id), None) is not None
        if removed:
            self._flush()
        return removed

    def get_settings(self, user_id: int) -> dict:
        record = self._users.get(str(user_id)) or {}
        return sanitize_settings(record.get('settings'))

    def set_settings(self, user_id: int, settings: dict) -> None:
        record = self._users.get(str(user_id))
        if not record:
            return
        record['settings'] = sanitize_settings(settings)
        self._flush()

    def known_users(self) -> int:
        return len(self._users)
