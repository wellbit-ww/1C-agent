"""Общие фикстуры: реальные выгрузки 1С как регрессионный эталон."""
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DATA_DIR = Path(__file__).resolve().parent / "data"
SALES_FILE = DATA_DIR / "sales.xlsx"
DEFICIT_FILE = DATA_DIR / "deficit.xlsx"


@pytest.fixture(scope="session")
def sales_df():
    from services.excel_service import read_excel

    return read_excel(str(SALES_FILE))


@pytest.fixture(scope="session")
def deficit_df():
    from services.excel_service import read_excel

    return read_excel(str(DEFICIT_FILE))


def ollama_available() -> bool:
    try:
        import httpx

        from config import OLLAMA_BASE_URL

        response = httpx.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=2)
        return response.status_code == 200
    except Exception:
        return False


requires_ollama = pytest.mark.skipif(
    not ollama_available(),
    reason="Ollama недоступна — LLM-тест пропущен",
)
