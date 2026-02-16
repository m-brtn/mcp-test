from fastmcp import FastMCP

# Создаем сервер FastMCP
mcp = FastMCP("TextProcessor")

@mcp.tool()
def shout(text: str) -> str:
    """Преобразует текст в верхний регистр (КРИЧИТ)."""
    return text.upper()

@mcp.tool()
def whisper(text: str) -> str:
    """Преобразует текст в нижний регистр (шепчет)."""
    return text.lower()

@mcp.tool()
def reverse(text: str) -> str:
    """Переворачивает строку задом наперед."""
    return text[::-1]

@mcp.tool()
def word_count(text: str) -> int:
    """Считает количество слов в тексте."""
    return len(text.split())

if __name__ == "__main__":
    # Запуск сервера
    mcp.run()
