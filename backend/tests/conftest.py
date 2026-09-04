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


@pytest.fixture(scope="session", autouse=True)
def _isolated_storage(tmp_path_factory):
    """Весь тестовый сеанс работает в песочнице: боевые agent.db,
    uploads/ и parquet-кэш не трогаем — иначе lifespan-purge при
    старте TestClient удалит реальные файлы пользователя."""
    import config
    from services import cache_service, db_service, storage_service

    root = tmp_path_factory.mktemp("isolated_storage")
    (root / "uploads").mkdir()
    (root / "cache").mkdir()

    mp = pytest.MonkeyPatch()
    # TTL-очистку по возрасту глушим: в тестах файлы «свежие», но
    # гарантия, что ни один lifespan/upload не удалит ничего лишнего
    mp.setattr(config, "FILE_TTL_HOURS", 0.0)
    mp.setattr(db_service, "DB_PATH", root / "agent.db")
    mp.setattr(storage_service, "UPLOAD_DIR", root / "uploads")
    mp.setattr(storage_service, "CACHE_DIR", root / "cache")
    mp.setattr(cache_service, "CACHE_DIR", root / "cache")
    db_service.init_db()
    yield
    mp.undo()


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
