import asyncio
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def run_client():
    # Параметры подключения к серверу
    # В данном случае мы запускаем наш локальный server.py
    # Для стороннего сервера замените команду и аргументы
    server_params = StdioServerParameters(
        command=sys.executable, # Используем текущий python
        args=["server.py"],     # Запускаем наш скрипт сервера
        env=None                # Можно передать переменные окружения
    )

    print(f"🔌 Подключаемся к серверу...")
    
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            # 1. Инициализация
            await session.initialize()
            print("✅ Подключение успешно!")

            # 2. Получение списка инструментов
            print("\n📋 Список доступных инструментов:")
            tools = await session.list_tools()
            for tool in tools.tools:
                print(f"  - {tool.name}: {tool.description}")

            # 3. Вызов инструмента (Тест)
            tool_name = "shout"
            tool_args = {"text": "привет, мир!"}
            
            print(f"\n🚀 Тестируем инструмент '{tool_name}' с аргументами {tool_args}...")
            
            try:
                result = await session.call_tool(tool_name, tool_args)
                
                # Вывод результата
                print("🎉 Результат:")
                for content in result.content:
                    if content.type == "text":
                        print(f"  {content.text}")
                    else:
                        print(f"  [{content.type}]")
                        
            except Exception as e:
                print(f"❌ Ошибка при вызове инструмента: {e}")

if __name__ == "__main__":
    asyncio.run(run_client())
