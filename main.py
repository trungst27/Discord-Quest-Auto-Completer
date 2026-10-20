#!/usr/bin/env python3
"""
CTDOTEAM - Discord Quest Auto-Completer
"""

import requests
import time
import json
import random
import sys
import os
import re
import base64
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"), override=False)

# ── Shutdown ───────────────────────────────────────────────────────────────────
# Quest work runs in worker threads (asyncio.to_thread). Those threads hold the
# interpreter open, so a panel stop waits for a 15-minute PLAY_ON_DESKTOP cycle
# unless something interrupts the sleeps. Must derive from BaseException so the
# broad `except Exception` blocks around HTTP calls do not swallow it.
SHUTDOWN = threading.Event()


class ShutdownRequested(BaseException):
    """Raised inside quest work when the host asked the process to stop."""


def request_shutdown() -> None:
    SHUTDOWN.set()


def wait_or_abort(seconds: float) -> None:
    """Sleep, but wake immediately once shutdown has been requested."""
    if SHUTDOWN.wait(max(0.0, seconds)):
        raise ShutdownRequested()


# ── Config ─────────────────────────────────────────────────────────────────────
API_BASE = "https://discord.com/api/v9"
POLL_INTERVAL = 60          # seconds between quest scans
HEARTBEAT_INTERVAL = 20     # seconds between heartbeat calls
AUTO_ACCEPT = True          # auto-enroll in all available quests
LOG_PROGRESS = True
DEBUG = True                # verbose debug logging

SUPPORTED_TASKS = [
    "WATCH_VIDEO",
    "PLAY_ON_DESKTOP",
    "STREAM_ON_DESKTOP",
    "PLAY_ACTIVITY",
    "WATCH_VIDEO_ON_MOBILE",
]


# ── Logging ────────────────────────────────────────────────────────────────────
class Colors:
    RESET  = "\033[0m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    RED    = "\033[91m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"


def log(msg: str, level: str = "info"):
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = {
        "info":     f"{Colors.CYAN}[INFO]{Colors.RESET}",
        "ok":       f"{Colors.GREEN}[  OK]{Colors.RESET}",
        "warn":     f"{Colors.YELLOW}[WARN]{Colors.RESET}",
        "error":    f"{Colors.RED}[ ERR]{Colors.RESET}",
        "progress": f"{Colors.DIM}[PROG]{Colors.RESET}",
        "debug":    f"{Colors.DIM}[DBG ]{Colors.RESET}",
    }.get(level, f"[{level.upper()}]")

    if level == "debug" and not DEBUG:
        return
    if LOG_PROGRESS or level != "progress":
        print(f"{Colors.DIM}{ts}{Colors.RESET} {prefix} {msg}")


# ── Build number fetcher ───────────────────────────────────────────────────────
def fetch_latest_build_number() -> int:
    """Scrape Discord web app to get the latest client_build_number."""
    FALLBACK = 504649
    try:
        log("Đang lấy build number mới nhất từ Discord...", "info")
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        r = requests.get("https://discord.com/app", headers={"User-Agent": ua}, timeout=15)
        if r.status_code != 200:
            log(f"Không lấy được trang Discord ({r.status_code}), dùng fallback", "warn")
            return FALLBACK

        scripts = re.findall(r'/assets/([a-f0-9]+)\.js', r.text)
        if not scripts:
            scripts_alt = re.findall(r'src="(/assets/[^"]+\.js)"', r.text)
            scripts = [s.split('/')[-1].replace('.js', '') for s in scripts_alt]

        if not scripts:
            log("Không tìm thấy JS assets, dùng fallback", "warn")
            return FALLBACK

        for asset_hash in scripts[-5:]:
            try:
                ar = requests.get(
                    f"https://discord.com/assets/{asset_hash}.js",
                    headers={"User-Agent": ua}, timeout=15
                )
                m = re.search(r'buildNumber["\s:]+["\s]*(\d{5,7})', ar.text)
                if m:
                    bn = int(m.group(1))
                    log(f"Build number: {Colors.BOLD}{bn}{Colors.RESET}", "ok")
                    return bn
            except Exception:
                continue

        log(f"Không tìm thấy build number, dùng fallback {FALLBACK}", "warn")
        return FALLBACK
    except Exception as e:
        log(f"Lỗi lấy build number: {e}, dùng fallback {FALLBACK}", "warn")
        return FALLBACK


def make_super_properties(build_number: int) -> str:
    """Create base64-encoded X-Super-Properties header."""
    obj = {
        "os": "Windows",
        "browser": "Discord Client",
        "release_channel": "stable",
        "client_version": "1.0.9175",
        "os_version": "10.0.26100",
        "os_arch": "x64",
        "app_arch": "x64",
        "system_locale": "en-US",
        "browser_user_agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        ),
        "browser_version": "32.2.7",
        "client_build_number": build_number,
        "native_build_number": 59498,
        "client_event_source": None,
    }
    return base64.b64encode(json.dumps(obj).encode()).decode()


# ── HTTP helpers ───────────────────────────────────────────────────────────────
class DiscordAPI:
    def __init__(self, token: str, build_number: int):
        self.token = token
        self.session = requests.Session()
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "discord/1.0.9175 Chrome/128.0.6613.186 "
            "Electron/32.2.7 Safari/537.36"
        )
        sp = make_super_properties(build_number)
        self.session.headers.update({
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.9",
            "User-Agent": ua,
            "X-Super-Properties": sp,
            "X-Discord-Locale": "en-US",
            "X-Discord-Timezone": "Asia/Ho_Chi_Minh",
            "Origin": "https://discord.com",
            "Referer": "https://discord.com/channels/@me",
        })

    def get(self, path: str, **kwargs) -> requests.Response:
        url = f"{API_BASE}{path}"
        log(f"GET {path}", "debug")
        r = self.session.get(url, **kwargs)
        log(f"  -> {r.status_code} ({len(r.content)} bytes)", "debug")
        return r

    def post(self, path: str, payload: Optional[dict] = None, **kwargs) -> requests.Response:
        url = f"{API_BASE}{path}"
        log(f"POST {path}", "debug")
        r = self.session.post(url, json=payload, **kwargs)
        log(f"  -> {r.status_code} ({len(r.content)} bytes)", "debug")
        return r

    def validate_token(self) -> bool:
        try:
            r = self.get("/users/@me")
            if r.status_code == 200:
                user = r.json()
                if user.get("bot"):
                    log(
                        "Đây là BOT token, không dùng cho quest. Quest cần USER token "
                        "(Developer Portal không cấp token này).",
                        "error",
                    )
                    return False
                name = user.get("username", "?")
                log(f"Đăng nhập: {Colors.BOLD}{name}{Colors.RESET} (ID: {user['id']})", "ok")
                return True
            else:
                log(f"Token không hợp lệ (status {r.status_code})", "error")
                if r.status_code == 401:
                    log("Token đã hết hạn hoặc bị đăng xuất. Lấy token mới từ trình duyệt.", "warn")
                return False
        except Exception as e:
            log(f"Không thể kết nối tới Discord: {e}", "error")
            return False


# ── Quest helpers (handles both camelCase & snake_case) ────────────────────────
def _get(d: Optional[dict], *keys):
    """Get value from dict trying multiple key names."""
    if d is None:
        return None
    for k in keys:
        if k in d:
            return d[k]
    return None


def get_task_config(quest: dict) -> Optional[dict]:
    cfg = quest.get("config", {})
    return _get(cfg, "taskConfig", "task_config", "taskConfigV2", "task_config_v2")


def get_quest_name(quest: dict) -> str:
    cfg = quest.get("config", {})
    msgs = cfg.get("messages", {})
    name = _get(msgs, "questName", "quest_name")
    if name:
        return name.strip()
    game = _get(msgs, "gameTitle", "game_title")
    if game:
        return game.strip()
    app_name = cfg.get("application", {}).get("name")
    if app_name:
        return app_name
    return f"Quest#{quest.get('id', '?')}"


def get_expires_at(quest: dict) -> Optional[str]:
    cfg = quest.get("config", {})
    return _get(cfg, "expiresAt", "expires_at")


def get_user_status(quest: dict) -> dict:
    us = _get(quest, "userStatus", "user_status")
    return us if isinstance(us, dict) else {}


def is_completable(quest: dict) -> bool:
    expires = get_expires_at(quest)
    if expires:
        try:
            exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp_dt <= datetime.now(timezone.utc):
                return False
        except Exception:
            pass

    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return False

    tasks = tc["tasks"]
    return any(tasks.get(t) is not None for t in SUPPORTED_TASKS)


def is_enrolled(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "enrolledAt", "enrolled_at"))


def is_completed(quest: dict) -> bool:
    us = get_user_status(quest)
    return bool(_get(us, "completedAt", "completed_at"))


def get_task_type(quest: dict) -> Optional[str]:
    tc = get_task_config(quest)
    if not tc or "tasks" not in tc:
        return None
    for t in SUPPORTED_TASKS:
        if tc["tasks"].get(t) is not None:
            return t
    return None


def get_seconds_needed(quest: dict) -> int:
    tc = get_task_config(quest)
    task_type = get_task_type(quest)
    if not tc or not task_type:
        return 0
    return tc["tasks"][task_type].get("target", 0)


def get_seconds_done(quest: dict) -> float:
    task_type = get_task_type(quest)
    if not task_type:
        return 0
    us = get_user_status(quest)
    progress = us.get("progress", {})
    if not progress:
        progress = {}
    return progress.get(task_type, {}).get("value", 0)


def get_enrolled_at(quest: dict) -> Optional[str]:
    us = get_user_status(quest)
    return _get(us, "enrolledAt", "enrolled_at")


# ── Core logic ─────────────────────────────────────────────────────────────────
class QuestAutocompleter:
    def __init__(self, api: DiscordAPI):
        self.api = api
        self.completed_ids: set = set()
        # Last fetch failure, so a caller can tell 'no quests' from 'auth broke'.
        self.last_fetch_error: Optional[str] = None

    # ── Fetch quests ───────────────────────────────────────────────────────────
    def fetch_quests(self) -> list:
        self.last_fetch_error = None
        try:
            r = self.api.get("/quests/@me")

            if r.status_code == 200:
                data = r.json()
                if isinstance(data, dict):
                    quests = data.get("quests", [])
                    excluded = data.get("excluded_quests", [])
                    blocked = _get(data, "quest_enrollment_blocked_until")
                    if blocked:
                        log(f"Enrollment blocked until: {blocked}", "warn")
                    if excluded:
                        log(f"{len(excluded)} quest(s) excluded", "debug")
                    return quests
                elif isinstance(data, list):
                    return data
                return []

            elif r.status_code == 429:
                retry_after = r.json().get("retry_after", 10)
                log(f"Rate limited – chờ {retry_after}s", "warn")
                wait_or_abort(retry_after)
                return self.fetch_quests()
            else:
                log(f"Quest fetch lỗi ({r.status_code}): {r.text[:200]}", "warn")
                self.last_fetch_error = (
                    f"HTTP {r.status_code}: {r.text[:160]}"
                )
                return []

        except Exception as e:
            log(f"Error fetching quests: {e}", "error")
            if DEBUG:
                traceback.print_exc()
            self.last_fetch_error = f"{type(e).__name__}: {e}"
            return []

    # ── Auto-accept ────────────────────────────────────────────────────────────
    def enroll_quest(self, quest: dict) -> bool:
        name = get_quest_name(quest)
        qid = quest["id"]

        for attempt in range(1, 4):
            try:
                r = self.api.post(f"/quests/{qid}/enroll", {
                    "location": 11,
                    "is_targeted": False,
                    "metadata_raw": None,
                    "metadata_sealed": None,
                    "traffic_metadata_raw": quest.get("traffic_metadata_raw"),
                    "traffic_metadata_sealed": quest.get("traffic_metadata_sealed"),
                })

                if r.status_code == 429:
                    retry_after = r.json().get("retry_after", 5)
                    wait = retry_after + 1
                    log(f"Rate limited nhận \"{name}\" (lần {attempt}/3) – chờ {wait}s", "warn")
                    wait_or_abort(wait)
                    continue

                if r.status_code in (200, 201, 204):
                    log(f"Đã nhận: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                    return True

                log(f"Enroll \"{name}\" thất bại ({r.status_code}): {r.text[:200]}", "warn")
                return False

            except Exception as e:
                log(f"Lỗi enroll \"{name}\": {e}", "error")
                return False

        log(f"Bỏ qua \"{name}\" sau 3 lần rate limited", "warn")
        return False

    def auto_accept(self, quests: list) -> list:
        if not AUTO_ACCEPT:
            return quests

        unaccepted = [
            q for q in quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]

        if not unaccepted:
            return quests

        log(f"Tìm thấy {len(unaccepted)} quest chưa nhận – đang auto-accept...", "info")

        for q in unaccepted:
            self.enroll_quest(q)
            wait_or_abort(3)

        wait_or_abort(2)
        return self.fetch_quests()

    # ── Complete: WATCH_VIDEO ──────────────────────────────────────────────────
    def complete_video(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)
        enrolled_at_str = get_enrolled_at(quest)

        if enrolled_at_str:
            enrolled_ts = datetime.fromisoformat(enrolled_at_str.replace("Z", "+00:00")).timestamp()
        else:
            enrolled_ts = time.time()

        log(f"🎬 Video: {Colors.BOLD}{name}{Colors.RESET} ({seconds_done:.0f}/{seconds_needed}s)", "info")

        max_future = 10
        speed = 7
        interval = 1

        while seconds_done < seconds_needed:
            max_allowed = (time.time() - enrolled_ts) + max_future
            diff = max_allowed - seconds_done
            timestamp = seconds_done + speed

            if diff >= speed:
                try:
                    r = self.api.post(f"/quests/{qid}/video-progress", {
                        "timestamp": min(seconds_needed, timestamp + random.random())
                    })
                    if r.status_code == 200:
                        body = r.json()
                        if body.get("completed_at"):
                            log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                            return
                        seconds_done = min(seconds_needed, timestamp)
                        log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")
                    elif r.status_code == 429:
                        retry_after = r.json().get("retry_after", 5)
                        log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                        wait_or_abort(retry_after + 1)
                        continue
                    else:
                        log(f"  Video progress lỗi ({r.status_code}): {r.text[:200]}", "warn")
                except Exception as e:
                    log(f"  Lỗi: {e}", "error")

            if timestamp >= seconds_needed:
                break
            wait_or_abort(interval)

        try:
            self.api.post(f"/quests/{qid}/video-progress", {"timestamp": seconds_needed})
        except Exception:
            pass
        log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Complete: PLAY_ON_DESKTOP / STREAM_ON_DESKTOP ──────────────────────────
    def complete_heartbeat(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        task_type = get_task_type(quest)
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        log(
            f"🎮 {task_type}: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60} phút còn lại)",
            "info"
        )

        pid = random.randint(1000, 30000)

        while seconds_done < seconds_needed:
            try:
                r = self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": f"call:0:{pid}",
                    "terminal": False,
                })

                if r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and task_type in progress_data:
                        seconds_done = progress_data[task_type].get("value", seconds_done)
                    log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")
                        return

                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                    wait_or_abort(retry_after + 1)
                    continue
                else:
                    log(f"  Heartbeat lỗi ({r.status_code}): {r.text[:200]}", "warn")

            except Exception as e:
                log(f"  Lỗi heartbeat: {e}", "error")

            wait_or_abort(HEARTBEAT_INTERVAL)

        try:
            self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": f"call:0:{pid}",
                "terminal": True,
            })
        except Exception:
            pass
        log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Complete: PLAY_ACTIVITY ────────────────────────────────────────────────
    def complete_activity(self, quest: dict):
        name = get_quest_name(quest)
        qid = quest["id"]
        seconds_needed = get_seconds_needed(quest)
        seconds_done = get_seconds_done(quest)

        remaining = max(0, seconds_needed - seconds_done)
        log(
            f"🕹️  Activity: {Colors.BOLD}{name}{Colors.RESET} "
            f"(~{remaining // 60} phút còn lại)",
            "info"
        )

        stream_key = "call:0:1"

        while seconds_done < seconds_needed:
            try:
                r = self.api.post(f"/quests/{qid}/heartbeat", {
                    "stream_key": stream_key,
                    "terminal": False,
                })

                if r.status_code == 200:
                    body = r.json()
                    progress_data = body.get("progress", {})
                    if progress_data and "PLAY_ACTIVITY" in progress_data:
                        seconds_done = progress_data["PLAY_ACTIVITY"].get("value", seconds_done)
                    log(f"  [{name}] {seconds_done:.0f}/{seconds_needed}s", "progress")

                    if body.get("completed_at") or seconds_done >= seconds_needed:
                        break
                elif r.status_code == 429:
                    retry_after = r.json().get("retry_after", 10)
                    log(f"  Rate limited – chờ {retry_after + 1}s", "warn")
                    wait_or_abort(retry_after + 1)
                    continue
                else:
                    log(f"  Heartbeat lỗi ({r.status_code}): {r.text[:200]}", "warn")
            except Exception as e:
                log(f"  Lỗi: {e}", "error")

            wait_or_abort(HEARTBEAT_INTERVAL)

        try:
            self.api.post(f"/quests/{qid}/heartbeat", {
                "stream_key": stream_key,
                "terminal": True,
            })
        except Exception:
            pass
        log(f"✅ Hoàn thành: {Colors.BOLD}{name}{Colors.RESET}", "ok")

    # ── Process a single quest ─────────────────────────────────────────────────
    def process_quest(self, quest: dict):
        qid = quest.get("id")
        name = get_quest_name(quest)
        task_type = get_task_type(quest)

        if not task_type:
            log(f"\"{name}\" – task không hỗ trợ, bỏ qua", "warn")
            return

        if qid in self.completed_ids:
            return

        log(f"━━━ Bắt đầu: {Colors.BOLD}{name}{Colors.RESET} (task: {task_type}) ━━━", "info")

        if task_type in ("WATCH_VIDEO", "WATCH_VIDEO_ON_MOBILE"):
            self.complete_video(quest)
        elif task_type in ("PLAY_ON_DESKTOP", "STREAM_ON_DESKTOP"):
            self.complete_heartbeat(quest)
        elif task_type == "PLAY_ACTIVITY":
            self.complete_activity(quest)

        self.completed_ids.add(qid)

    # ── One scan cycle ─────────────────────────────────────────────────────────
    def scan_once(self, dry_run: bool = False) -> dict:
        """Public entry: a shutdown mid-cycle returns a summary, never a crash."""
        try:
            return self._run_cycle(dry_run)
        except ShutdownRequested:
            log("Chu kỳ dừng giữa đường vì tiến trình đang tắt.", "warn")
            return {
                "total": 0,
                "enrolled": 0,
                "completed": 0,
                "completable": 0,
                "items": [],
                "pending_accept": [],
                "pending_complete": [],
                "completed_now": [],
                "dry_run": dry_run,
                "error": "shutdown",
            }

    def _run_cycle(self, dry_run: bool = False) -> dict:
        """Scan, report, and (unless dry_run) accept + complete.

        Returns a plain-data summary so a caller with no knowledge of the quest
        schema — the Discord bot — can render it without importing helpers.
        Blocking: run it in a worker thread when called from async code.
        """
        quests = self.fetch_quests()
        summary = {
            "total": len(quests),
            "enrolled": 0,
            "completed": 0,
            "completable": 0,
            "items": [],
            "pending_accept": [],
            "pending_complete": [],
            "completed_now": [],
            "dry_run": dry_run,
            "error": self.last_fetch_error,
        }

        if not quests:
            log("Không có quest nào", "info")
            return summary

        summary["enrolled"] = sum(1 for q in quests if is_enrolled(q))
        summary["completed"] = sum(1 for q in quests if is_completed(q))
        summary["completable"] = sum(1 for q in quests if is_completable(q))

        log(
            f"Tổng: {summary['total']} quest | Enrolled: {summary['enrolled']} | "
            f"Completed: {summary['completed']} | Completable: {summary['completable']}",
            "info"
        )

        for q in quests:
            name = get_quest_name(q)
            task = get_task_type(q) or "?"
            if is_completed(q):
                status = "completed"
                marker = f"{Colors.GREEN}✓{Colors.RESET}"
            elif is_enrolled(q):
                status = "enrolled"
                marker = f"{Colors.YELLOW}▶{Colors.RESET}"
            else:
                status = "new"
                marker = f"{Colors.DIM}○{Colors.RESET}"
            log(f"  {marker} {name} [{task}]", "info")
            summary["items"].append({
                "id": q.get("id"),
                "name": name,
                "task": task,
                "status": status,
                "done": int(get_seconds_done(q)),
                "need": int(get_seconds_needed(q)),
                "completable": is_completable(q),
            })

        pending_accept = [
            q for q in quests
            if not is_enrolled(q) and not is_completed(q) and is_completable(q)
        ]
        pending_complete = [
            q for q in quests
            if is_enrolled(q) and not is_completed(q) and is_completable(q)
            and q.get("id") not in self.completed_ids
        ]

        summary["pending_accept"] = [
            {"name": get_quest_name(q), "task": get_task_type(q)} for q in pending_accept
        ]
        summary["pending_complete"] = [
            {
                "name": get_quest_name(q),
                "task": get_task_type(q),
                "done": int(get_seconds_done(q)),
                "need": int(get_seconds_needed(q)),
            }
            for q in pending_complete
        ]

        if dry_run:
            if pending_accept:
                log(f"\nDRY RUN: sẽ nhận {len(pending_accept)} quest:", "warn")
                for q in pending_accept:
                    log(f"  + {get_quest_name(q)} [{get_task_type(q)}]", "progress")
            if pending_complete:
                log(f"\nDRY RUN: sẽ hoàn thành {len(pending_complete)} quest:", "warn")
                for q in pending_complete:
                    log(
                        f"  ~ {get_quest_name(q)} "
                        f"({get_seconds_done(q):.0f}/{get_seconds_needed(q)}s)",
                        "progress",
                    )
            if not pending_accept and not pending_complete:
                log("DRY RUN: không có gì để làm", "info")
            return summary

        # Auto-accept, then complete whatever is enrolled and unfinished.
        quests = self.auto_accept(quests)
        actionable = [
            q for q in quests
            if is_enrolled(q) and not is_completed(q) and is_completable(q)
            and q.get("id") not in self.completed_ids
        ]

        if actionable:
            log(f"\n{len(actionable)} quest(s) cần hoàn thành:", "info")
            for q in actionable:
                self.process_quest(q)
                summary["completed_now"].append(get_quest_name(q))
        else:
            log("Không có quest nào cần hoàn thành lúc này", "info")

        return summary

    # ── Main loop ──────────────────────────────────────────────────────────────
    def run(self, once: bool = False, dry_run: bool = False):
        log("=" * 60, "info")
        log(f"{Colors.BOLD}Discord Quest Auto-Completer v3.1{Colors.RESET}", "info")
        log(f"Auto-accept: {'BẬT' if AUTO_ACCEPT else 'TẮT'}  |  Poll: {POLL_INTERVAL}s", "info")
        if dry_run:
            log(f"{Colors.YELLOW}DRY RUN{Colors.RESET} – chỉ quét và in kế hoạch, không nhận/không hoàn thành", "warn")
        if once:
            log("Chế độ một lần – quét xong thoát", "info")
        log("=" * 60, "info")

        cycle = 0
        while True:
            cycle += 1
            log(f"── Quét lần #{cycle} ──", "info")
            self.scan_once(dry_run=dry_run)

            if once:
                break

            log(f"\nChờ {POLL_INTERVAL}s... (Ctrl+C để dừng)\n", "info")
            time.sleep(POLL_INTERVAL)


# ── Entry point ────────────────────────────────────────────────────────────────
def parse_args(argv):
    """Tiny flag parser: POSIX-style flags only, no external dependency."""
    opts = {"token": None, "once": False, "dry_run": False, "interval": None}
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg in ("-h", "--help"):
            print(__doc__)
            print(
                "Usage: python main.py [TOKEN] [--once] [--dry-run] [--interval N]\n"
                "\n"
                "  --once         quét một lần rồi thoát\n"
                "  --dry-run      chỉ quét và in kế hoạch, không nhận/không hoàn thành\n"
                "  --interval N   giây giữa các lần quét (mặc định 60)\n"
                "\n"
                "Token lấy theo thứ tự: tham số dòng lệnh -> QUEST_TOKEN -> USER_TOKEN\n"
                "-> DISCORD_TOKEN -> file .token -> nhập tay.\n"
                "Quest cần USER token, KHÔNG phải bot token."
            )
            sys.exit(0)
        elif arg == "--once":
            opts["once"] = True
        elif arg == "--dry-run":
            opts["dry_run"] = True
        elif arg == "--interval":
            if i + 1 >= len(argv):
                log("--interval cần một số giây.", "error")
                sys.exit(2)
            try:
                opts["interval"] = int(argv[i + 1])
            except ValueError:
                log("--interval phải là số nguyên.", "error")
                sys.exit(2)
            i += 1
        elif arg.startswith("-"):
            log(f"Cờ không hợp lệ: {arg}", "error")
            sys.exit(2)
        else:
            opts["token"] = arg.strip()
        i += 1
    return opts


def main():
    global POLL_INTERVAL

    print(f"""
{Colors.BOLD}{Colors.CYAN}╔══════════════════════════════════════════════╗
║     Discord Quest Auto-Completer.            ║
║  Auto quét · Auto nhận · Auto hoàn thành     ║
╚══════════════════════════════════════════════╝{Colors.RESET}
""")

    opts = parse_args(sys.argv)
    if opts["interval"]:
        POLL_INTERVAL = opts["interval"]

    base_dir = os.path.dirname(os.path.abspath(__file__))

    # Quest endpoints need a USER token. Keep it in its own variable so a bot
    # token sitting in DISCORD_TOKEN (the bot's env) never gets used by mistake.
    token = opts["token"]
    if not token:
        for env_key in ("QUEST_TOKEN", "USER_TOKEN", "DISCORD_TOKEN"):
            value = os.environ.get(env_key, "").strip()
            if value:
                token = value
                log(f"Đọc token từ Environment ({env_key}).", "info")
                break
    if not token and os.path.exists(os.path.join(base_dir, ".token")):
        with open(os.path.join(base_dir, ".token"), "r", encoding="utf-8") as f:
            token = f.read().strip()
        log("Đọc token từ file .token", "info")
    if not token and sys.stdin.isatty():
        token = input(f"{Colors.BOLD}Nhập Discord Token: {Colors.RESET}").strip()

    if not token:
        log("Thiếu token. Đặt QUEST_TOKEN trong .env hoặc truyền tham số.", "error")
        sys.exit(1)

    build_number = fetch_latest_build_number()
    api = DiscordAPI(token, build_number)

    if not api.validate_token():
        sys.exit(1)

    completer = QuestAutocompleter(api)

    try:
        completer.run(once=opts["once"], dry_run=opts["dry_run"])
    except KeyboardInterrupt:
        print()
        log("Đã dừng.", "info")
        sys.exit(0)


if __name__ == "__main__":
    main()
