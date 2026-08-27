"""Хранилище загруженных файлов.

Имя файла на диске всегда генерируется нами (uuid), пользовательское имя
используется только как метаданные для отображения — это исключает
path traversal и коллизии имён.

Хранилище в памяти: при рестарте backend реестр очищается (персистентность —
Фаза 1).
"""
import uuid
from pathlib import Path

from config import UPLOAD_DIR

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_files: dict[str, dict] = {}


def save_upload(data: bytes, original_name: str) -> str:
    """Сохраняет байты под uuid-именем, возвращает file_id."""
    extension = Path(original_name).suffix.lower()
    file_id = uuid.uuid4().hex
    file_path = UPLOAD_DIR / f"{file_id}{extension}"
    file_path.write_bytes(data)
    _files[file_id] = {"path": str(file_path), "name": original_name}
    return file_id


def get_file(file_id: str) -> str | None:
    record = _files.get(file_id)
    return record["path"] if record else None


def get_original_name(file_id: str) -> str | None:
    record = _files.get(file_id)
    return record["name"] if record else None


def save_file(path: str) -> str:
    """Обратная совместимость: регистрирует уже существующий файл."""
    file_id = uuid.uuid4().hex
    _files[file_id] = {"path": path, "name": Path(path).name}
    return file_id
