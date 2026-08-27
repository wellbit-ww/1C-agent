"""Единая конфигурация backend.

Все параметры можно переопределить через переменные окружения или .env-файл
в корне проекта (см. .env.example).
"""
import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

load_dotenv(PROJECT_ROOT / ".env")

# Куда складывать загруженные файлы. Имя на диске — uuid (см. storage_service),
# поэтому путь полностью под нашим контролем.
UPLOAD_DIR = Path(
    os.getenv("EXCEL_AGENT_UPLOAD_DIR", BACKEND_DIR / "uploads")
).resolve()

# Лимит размера загружаемого файла
MAX_UPLOAD_MB = int(os.getenv("EXCEL_AGENT_MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024

# CORS: Streamlit UI и локальная разработка
CORS_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "EXCEL_AGENT_CORS_ORIGINS",
        "http://localhost:8501,http://127.0.0.1:8501",
    ).split(",")
    if origin.strip()
]

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MAIN_MODEL = os.getenv("EXCEL_AGENT_MODEL", "qwen3:8b")
ROUTER_MODEL = os.getenv("EXCEL_AGENT_ROUTER_MODEL", MAIN_MODEL)
# Таймаут health-check при старте, секунды
OLLAMA_HEALTHCHECK_TIMEOUT = float(os.getenv("EXCEL_AGENT_OLLAMA_HC_TIMEOUT", "3"))

# Персистентность (Фаза 1): SQLite + parquet-кэш
DATA_DIR = Path(
    os.getenv("EXCEL_AGENT_DATA_DIR", BACKEND_DIR / "data")
).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = Path(os.getenv("EXCEL_AGENT_DB", DATA_DIR / "agent.db"))
CACHE_DIR = Path(
    os.getenv("EXCEL_AGENT_CACHE_DIR", DATA_DIR / "cache")
).resolve()
CACHE_DIR.mkdir(parents=True, exist_ok=True)
# Сколько часов parquet-кэш считается свежим; старше — перепарсинг из исходника
CACHE_TTL_HOURS = float(os.getenv("EXCEL_AGENT_CACHE_TTL_HOURS", "72"))

# Чат: сколько последних сообщений учитывать как контекст диалога
CHAT_HISTORY_LIMIT = int(os.getenv("EXCEL_AGENT_CHAT_HISTORY_LIMIT", "20"))
CHAT_HISTORY_CONTEXT = int(os.getenv("EXCEL_AGENT_CHAT_HISTORY_CONTEXT", "6"))
