"""
Стресс-тест MCP сервера Vaiz.
Рандомно вызывает разные инструменты, отключается и переподключается.
"""

import asyncio
import json
import random
import time
import sys
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

# Сколько сессий (подключений/отключений)
NUM_SESSIONS = 5
# Сколько вызовов за одну сессию
CALLS_PER_SESSION = (3, 8)
# Пауза между вызовами (сек)
CALL_DELAY = (0.2, 1.5)
# Пауза между сессиями (сек)
SESSION_DELAY = (1.0, 3.0)

# ─── Цвета для логов ─────────────────────────────────────────────────────────

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


def log(msg: str, color: str = C.RESET):
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{C.DIM}[{ts}]{C.RESET} {color}{msg}{C.RESET}")


# ─── Определение рандомных вызовов ───────────────────────────────────────────

def random_tool_calls() -> list[tuple[str, dict]]:
    """Возвращает список возможных (tool_name, args) для рандомного выбора."""
    calls = [
        # Без аргументов
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

        # С аргументами — проекты
        ("get_project", {"projectId": random.choice(PROJECT_IDS)}),
        ("list_boards", {"projectId": random.choice(PROJECT_IDS)}),
        ("list_milestones", {"projectId": random.choice(PROJECT_IDS)}),
        ("get_project_history", {"projectId": random.choice(PROJECT_IDS), "limit": 5}),

        # С аргументами — доски
        ("get_board", {"boardId": random.choice(BOARD_IDS)}),
        ("list_milestones", {"boardId": random.choice(BOARD_IDS)}),
        ("get_automations", {"boardId": random.choice(BOARD_IDS)}),
        ("get_tasks", {"boardId": random.choice(BOARD_IDS), "limit": 5}),

        # С аргументами — участники
        ("get_member", {"memberId": random.choice(MEMBER_IDS)}),
        ("get_user_history", {"memberId": random.choice(MEMBER_IDS), "limit": 5}),

        # Поиск
        ("search", {"query": random.choice(SEARCH_QUERIES), "entityType": random.choice(SEARCH_ENTITY_TYPES), "limit": 5}),
        ("search", {"query": random.choice(SEARCH_QUERIES), "limit": 3}),

        # Пространство
        ("select_space", {"spaceId": SPACE_ID}),

        # Уведомления с фильтрами
        ("get_notifications", {"limit": 5, "readStatus": "Unread"}),
        ("get_notifications", {"limit": 10, "groups": ["TaskChanges"]}),
    ]
    return calls


# ─── Статистика ──────────────────────────────────────────────────────────────

class Stats:
    def __init__(self):
        self.total_calls = 0
        self.successful = 0
        self.errors = 0
        self.tool_stats: dict[str, list[float]] = {}
        self.sessions = 0
        self.session_errors = 0
        self.start_time = time.time()

    def record_call(self, tool: str, duration: float, success: bool):
        self.total_calls += 1
        if success:
            self.successful += 1
        else:
            self.errors += 1
        self.tool_stats.setdefault(tool, []).append(duration)

    def print_summary(self):
        elapsed = time.time() - self.start_time
        print(f"\n{'='*70}")
        print(f"{C.BOLD}{C.CYAN}  ИТОГИ СТРЕСС-ТЕСТА{C.RESET}")
        print(f"{'='*70}")
        print(f"  Время выполнения:     {elapsed:.1f}с")
        print(f"  Сессий:               {self.sessions} (ошибок подключения: {self.session_errors})")
        print(f"  Всего вызовов:        {self.total_calls}")
        print(f"  {C.GREEN}Успешных:             {self.successful}{C.RESET}")
        print(f"  {C.RED}С ошибками:           {self.errors}{C.RESET}")
        if self.total_calls > 0:
            avg = sum(d for times in self.tool_stats.values() for d in times) / self.total_calls
            print(f"  Среднее время ответа: {avg*1000:.0f}мс")

        print(f"\n  {'Инструмент':<30} {'Вызовов':>8} {'Ср.время':>10} {'Макс':>10}")
        print(f"  {'-'*30} {'-'*8} {'-'*10} {'-'*10}")
        for tool, times in sorted(self.tool_stats.items(), key=lambda x: len(x[1]), reverse=True):
            avg_t = sum(times) / len(times)
            max_t = max(times)
            print(f"  {tool:<30} {len(times):>8} {avg_t*1000:>8.0f}мс {max_t*1000:>8.0f}мс")
        print(f"{'='*70}\n")


# ─── Основная логика ─────────────────────────────────────────────────────────

async def run_session(session_num: int, stats: Stats):
    """Одна сессия: подключиться, вызвать рандомные инструменты, отключиться."""

    log(f"━━━ Сессия #{session_num} — подключение...", C.BOLD + C.BLUE)
    stats.sessions += 1

    server_params = StdioServerParameters(
        command=SERVER_COMMAND,
        args=SERVER_ARGS,
        env=SERVER_ENV,
    )

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                log(f"  Подключено! Инициализация OK", C.GREEN)

                # Сколько вызовов в этой сессии
                num_calls = random.randint(*CALLS_PER_SESSION)
                log(f"  Планируется вызовов: {num_calls}", C.CYAN)

                for i in range(num_calls):
                    # Выбираем рандомный инструмент
                    tool_name, tool_args = random.choice(random_tool_calls())

                    args_str = json.dumps(tool_args, ensure_ascii=False) if tool_args else "()"
                    log(f"  [{i+1}/{num_calls}] → {tool_name} {args_str}", C.YELLOW)

                    t0 = time.time()
                    try:
                        result = await session.call_tool(tool_name, tool_args)
                        duration = time.time() - t0
                        stats.record_call(tool_name, duration, success=True)

                        # Показываем краткий результат
                        is_error = getattr(result, "is_error", False) or getattr(result, "isError", False)
                        if is_error:
                            preview = str(result.content[0].text)[:120] if result.content else "?"
                            log(f"       ⚠ Ошибка от сервера ({duration*1000:.0f}мс): {preview}", C.RED)
                            stats.errors += 1
                            stats.successful -= 1
                        else:
                            preview = ""
                            if result.content:
                                first = result.content[0].text
                                preview = first[:100].replace("\n", " ")
                            log(f"       ✓ OK ({duration*1000:.0f}мс): {preview}...", C.GREEN)

                    except Exception as e:
                        duration = time.time() - t0
                        stats.record_call(tool_name, duration, success=False)
                        log(f"       ✗ EXCEPTION ({duration*1000:.0f}мс): {e}", C.RED)

                    # Пауза между вызовами
                    delay = random.uniform(*CALL_DELAY)
                    await asyncio.sleep(delay)

        log(f"  Отключено. Сессия #{session_num} завершена.", C.BLUE)

    except Exception as e:
        stats.session_errors += 1
        log(f"  ОШИБКА СЕССИИ #{session_num}: {e}", C.RED + C.BOLD)


async def main():
    num_sessions = NUM_SESSIONS
    if len(sys.argv) > 1:
        num_sessions = int(sys.argv[1])

    log(f"🚀 Стресс-тест MCP сервера Vaiz", C.BOLD + C.MAGENTA)
    log(f"   Сессий: {num_sessions}, вызовов за сессию: {CALLS_PER_SESSION[0]}-{CALLS_PER_SESSION[1]}", C.MAGENTA)
    print()

    stats = Stats()

    for s in range(1, num_sessions + 1):
        await run_session(s, stats)

        if s < num_sessions:
            pause = random.uniform(*SESSION_DELAY)
            log(f"  ... пауза {pause:.1f}с перед следующей сессией ...\n", C.DIM)
            await asyncio.sleep(pause)

    stats.print_summary()


if __name__ == "__main__":
    asyncio.run(main())
