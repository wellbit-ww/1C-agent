"""Хранилище загруженных файлов.

Имя файла на диске всегда генерируется нами (uuid), пользовательское имя
используется только как метаданные для отображения — это исключает
path traversal и коллизии имён.

Реестр персистентен (SQLite): после рестарта backend ранее загруженные
файлы остаются доступны по прежнему file_id.
"""
import logging
import re
import time
import uuid
from pathlib import Path

import config
from config import CACHE_DIR, UPLOAD_DIR
from services import cache_service, db_service

logger = logging.getLogger(__name__)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_files: dict[str, dict] = {}
_UUID_NAME = re.compile(r"^[0-9a-f]{32}$")


def save_upload(data: bytes, original_name: str) -> str:
    """Сохраняет байты под uuid-именем, возвращает file_id."""
    extension = Path(original_name).suffix.lower()
    file_id = uuid.uuid4().hex
    file_path = UPLOAD_DIR / f"{file_id}{extension}"
    file_path.write_bytes(data)
    record = {"path": str(file_path), "name": original_name}
    _files[file_id] = record
    try:
        db_service.save_file_record(file_id, original_name, str(file_path))
    except Exception as exc:
        logger.warning("Не удалось сохранить запись о файле в БД: %s", exc)
    return file_id


def _get_record(file_id: str) -> dict | None:
    record = _files.get(file_id)
    if record:
        return record

    # Рестарт backend: восстанавливаем из SQLite, проверяя, что файл на месте
    record_db = db_service.get_file_record(file_id)
    if not record_db:
        return None
    if not Path(record_db["path"]).exists():
        logger.warning("Файл %s из реестра отсутствует на диске", record_db["path"])
        return None

    record = {"path": record_db["path"], "name": record_db["original_name"]}
    _files[file_id] = record
    return record


def get_file(file_id: str) -> str | None:
    record = _get_record(file_id)
    return record["path"] if record else None


def get_original_name(file_id: str) -> str | None:
    record = _get_record(file_id)
    return record["name"] if record else None


def save_file(path: str) -> str:
    """Обратная совместимость: регистрирует уже существующий файл."""
    file_id = uuid.uuid4().hex
    name = Path(path).name
    _files[file_id] = {"path": path, "name": name}
    try:
        db_service.save_file_record(file_id, name, path)
    except Exception as exc:
        logger.warning("Не удалось сохранить запись о файле в БД: %s", exc)
    return file_id


def delete_file(file_id: str) -> bool:
    """Удаляет выгрузку, parquet-кэш и записи SQLite. False — файла не было."""
    mem = _files.get(file_id)
    db_rec = db_service.get_file_record(file_id)
    existed = mem is not None or db_rec is not None
    path = (mem or {}).get("path") or (db_rec or {}).get("path")
    if path:
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("Не удалось удалить файл %s: %s", path, exc)
    _files.pop(file_id, None)
    cache_service.remove_dataframe(file_id)
    try:
        db_service.delete_file_cascade(file_id)
    except Exception as exc:
        logger.warning("Не удалось очистить БД для %s: %s", file_id, exc)
    return existed


def purge_expired(max_age_hours: float | None = None) -> int:
    """Удаляет выгрузки старше TTL и uuid-сироты на диске. 0 TTL — только сироты старше TTL кэша."""
    hours = config.FILE_TTL_HOURS if max_age_hours is None else max_age_hours
    removed = 0
    known = set(db_service.list_file_ids())

    if hours > 0:
        cutoff = time.time() - hours * 3600
        for file_id in db_service.list_file_ids_older_than(cutoff):
            if delete_file(file_id):
                removed += 1
                known.discard(file_id)

    orphan_cutoff = time.time() - max(hours, config.CACHE_TTL_HOURS, 1) * 3600
    removed += _purge_orphan_dir(UPLOAD_DIR, known, orphan_cutoff)
    removed += _purge_orphan_dir(CACHE_DIR, known, orphan_cutoff, suffix=".parquet")
    return removed


def _purge_orphan_dir(
    directory: Path,
    known_ids: set[str],
    cutoff_ts: float,
    suffix: str | None = None,
) -> int:
    if not directory.exists():
        return 0
    removed = 0
    for path in directory.iterdir():
        if not path.is_file():
            continue
        file_id = _orphan_file_id(path, suffix)
        if file_id is None or file_id in known_ids:
            continue
        try:
            if path.stat().st_mtime >= cutoff_ts:
                continue
            path.unlink()
            (path.parent / f"{file_id}.dtypes.json").unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            logger.warning("Не удалось удалить сироту %s: %s", path, exc)
    return removed


def _orphan_file_id(path: Path, suffix: str | None) -> str | None:
    """uuid из имени файла выгрузки/кэша, включая sidecar <uuid>.dtypes.json."""
    name = path.name
    if name.endswith(".dtypes.json"):
        file_id = name[: -len(".dtypes.json")]
        return file_id if _UUID_NAME.match(file_id) else None
    if suffix and path.suffix.lower() != suffix:
        return None
    return path.stem if _UUID_NAME.match(path.stem) else None
