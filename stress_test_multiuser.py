"""
Мульти-юзер параллельный стресс-тест MCP сервера Vaiz.
Воркеры распределяются по юзерам (каждые N воркеров — свой юзер).
Цель: проверить, лимит сессий per-user или глобальный.

Использование:
    python stress_test_multiuser.py                  # 50 воркеров, 2 сессии, 10 на юзера
    python stress_test_multiuser.py 30               # 30 воркеров
    python stress_test_multiuser.py 50 3             # 50 воркеров, 3 сессии
    python stress_test_multiuser.py 50 2 5           # 50 воркеров, 2 сессии, 5 на юзера
"""

import asyncio
import json
import random
import time
import sys
import threading
from datetime import datetime

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

# ─── Конфиг ──────────────────────────────────────────────────────────────────

SERVER_COMMAND = "node"
SERVER_ARGS = ["/Users/mbrtn/Projects/vaiz-mcp/dist/cli.js"]

USERS = [
    {
        "name": "User-1",
        "space_id": "698ef59fa6bf98dce521753e",
        "token": "pat_5fbfca393a68f70cfc9ad4c0344b39cfcdc2dac32b46b0f200124c63a91214b9",
    },
    {
        "name": "User-2",
        "space_id": "6992f9d0a735b5f763fd2a1e",
        "token": "pat_3b044df931a8d802b73d970bb95f1e3fb0e92400a024d48c314648ce5a70e55f",
    },
    {
        "name": "User-3",
        "space_id": "6992fa0b416ff4f0775275dd",
        "token": "pat_7294780f11b5e4ad3b521af7a1519dcdc977777508d12118e47d53f22cfea62f",
    },
    {
        "name": "User-4",
        "space_id": "6992fa4d416ff4f077527b88",
        "token": "pat_bce856cb2665aaf74ac73e70182dc2b459d3eef96ed76fbeb1528988fdd0f416",
    },
    {
        "name": "User-5",
        "space_id": "6992fa6e416ff4f077528132",
        "token": "pat_6fd0c6ab275f64b5fbb7aafbbd8ac904ee3e6a7990f62241dac1e060cee69927",
    },
]

API_URL = "https://api.vaiz.local:10000/mcp"

SEARCH_QUERIES = ["баг", "фича", "тест", "дизайн", "api", "deploy", "auth", "UI"]
SEARCH_ENTITY_TYPES = ["task", "project", "user", "comment", "board", "document"]

# Параллельность
NUM_WORKERS = 50
SESSIONS_PER_WORKER = 2
WORKERS_PER_USER = 10
CALLS_PER_SESSION = (3, 8)
CALL_DELAY = (0.1, 1.0)
SESSION_DELAY = (0.5, 2.0)

# Retry подключения (поверх proxy retry — перезапуск subprocess)
SESSION_CONNECT_RETRIES = 3
SESSION_RETRY_BASE_DELAY = 5.0
SESSION_RETRY_JITTER = 2.0

# ─── Цвета ───────────────────────────────────────────────────────────────────

USER_COLORS = [
    "\033[92m",  # зеленый
    "\033[93m",  # желтый
    "\033[94m",  # синий
    "\033[95m",  # малиновый
    "\033[96m",  # циан
]

class C:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    RED = "\033[91m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    CYAN = "\033[96m"
    DIM = "\033[2m"


_log_lock = threading.Lock()

def log(worker_id: int, user_idx: int, msg: str, color: str = ""):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    uc = USER_COLORS[user_idx % len(USER_COLORS)]
    prefix = f"{C.DIM}[{ts}]{C.RESET} {uc}[U{user_idx}:W{worker_id}]{C.RESET}"
    line = f"{prefix} {color}{msg}{C.RESET}"
    with _log_lock:
        print(line, flush=True)


# ─── Рандомные вызовы ────────────────────────────────────────────────────────

def random_tool_calls() -> list[tuple[str, dict]]:
    """Возвращает список универсальных вызовов (без хардкод-ID, работают в любом спейсе)."""
    calls = [
        ("ping", {}),
        ("current_user", {}),
        ("list_spaces", {}),
        ("space_info", {}),
        ("list_projects", {}),
        ("list_members", {}),
        ("list_milestones", {}),
        ("get_notifications", {}),
        ("list_resources", {}),

        ("search", {"query": random.choice(SEARCH_QUERIES), "entityType": random.choice(SEARCH_ENTITY_TYPES), "limit": 5}),
        ("search", {"query": random.choice(SEARCH_QUERIES), "limit": 3}),

        ("get_notifications", {"limit": 5, "readStatus": "Unread"}),
        ("get_notifications", {"limit": 10, "groups": ["TaskChanges"]}),

        ("get_tasks", {"limit": 5}),
        ("get_tasks", {"completed": False, "limit": 3}),
    ]
    return calls


# ─── Статистика ──────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.total_calls = 0
        self.successful = 0
        self.errors = 0
        self.tool_stats: dict[str, list[float]] = {}
        self.sessions = 0
        self.session_errors = 0
        self.session_retries = 0
        self.worker_calls: dict[int, int] = {}
        self.user_stats: dict[int, dict] = {}  # user_idx -> {calls, ok, err}
        self.start_time = time.time()

    def record_retry(self):
        with self._lock:
            self.session_retries += 1

    def record_call(self, worker_id: int, user_idx: int, tool: str, duration: float, success: bool):
        with self._lock:
            self.total_calls += 1
            if success:
                self.successful += 1
            else:
                self.errors += 1
            self.tool_stats.setdefault(tool, []).append(duration)
            self.worker_calls[worker_id] = self.worker_calls.get(worker_id, 0) + 1

            us = self.user_stats.setdefault(user_idx, {"calls": 0, "ok": 0, "err": 0})
            us["calls"] += 1
            if success:
                us["ok"] += 1
            else:
                us["err"] += 1

    def record_session(self, error: bool = False):
        with self._lock:
            self.sessions += 1
            if error:
                self.session_errors += 1

    def print_summary(self, users_used: list[dict], workers_per_user: int):
        elapsed = time.time() - self.start_time

        print(f"\n{'='*78}")
        print(f"{C.BOLD}{C.CYAN}  ИТОГИ МУЛЬТИ-ЮЗЕР СТРЕСС-ТЕСТА{C.RESET}")
        print(f"{'='*78}")
        print(f"  Время выполнения:     {elapsed:.1f}с")
        print(f"  Юзеров:               {len(users_used)}")
        print(f"  Воркеров на юзера:    {workers_per_user}")
        print(f"  Всего воркеров:       {len(self.worker_calls)}")
        ok_sessions = self.sessions - self.session_errors
        sess_err_color = C.RED if self.session_errors > 0 else C.GREEN
        call_err_color = C.RED if self.errors > 0 else C.GREEN
        print(f"  Сессий всего:         {self.sessions}")
        print(f"  {C.GREEN}Сессий успешных:      {ok_sessions}{C.RESET}")
        print(f"  {sess_err_color}Сессий с ошибкой:     {self.session_errors}{C.RESET}")
        if self.session_retries > 0:
            print(f"  {C.YELLOW}Retry подключений:    {self.session_retries}{C.RESET}")
        if self.sessions > 0:
            print(f"  Успешность сессий:    {ok_sessions/self.sessions*100:.1f}%")
        print()
        print(f"  Вызовов всего:        {self.total_calls}")
        print(f"  {C.GREEN}Вызовов успешных:     {self.successful}{C.RESET}")
        print(f"  {call_err_color}Вызовов с ошибкой:    {self.errors}{C.RESET}")
        if self.total_calls > 0:
            print(f"  Успешность вызовов:   {self.successful/self.total_calls*100:.1f}%")

        if self.total_calls > 0:
            all_times = [d for times in self.tool_stats.values() for d in times]
            avg = sum(all_times) / len(all_times)
            sorted_times = sorted(all_times)
            p50 = sorted_times[len(sorted_times) // 2]
            p95 = sorted_times[int(len(sorted_times) * 0.95)]
            p99 = sorted_times[int(len(sorted_times) * 0.99)]
            rps = self.total_calls / elapsed

            print(f"  RPS (запросов/с):     {rps:.1f}")
            print(f"  Среднее время:        {avg*1000:.0f}мс")
            print(f"  p50:                  {p50*1000:.0f}мс")
            print(f"  p95:                  {p95*1000:.0f}мс")
            print(f"  p99:                  {p99*1000:.0f}мс")

        # По юзерам
        print(f"\n  {'Юзер':<25} {'Воркеров':>10} {'Вызовов':>10} {'OK':>8} {'Ошибок':>8} {'Ошибок%':>8}")
        print(f"  {'-'*25} {'-'*10} {'-'*10} {'-'*8} {'-'*8} {'-'*8}")
        for uid in sorted(self.user_stats):
            us = self.user_stats[uid]
            uc = USER_COLORS[uid % len(USER_COLORS)]
            uname = users_used[uid]["name"] if uid < len(users_used) else f"User-{uid}"
            err_pct = (us["err"] / us["calls"] * 100) if us["calls"] > 0 else 0
            err_color = C.RED if err_pct > 0 else C.GREEN
            print(f"  {uc}{uname:<25}{C.RESET} {workers_per_user:>10} {us['calls']:>10} {us['ok']:>8} {err_color}{us['err']:>8}{C.RESET} {err_color}{err_pct:>7.1f}%{C.RESET}")

        # По инструментам
        print(f"\n  {'Инструмент':<30} {'Вызовов':>8} {'Ср.время':>10} {'p95':>10} {'Макс':>10}")
        print(f"  {'-'*30} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
        for tool, times in sorted(self.tool_stats.items(), key=lambda x: len(x[1]), reverse=True):
            avg_t = sum(times) / len(times)
            max_t = max(times)
            sorted_t = sorted(times)
            p95_t = sorted_t[int(len(sorted_t) * 0.95)] if len(sorted_t) > 1 else max_t
            print(f"  {tool:<30} {len(times):>8} {avg_t*1000:>8.0f}мс {p95_t*1000:>8.0f}мс {max_t*1000:>8.0f}мс")
        print(f"{'='*78}\n")


# ─── Логика воркера ──────────────────────────────────────────────────────────

def make_env(token: str, space_id: str) -> dict:
    return {
        "VAIZ_API_TOKEN": token,
        "VAIZ_SPACE_ID": space_id,
        "VAIZ_API_URL": API_URL,
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "PATH": "/usr/local/bin:/usr/bin:/bin",
    }


async def _do_session(worker_id: int, user_idx: int, token: str, space_id: str, session_num: int, stats: Stats):
    """Одна попытка подключения и работы в сессии. Бросает исключение при ошибке."""
    server_params = StdioServerParameters(
        command=SERVER_COMMAND,
        args=SERVER_ARGS,
        env=make_env(token, space_id),
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            stats.record_session(error=False)
            log(worker_id, user_idx, f"Сессия #{session_num} — подключено!", C.GREEN)

            num_calls = random.randint(*CALLS_PER_SESSION)

            for i in range(num_calls):
                tool_name, tool_args = random.choice(random_tool_calls())

                t0 = time.time()
                try:
                    result = await session.call_tool(tool_name, tool_args)
                    duration = time.time() - t0

                    is_error = getattr(result, "is_error", False) or getattr(result, "isError", False)
                    if is_error:
                        stats.record_call(worker_id, user_idx, tool_name, duration, success=False)
                        preview = str(result.content[0].text)[:80] if result.content else "?"
                        log(worker_id, user_idx, f"  [{i+1}/{num_calls}] {tool_name} ⚠ {duration*1000:.0f}мс — {preview}", C.RED)
                    else:
                        stats.record_call(worker_id, user_idx, tool_name, duration, success=True)
                        log(worker_id, user_idx, f"  [{i+1}/{num_calls}] {tool_name} ✓ {duration*1000:.0f}мс", C.GREEN)

                except Exception as e:
                    duration = time.time() - t0
                    stats.record_call(worker_id, user_idx, tool_name, duration, success=False)
                    log(worker_id, user_idx, f"  [{i+1}/{num_calls}] {tool_name} ✗ {duration*1000:.0f}мс — {e}", C.RED)

                delay = random.uniform(*CALL_DELAY)
                await asyncio.sleep(delay)

    log(worker_id, user_idx, f"Сессия #{session_num} — отключено.", C.DIM)


async def run_session(worker_id: int, user_idx: int, token: str, space_id: str, session_num: int, stats: Stats):
    log(worker_id, user_idx, f"Сессия #{session_num} — подключение...", C.BOLD)

    for attempt in range(1, SESSION_CONNECT_RETRIES + 1):
        try:
            await _do_session(worker_id, user_idx, token, space_id, session_num, stats)
            return
        except Exception as e:
            if attempt < SESSION_CONNECT_RETRIES:
                delay = SESSION_RETRY_BASE_DELAY + random.uniform(0, SESSION_RETRY_JITTER)
                stats.record_retry()
                log(worker_id, user_idx,
                    f"Сессия #{session_num} retry {attempt}/{SESSION_CONNECT_RETRIES} через {delay:.1f}с — {e}",
                    C.YELLOW)
                await asyncio.sleep(delay)
            else:
                stats.record_session(error=True)
                log(worker_id, user_idx,
                    f"Сессия #{session_num} ОШИБКА (после {SESSION_CONNECT_RETRIES} попыток): {e}",
                    C.RED + C.BOLD)


async def worker(worker_id: int, user_idx: int, token: str, space_id: str, num_sessions: int, stats: Stats):
    for s in range(1, num_sessions + 1):
        await run_session(worker_id, user_idx, token, space_id, s, stats)

        if s < num_sessions:
            pause = random.uniform(*SESSION_DELAY)
            await asyncio.sleep(pause)

    log(worker_id, user_idx, f"Воркер завершен ({num_sessions} сессий).", C.BOLD)


# ─── Точка входа ─────────────────────────────────────────────────────────────

async def main():
    num_workers = NUM_WORKERS
    num_sessions = SESSIONS_PER_WORKER
    workers_per_user = WORKERS_PER_USER

    if len(sys.argv) > 1:
        num_workers = int(sys.argv[1])
    if len(sys.argv) > 2:
        num_sessions = int(sys.argv[2])
    if len(sys.argv) > 3:
        workers_per_user = int(sys.argv[3])

    num_users_needed = (num_workers + workers_per_user - 1) // workers_per_user
    if num_users_needed > len(USERS):
        print(f"{C.RED}Нужно {num_users_needed} юзеров, но доступно только {len(USERS)}.{C.RESET}")
        print(f"{C.RED}Уменьши воркеров или увеличь workers_per_user.{C.RESET}")
        return

    users_used = USERS[:num_users_needed]
    total_sessions = num_workers * num_sessions
    total_calls_est = total_sessions * sum(CALLS_PER_SESSION) // 2

    print(f"\n{C.BOLD}{C.MAGENTA}{'='*78}{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}  🔥 МУЛЬТИ-ЮЗЕР СТРЕСС-ТЕСТ MCP СЕРВЕРА{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}{'='*78}{C.RESET}")
    print(f"  Воркеров:             {num_workers}")
    print(f"  Юзеров:               {num_users_needed}")
    print(f"  Воркеров на юзера:    {workers_per_user}")
    print(f"  Сессий на воркер:     {num_sessions}")
    print(f"  Всего сессий:         {total_sessions}")
    print(f"  Ожид. вызовов:        ~{total_calls_est}")
    print()

    for i, u in enumerate(users_used):
        uc = USER_COLORS[i % len(USER_COLORS)]
        w_start = i * workers_per_user
        w_end = min(w_start + workers_per_user, num_workers)
        print(f"  {uc}U{i}: {u['name']:<10} space={u['space_id'][:8]}… воркеры W{w_start}-W{w_end-1}{C.RESET}")
    print()

    stats = Stats()

    tasks = []
    for wid in range(num_workers):
        user_idx = wid // workers_per_user
        user = users_used[user_idx]
        tasks.append(asyncio.create_task(worker(wid, user_idx, user["token"], user["space_id"], num_sessions, stats)))

    await asyncio.gather(*tasks)
    stats.print_summary(users_used, workers_per_user)


if __name__ == "__main__":
    asyncio.run(main())
