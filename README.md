# MCP Test — стресс-тестирование MCP серверов

Набор скриптов для тестирования MCP серверов через Python-клиент и FastMCP CLI.

## Требования

- Python 3.12+
- Node.js (если тестируемый MCP сервер на Node)

## Установка

```bash
# Клонируем / переходим в папку проекта
cd mcp-test

# Создаем виртуальное окружение
python3 -m venv .venv

# Активируем
source .venv/bin/activate

# Ставим зависимости
pip install -r requirements.txt
```

## Структура проекта

```
mcp-test/
├── mcp-config.json            # Конфиг подключения к MCP серверу
├── server.py                  # Пример MCP сервера (для локальных тестов)
├── client_test.py             # Простой клиент — подключиться и вызвать инструмент
├── stress_test.py             # Последовательный стресс-тест (один клиент)
├── stress_test_parallel.py    # Параллельный стресс-тест (несколько клиентов)
├── requirements.txt           # Зависимости Python
└── README.md
```

## Настройка подключения

### Вариант 1 — через `mcp-config.json`

Используется для FastMCP CLI. Пример:

```json
{
  "mcpServers": {
    "VaizLocalNpm": {
      "command": "node",
      "args": ["/path/to/mcp-server/dist/cli.js"],
      "env": {
        "VAIZ_API_TOKEN": "your_token",
        "VAIZ_SPACE_ID": "your_space_id",
        "VAIZ_API_URL": "https://api.example.com/mcp"
      }
    }
  }
}
```

### Вариант 2 — в Python-скриптах

В `stress_test.py` и `stress_test_parallel.py` конфиг задается в начале файла:

```python
SERVER_COMMAND = "node"
SERVER_ARGS = ["/path/to/mcp-server/dist/cli.js"]
SERVER_ENV = {
    "VAIZ_API_TOKEN": "your_token",
    "VAIZ_SPACE_ID": "your_space_id",
    "VAIZ_API_URL": "https://api.example.com/mcp",
    "NODE_TLS_REJECT_UNAUTHORIZED": "0",  # для самоподписанных сертификатов
    "PATH": "/usr/local/bin:/usr/bin:/bin",
}
```

Также обновите `PROJECT_IDS`, `BOARD_IDS`, `MEMBER_IDS` реальными значениями из вашего воркспейса.

## Использование

### FastMCP CLI

Быстрый способ посмотреть инструменты и вызвать их вручную:

```bash
# Список всех инструментов сервера
fastmcp list mcp-config.json

# Вызвать конкретный инструмент
fastmcp call mcp-config.json ping
fastmcp call mcp-config.json search query="баг" entityType="task"
fastmcp call mcp-config.json get_task taskId="PRJ-123"

# JSON-вывод (для автоматизации)
fastmcp call mcp-config.json list_projects --json
```

### Простой клиент

Подключается к серверу, показывает список инструментов, вызывает один из них:

```bash
python client_test.py
```

### Последовательный стресс-тест

Один клиент последовательно открывает сессии, в каждой рандомно вызывает инструменты, затем отключается и переподключается.

```bash
# 5 сессий (по умолчанию)
python stress_test.py

# Указать количество сессий
python stress_test.py 10
```

Настраиваемые параметры (в начале файла):

| Параметр | По умолчанию | Описание |
|---|---|---|
| `NUM_SESSIONS` | 5 | Количество сессий |
| `CALLS_PER_SESSION` | (3, 8) | Диапазон вызовов за сессию |
| `CALL_DELAY` | (0.2, 1.5) | Пауза между вызовами (сек) |
| `SESSION_DELAY` | (1.0, 3.0) | Пауза между сессиями (сек) |

### Параллельный стресс-тест

Несколько воркеров (клиентов) одновременно подключаются к серверу и параллельно вызывают рандомные инструменты. Каждый воркер — независимый stdio процесс.

```bash
# 3 воркера, 3 сессии каждый (по умолчанию)
python stress_test_parallel.py

# 5 воркеров, 3 сессии
python stress_test_parallel.py 5

# 5 воркеров, 10 сессий каждый
python stress_test_parallel.py 5 10

# Хардкор: 10 воркеров по 20 сессий
python stress_test_parallel.py 10 20
```

Настраиваемые параметры (в начале файла):

| Параметр | По умолчанию | Описание |
|---|---|---|
| `NUM_WORKERS` | 3 | Количество параллельных воркеров |
| `SESSIONS_PER_WORKER` | 3 | Сессий на каждый воркер |
| `CALLS_PER_SESSION` | (3, 8) | Диапазон вызовов за сессию |
| `CALL_DELAY` | (0.1, 1.0) | Пауза между вызовами (сек) |
| `SESSION_DELAY` | (0.5, 2.0) | Пауза между сессиями (сек) |

## Отчет

После завершения тестов выводится сводная таблица:

- **RPS** — запросов в секунду
- **p50 / p95 / p99** — перцентили времени ответа
- **По воркерам** — сколько вызовов сделал каждый
- **По инструментам** — среднее, p95 и максимальное время

Пример вывода:

```
======================================================================
  ИТОГИ ПАРАЛЛЕЛЬНОГО СТРЕСС-ТЕСТА
======================================================================
  Время выполнения:     26.8с
  Воркеров:             3
  Сессий:               6 (ошибок подключения: 0)
  Всего вызовов:        41
  Успешных:             41
  С ошибками:           0
  RPS (запросов/с):     1.5
  Среднее время:        1127мс
  p50:                  929мс
  p95:                  1794мс
  p99:                  2110мс
```

## Тестирование локального сервера

Для проверки самого тестового окружения можно поднять встроенный `server.py`:

```bash
# Через FastMCP CLI
fastmcp list server.py
fastmcp call server.py shout text="привет"

# Через FastMCP Dev UI (откроет браузер)
fastmcp dev server.py
```
