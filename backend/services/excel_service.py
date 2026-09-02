from pathlib import Path

import pandas as pd

from services.exceptions import EmptyDataFrameError, InvalidFileError
from services.excel_parser import parse_excel

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

# Магические байты: .xlsx — это ZIP-архив, .xls — OLE2 compound document
_XLSX_SIGNATURE = b"PK\x03\x04"
_XLS_SIGNATURE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def validate_excel_filename(filename: str | None) -> None:
    if not filename:
        raise InvalidFileError("Имя файла не указано")

    extension = Path(filename).suffix.lower()

    if extension not in ALLOWED_EXTENSIONS:
        raise InvalidFileError(
            "Поддерживаются только файлы .xlsx, .xls и .csv"
        )


def validate_excel_content(data: bytes, filename: str | None) -> None:
    """Проверяет, что содержимое действительно похоже на Excel-файл,
    а расширение соответствует формату."""
    if len(data) < 8:
        raise InvalidFileError("Файл слишком мал или пуст")

    extension = Path(filename or "").suffix.lower()

    # у CSV нет магических байтов — текстовый формат проверяется при парсинге
    if extension == ".csv":
        return

    is_xlsx = data.startswith(_XLSX_SIGNATURE)
    is_xls = data.startswith(_XLS_SIGNATURE)

    if not (is_xlsx or is_xls):
        raise InvalidFileError(
            "Содержимое файла не похоже на Excel (.xlsx/.xls)"
        )

    if extension == ".xlsx" and not is_xlsx:
        raise InvalidFileError(
            "Файл имеет расширение .xlsx, но другой формат содержимого"
        )

    if extension == ".xls" and not is_xls:
        raise InvalidFileError(
            "Файл имеет расширение .xls, но другой формат содержимого"
        )


def read_workbook(file_path: str) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Все непустые видимые листы + рабочий DataFrame (первый лист)."""
    validate_excel_filename(file_path)

    try:
        sheets = parse_excel(file_path)
    except Exception as exc:
        raise InvalidFileError(
            f"Не удалось прочитать Excel-файл: {exc}"
        ) from exc

    if not sheets:
        raise EmptyDataFrameError("Excel-файл не содержит валидных данных")

    return next(iter(sheets.values())), sheets


def read_excel(file_path: str):
    df, _ = read_workbook(file_path)
    return df
