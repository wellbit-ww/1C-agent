import pandas as pd

_cache: dict[str, pd.DataFrame] = {}


def set_dataframe(file_id: str, df: pd.DataFrame) -> None:
    _cache[file_id] = df


def get_dataframe(file_id: str) -> pd.DataFrame | None:
    return _cache.get(file_id)


def remove_dataframe(file_id: str) -> None:
    _cache.pop(file_id, None)


def clear_cache() -> None:
    _cache.clear()
