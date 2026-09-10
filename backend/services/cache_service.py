"""Кэш DataFrame: память + parquet-снимок на диске.

Parquet-кэш переживает рестарт backend (парсинг 10 МБ xlsx занимает
10–20 с, чтение parquet — доли секунды). Снимок старше CACHE_TTL_HOURS
считается протухшим — тогда читаем исходный файл заново.
"""
import json
import logging
import time
from pathlib import Path

import pandas as pd

from config import CACHE_DIR, CACHE_TTL_HOURS

logger = logging.getLogger(__name__)

_cache: dict[str, pd.DataFrame] = {}


def _parquet_path(file_id: str) -> Path:
    return CACHE_DIR / f"{file_id}.parquet"


def _dtypes_path(file_id: str) -> Path:
    return CACHE_DIR / f"{file_id}.dtypes.json"


def _parquet_fresh(path: Path) -> bool:
    age_hours = (time.time() - path.stat().st_mtime) / 3600
    return age_hours <= CACHE_TTL_HOURS


def _dump_dtypes(df: pd.DataFrame) -> dict[str, str]:
    return {str(col): str(df[col].dtype) for col in df.columns}


def _restore_dtypes(df: pd.DataFrame, dtypes: dict) -> pd.DataFrame:
    """Возвращает числовые/датовые колонки после fallback object→str."""
    out = df.copy()
    for col, dtype in dtypes.items():
        if col not in out.columns:
            continue
        current = str(out[col].dtype)
        if current == dtype:
            continue
        try:
            if str(dtype).startswith("datetime"):
                out[col] = pd.to_datetime(out[col], errors="coerce")
            elif str(dtype).startswith(("int", "uint", "float", "Float", "Int", "UInt")):
                out[col] = pd.to_numeric(out[col], errors="coerce")
            elif str(dtype) in {"bool", "boolean"}:
                out[col] = out[col].astype("boolean")
        except Exception as exc:
            logger.debug("Не удалось восстановить тип %s.%s: %s", col, dtype, exc)
    return out


def _parquet_ready(df: pd.DataFrame) -> pd.DataFrame:
    """Object-колонки со смешанными str/float pyarrow не пишет — в parquet только str/None."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype != object:
            continue
        out[col] = [
            None if v is None or (isinstance(v, float) and v != v) else
            v if isinstance(v, str) else str(v)
            for v in out[col]
        ]
    return out


def _write_dtypes(file_id: str, df: pd.DataFrame) -> None:
    try:
        _dtypes_path(file_id).write_text(
            json.dumps(_dump_dtypes(df), ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("Не удалось записать схему кэша %s: %s", file_id, exc)


def set_dataframe(file_id: str, df: pd.DataFrame) -> None:
    _cache[file_id] = df
    path = _parquet_path(file_id)
    try:
        df.to_parquet(path)
        _write_dtypes(file_id, df)
        return
    except Exception:
        pass
    try:
        _parquet_ready(df).to_parquet(path)
        _write_dtypes(file_id, df)
    except Exception as exc:
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

    dtypes_file = _dtypes_path(file_id)
    if dtypes_file.exists():
        try:
            dtypes = json.loads(dtypes_file.read_text(encoding="utf-8"))
            if isinstance(dtypes, dict):
                df = _restore_dtypes(df, dtypes)
        except Exception as exc:
            logger.warning("Не удалось восстановить типы кэша %s: %s", file_id, exc)

    _cache[file_id] = df
    return df


def remove_dataframe(file_id: str) -> None:
    _cache.pop(file_id, None)
    _parquet_path(file_id).unlink(missing_ok=True)
    _dtypes_path(file_id).unlink(missing_ok=True)


def clear_cache() -> None:
    _cache.clear()
