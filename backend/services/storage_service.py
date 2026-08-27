"""Хранилище загруженных файлов.

Имя файла на диске всегда генерируется нами (uuid), пользовательское имя
используется только как метаданные для отображения — это исключает
path traversal и коллизии имён.

Реестр персистентен (SQLite): после рестарта backend ранее загруженные
файлы остаются доступны по прежнему file_id.
"""
import logging
import uuid
from pathlib import Path

from config import UPLOAD_DIR
from services import db_service

logger = logging.getLogger(__name__)

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_files: dict[str, dict] = {}


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
