"""Кэш DataFrame: память + parquet-снимок на диске.

Parquet-кэш переживает рестарт backend (парсинг 10 МБ xlsx занимает
10–20 с, чтение parquet — доли секунды). Снимок старше CACHE_TTL_HOURS
считается протухшим — тогда читаем исходный файл заново.
"""
import logging
import time
from pathlib import Path

import pandas as pd

from config import CACHE_DIR, CACHE_TTL_HOURS

logger = logging.getLogger(__name__)

_cache: dict[str, pd.DataFrame] = {}


def _parquet_path(file_id: str) -> Path:
    return CACHE_DIR / f"{file_id}.parquet"


def _parquet_fresh(path: Path) -> bool:
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours <= CACHE_TTL_HOURS


def set_dataframe(file_id: str, df: pd.DataFrame) -> None:
    _cache[file_id] = df
    try:
        df.to_parquet(_parquet_path(file_id))
    except Exception as exc:
        # объектные колонки со смешанными типами могут не сериализоваться —
        # тогда просто живём на in-memory кэше и перепарсинге
        logger.warning("Не удалось записать parquet-кэш для %s: %s", file_id, exc)


def get_dataframe(file_id: str) -> pd.DataFrame | None:
    df = _cache.get(file_id)
    if df is not None:
        return df

    path = _parquet_path(file_id)
    if not path.exists():
        return None
    if not _parquet_fresh(path):
        logger.info("Parquet-кэш %s протух (TTL %s ч)", file_id, CACHE_TTL_HOURS)
        return None

    try:
        df = pd.read_parquet(path)
    except Exception as exc:
        logger.warning("Не удалось прочитать parquet-кэш %s: %s", file_id, exc)
        return None

    _cache[file_id] = df
    return df


def remove_dataframe(file_id: str) -> None:
    _cache.pop(file_id, None)
    _parquet_path(file_id).unlink(missing_ok=True)


def clear_cache() -> None:
    _cache.clear()
