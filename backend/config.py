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
