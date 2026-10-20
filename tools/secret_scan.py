#!/usr/bin/env python3
"""Block staged files that contain credentials.

Runs from .githooks/pre-commit against `git diff --cached --name-only`.
Exits 1 on any hit so the commit never lands. Anything reported here must be
rotated, not just removed — the value already touched the disk.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Files that must never be tracked, whatever their content.
FORBIDDEN_NAMES = {
    '.env',
    '.token',
    '.bot-token.xml',
    'credentials.json',
    'token.txt',
}

PATTERNS = [
    (
        'Discord bot token',
        # id.base64timestamp.hmac — the shape Discord actually issues.
        re.compile(r'\b[M-Za-z0-9_-]{23,28}\.[\w-]{6,7}\.[\w-]{27,39}\b'),
    ),
    (
        'Discord webhook URL',
        re.compile(r'https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+'),
    ),
    (
        'GitHub token',
        re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})\b'),
    ),
    (
        'private key block',
        re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----'),
    ),
    (
        'AWS access key',
        re.compile(r'\bAKIA[0-9A-Z]{16}\b'),
    ),
    (
        'assigned secret literal',
        # DISCORD_TOKEN=abc123... but not the empty template form.
        re.compile(r'(?i)\b(?:DISCORD_TOKEN|BOT_TOKEN|CLIENT_SECRET|API_KEY|PASSWORD)\s*=\s*["\']?[A-Za-z0-9_\-\.]{12,}'),
    ),
]

ALLOWLIST_SUFFIXES = {'.example', '.sample', '.md', '.jpg', '.jpeg', '.png', '.gif', '.ico'}
ALLOWLIST_EXACT = {'.env.example', 'requirements.txt', 'requirements-bot.txt'}


def staged_files() -> list[str]:
    out = subprocess.run(
        ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACM'],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    if out.returncode != 0:
        return []
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def is_allowlisted(path: str) -> bool:
    name = Path(path).name
    if name in ALLOWLIST_EXACT:
        return True
    return Path(path).suffix.lower() in ALLOWLIST_SUFFIXES


def scan(path: str) -> list[str]:
    findings: list[str] = []
    name = Path(path).name

    if name in FORBIDDEN_NAMES:
        return [f'{path}: forbidden file name ({name})']

    target = REPO_ROOT / path
    if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
        return findings

    try:
        text = target.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return findings

    for label, pattern in PATTERNS:
        for match in pattern.finditer(text):
            line_no = text.count('\n', 0, match.start()) + 1
            findings.append(f'{path}:{line_no}: possible {label}')
    return findings


def main() -> int:
    files = [f for f in staged_files() if not is_allowlisted(f)]
    findings: list[str] = []

    for path in files:
        findings.extend(scan(path))

    if not findings:
        print(f'secret-scan: clean ({len(files)} file(s) checked)')
        return 0

    print('secret-scan: COMMIT BLOCKED', file=sys.stderr)
    for item in findings:
        print(f'  {item}', file=sys.stderr)
    print(
        '\nRemove the value, rotate the credential, then stage again.\n'
        'If this is a false positive, amend tools/secret_scan.py.',
        file=sys.stderr,
    )
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
