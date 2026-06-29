from pathlib import Path

import pandas as pd

from services.exceptions import EmptyDataFrameError, InvalidFileError
from services.excel_parser import parse_excel

ALLOWED_EXTENSIONS = {".xlsx", ".xls"}


def validate_excel_filename(filename: str | None) -> None:
    if not filename:
        raise InvalidFileError("Имя файла не указано")

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(
            "Поддерживаются только файлы .xlsx и .xls"
        )


def read_excel(file_path: str):
    validate_excel_filename(file_path)

    try:
        sheets = parse_excel(file_path)
    except Exception as exc:
        raise InvalidFileError(
            f"Не удалось прочитать Excel-файл: {exc}"
        ) from exc

    if not sheets:
        raise EmptyDataFrameError("Excel-файл не содержит валидных данных")

    # Return the first non-empty sheet
    return next(iter(sheets.values()))
