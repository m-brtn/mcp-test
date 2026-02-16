"""
Параллельный стресс-тест MCP сервера Vaiz.
Несколько воркеров одновременно подключаются, рандомно вызывают инструменты,
отключаются и переподключаются. Каждый воркер — независимый клиент.

Использование:
    python stress_test_parallel.py                     # 3 воркера, 3 сессии каждый
    python stress_test_parallel.py 5                   # 5 воркеров, 3 сессии
    python stress_test_parallel.py 5 10                # 5 воркеров, 10 сессий
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
SERVER_ENV = {
    "VAIZ_API_TOKEN": "pat_5fbfca393a68f70cfc9ad4c0344b39cfcdc2dac32b46b0f200124c63a91214b9",
    "VAIZ_SPACE_ID": "698ef59fa6bf98dce521753e",
    "VAIZ_API_URL": "https://api.vaiz.local:10000/mcp",
    "NODE_TLS_REJECT_UNAUTHORIZED": "0",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}

# Реальные ID из воркспейса
PROJECT_IDS = [
    "698ef5a0a6bf98dce521754e",
    "698efb1dd263712d7f2b3247",
    "698efb1ed263712d7f2b3365",
]
BOARD_IDS = [
    "698ef5a0a6bf98dce521755a",
    "698efb1ed263712d7f2b324c",
    "698efb1ed263712d7f2b336f",
]
MEMBER_IDS = [
    "698ef59fa6bf98dce5217540",
    "698efb1fd263712d7f2b34d8",
    "698efb21d263712d7f2b3568",
    "698efb22d263712d7f2b358b",
    "698efb23d263712d7f2b35ae",
    "698efb25d263712d7f2b35d1",
    "698efb26d263712d7f2b35f4",
]
SPACE_ID = "698ef59fa6bf98dce521753e"

SEARCH_QUERIES = ["баг", "фича", "тест", "дизайн", "api", "deploy", "auth", "UI"]
SEARCH_ENTITY_TYPES = ["task", "project", "user", "comment", "board", "document"]

# Параллельность
NUM_WORKERS = 3
SESSIONS_PER_WORKER = 3
CALLS_PER_SESSION = (3, 8)
CALL_DELAY = (0.1, 1.0)
SESSION_DELAY = (0.5, 2.0)

# ─── Цвета ───────────────────────────────────────────────────────────────────

WORKER_COLORS = [
    "\033[92m",   # зеленый
    "\033[93m",   # желтый
    "\033[94m",   # синий
    "\033[95m",   # малиновый
    "\033[96m",   # циан
    "\033[91m",   # красный
    "\033[97m",   # белый
    "\033[33m",   # темно-желтый
    "\033[34m",   # темно-синий
    "\033[35m",   # темно-малиновый
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

def log(worker_id: int, msg: str, color: str = ""):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    wc = WORKER_COLORS[worker_id % len(WORKER_COLORS)]
    prefix = f"{C.DIM}[{ts}]{C.RESET} {wc}[W{worker_id}]{C.RESET}"
    line = f"{prefix} {color}{msg}{C.RESET}"
    with _log_lock:
        print(line, flush=True)


# ─── Рандомные вызовы ────────────────────────────────────────────────────────

def random_tool_calls() -> list[tuple[str, dict]]:
    calls = [
        ("ping", {}),
        ("current_user", {}),
        ("list_spaces", {}),
        ("space_info", {}),
        ("list_projects", {}),
        ("list_members", {}),
        ("list_milestones", {}),
        ("list_boards", {}),
        ("get_notifications", {}),
        ("list_resources", {}),

        ("get_project", {"projectId": random.choice(PROJECT_IDS)}),
        ("list_boards", {"projectId": random.choice(PROJECT_IDS)}),
        ("list_milestones", {"projectId": random.choice(PROJECT_IDS)}),
        ("get_project_history", {"projectId": random.choice(PROJECT_IDS), "limit": 5}),

        ("get_board", {"boardId": random.choice(BOARD_IDS)}),
        ("list_milestones", {"boardId": random.choice(BOARD_IDS)}),
        ("get_automations", {"boardId": random.choice(BOARD_IDS)}),
        ("get_tasks", {"boardId": random.choice(BOARD_IDS), "limit": 5}),

        ("get_member", {"memberId": random.choice(MEMBER_IDS)}),
        ("get_user_history", {"memberId": random.choice(MEMBER_IDS), "limit": 5}),

        ("search", {"query": random.choice(SEARCH_QUERIES), "entityType": random.choice(SEARCH_ENTITY_TYPES), "limit": 5}),
        ("search", {"query": random.choice(SEARCH_QUERIES), "limit": 3}),

        ("select_space", {"spaceId": SPACE_ID}),

        ("get_notifications", {"limit": 5, "readStatus": "Unread"}),
        ("get_notifications", {"limit": 10, "groups": ["TaskChanges"]}),
    ]
    return calls


# ─── Потокобезопасная статистика ─────────────────────────────────────────────

class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.total_calls = 0
        self.successful = 0
        self.errors = 0
        self.tool_stats: dict[str, list[float]] = {}
        self.sessions = 0
        self.session_errors = 0
        self.worker_calls: dict[int, int] = {}
        self.start_time = time.time()

    def record_call(self, worker_id: int, tool: str, duration: float, success: bool):
        with self._lock:
            self.total_calls += 1
            if success:
                self.successful += 1
            else:
                self.errors += 1
            self.tool_stats.setdefault(tool, []).append(duration)
            self.worker_calls[worker_id] = self.worker_calls.get(worker_id, 0) + 1

    def record_session(self, error: bool = False):
        with self._lock:
            self.sessions += 1
            if error:
                self.session_errors += 1

    def print_summary(self):
        elapsed = time.time() - self.start_time

        print(f"\n{'='*74}")
        print(f"{C.BOLD}{C.CYAN}  ИТОГИ ПАРАЛЛЕЛЬНОГО СТРЕСС-ТЕСТА{C.RESET}")
        print(f"{'='*74}")
        print(f"  Время выполнения:     {elapsed:.1f}с")
        print(f"  Воркеров:             {len(self.worker_calls)}")
        print(f"  Сессий:               {self.sessions} (ошибок подключения: {self.session_errors})")
        print(f"  Всего вызовов:        {self.total_calls}")
        print(f"  {C.GREEN}Успешных:             {self.successful}{C.RESET}")
        print(f"  {C.RED}С ошибками:           {self.errors}{C.RESET}")

        if self.total_calls > 0:
            all_times = [d for times in self.tool_stats.values() for d in times]
            avg = sum(all_times) / len(all_times)
            p50 = sorted(all_times)[len(all_times) // 2]
            p95 = sorted(all_times)[int(len(all_times) * 0.95)]
            p99 = sorted(all_times)[int(len(all_times) * 0.99)]
            rps = self.total_calls / elapsed

            print(f"  RPS (запросов/с):     {rps:.1f}")
            print(f"  Среднее время:        {avg*1000:.0f}мс")
            print(f"  p50:                  {p50*1000:.0f}мс")
            print(f"  p95:                  {p95*1000:.0f}мс")
            print(f"  p99:                  {p99*1000:.0f}мс")

        # По воркерам
        print(f"\n  {'Воркер':<10} {'Вызовов':>8}")
        print(f"  {'-'*10} {'-'*8}")
        for wid in sorted(self.worker_calls):
            wc = WORKER_COLORS[wid % len(WORKER_COLORS)]
            print(f"  {wc}W{wid:<9}{C.RESET} {self.worker_calls[wid]:>8}")

        # По инструментам
        print(f"\n  {'Инструмент':<30} {'Вызовов':>8} {'Ср.время':>10} {'p95':>10} {'Макс':>10}")
        print(f"  {'-'*30} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
        for tool, times in sorted(self.tool_stats.items(), key=lambda x: len(x[1]), reverse=True):
            avg_t = sum(times) / len(times)
            max_t = max(times)
            sorted_t = sorted(times)
            p95_t = sorted_t[int(len(sorted_t) * 0.95)] if len(sorted_t) > 1 else max_t
            print(f"  {tool:<30} {len(times):>8} {avg_t*1000:>8.0f}мс {p95_t*1000:>8.0f}мс {max_t*1000:>8.0f}мс")
        print(f"{'='*74}\n")


# ─── Логика воркера ──────────────────────────────────────────────────────────

async def run_session(worker_id: int, session_num: int, stats: Stats):
    """Одна сессия одного воркера."""

    log(worker_id, f"Сессия #{session_num} — подключение...", C.BOLD)
    stats.record_session()

    server_params = StdioServerParameters(
        command=SERVER_COMMAND,
        args=SERVER_ARGS,
        env=SERVER_ENV,
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log(worker_id, f"Сессия #{session_num} — подключено!", C.GREEN)

                num_calls = random.randint(*CALLS_PER_SESSION)

                for i in range(num_calls):
                    tool_name, tool_args = random.choice(random_tool_calls())

                    args_short = json.dumps(tool_args, ensure_ascii=False) if tool_args else "()"
                    if len(args_short) > 60:
                        args_short = args_short[:57] + "..."

                    t0 = time.time()
                    try:
                        result = await session.call_tool(tool_name, tool_args)
                        duration = time.time() - t0

                        is_error = getattr(result, "is_error", False) or getattr(result, "isError", False)
                        if is_error:
                            stats.record_call(worker_id, tool_name, duration, success=False)
                            preview = str(result.content[0].text)[:80] if result.content else "?"
                            log(worker_id, f"  [{i+1}/{num_calls}] {tool_name} ⚠ {duration*1000:.0f}мс — {preview}", C.RED)
                        else:
                            stats.record_call(worker_id, tool_name, duration, success=True)
                            preview = ""
                            if result.content:
                                preview = result.content[0].text[:80].replace("\n", " ")
                            log(worker_id, f"  [{i+1}/{num_calls}] {tool_name} ✓ {duration*1000:.0f}мс", C.GREEN)

                    except Exception as e:
                        duration = time.time() - t0
                        stats.record_call(worker_id, tool_name, duration, success=False)
                        log(worker_id, f"  [{i+1}/{num_calls}] {tool_name} ✗ {duration*1000:.0f}мс — {e}", C.RED)

                    delay = random.uniform(*CALL_DELAY)
                    await asyncio.sleep(delay)

        log(worker_id, f"Сессия #{session_num} — отключено.", C.DIM)

    except Exception as e:
        stats.record_session(error=True)
        log(worker_id, f"Сессия #{session_num} ОШИБКА: {e}", C.RED + C.BOLD)


async def worker(worker_id: int, num_sessions: int, stats: Stats):
    """Один воркер: последовательно открывает N сессий."""
    for s in range(1, num_sessions + 1):
        await run_session(worker_id, s, stats)

        if s < num_sessions:
            pause = random.uniform(*SESSION_DELAY)
            await asyncio.sleep(pause)

    log(worker_id, f"Воркер завершен ({num_sessions} сессий).", C.BOLD)


# ─── Точка входа ─────────────────────────────────────────────────────────────

async def main():
    num_workers = NUM_WORKERS
    num_sessions = SESSIONS_PER_WORKER

    if len(sys.argv) > 1:
        num_workers = int(sys.argv[1])
    if len(sys.argv) > 2:
        num_sessions = int(sys.argv[2])

    total_sessions = num_workers * num_sessions
    total_calls_est = total_sessions * sum(CALLS_PER_SESSION) // 2

    print(f"\n{C.BOLD}{C.MAGENTA}{'='*74}{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}  🔥 ПАРАЛЛЕЛЬНЫЙ СТРЕСС-ТЕСТ MCP СЕРВЕРА{C.RESET}")
    print(f"{C.BOLD}{C.MAGENTA}{'='*74}{C.RESET}")
    print(f"  Воркеров:          {num_workers}")
    print(f"  Сессий на воркер:  {num_sessions}")
    print(f"  Всего сессий:      {total_sessions}")
    print(f"  Ожид. вызовов:     ~{total_calls_est}")
    print(f"  Вызовов/сессия:    {CALLS_PER_SESSION[0]}-{CALLS_PER_SESSION[1]}")
    print(f"  Пауза м/вызовами: {CALL_DELAY[0]}-{CALL_DELAY[1]}с")
    print(f"  Пауза м/сессиями: {SESSION_DELAY[0]}-{SESSION_DELAY[1]}с")
    print()

    stats = Stats()

    tasks = [
        asyncio.create_task(worker(wid, num_sessions, stats))
        for wid in range(num_workers)
    ]

    await asyncio.gather(*tasks)
    stats.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
