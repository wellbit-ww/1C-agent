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


@pytest.fixture(scope="session")
def sales_workbook():
    from services.excel_parser import parse_excel

    return parse_excel(str(SALES_FILE))


@pytest.fixture(scope="session")
def pdo_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "наименование работ": ["Шкаф А", "Шкаф Б", "Пульт", "Рама"],
            "статус/ приоритет": ["Завершено", "В работе", "В работе", "Завершено"],
            "количество изделий": [2, 1, 3, 1],
            "чел.час по плану, ссму": [5.0, 8.0, 12.0, 3.0],
            "% готовности": [1.0, 0.4, 0.2, 1.0],
            "ответственное подразделение": ["ССМУ", "ССМУ", "УПМ", "ПУ"],
            "дата готовности": pd.to_datetime(
                ["2024-06-28", "2024-07-10", "2024-07-15", "2024-06-01"]
            ),
            "возможен срыв сроков": ["Нет", "Да", "Нет", "Нет"],
        }
    )


@pytest.fixture(scope="session")
def warranty_df():
    import pandas as pd

    return pd.DataFrame(
        {
            "№ п/п": [1, 2, 3, 4],
            "номенклатура": ["Питатель", "Шкаф", "Двигатель", "Датчик"],
            "срок гарантии": ["06.07.2025", "01.01.2026", "15.03.2026", "20.04.2026"],
            "сервисный инженер": [
                "Иванов Сергей",
                "Иванов Сергей",
                "Петров Иван",
                "Петров Иван",
            ],
            "подразделение (продажа)": ["СТО", "СТО", "СМЭ", "СМЭ"],
            "контрагент": [
                "АЛАБУГА МАШИНЕРИ ООО",
                "АЛАБУГА МАШИНЕРИ ООО",
                "РОБЕЛ ООО",
                "КЭАЗ АО",
            ],
            "заказ клиента.номер": [
                "САУП-000111",
                "САУП-000112",
                "САУП-000220",
                "САУП-000330",
            ],
        }
    )


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
