import re
import zipfile
from xml.etree import ElementTree as ET

import openpyxl
import pandas as pd

_METADATA_MARKERS = ("параметры", "отбор", "фильтр", "настройк", "отчет сформирован")
_TOTAL_MARKERS = ("итого", "всего")
_DASH_VALUES = {"-", "–", "—"}


def _is_blank(value) -> bool:
    if value is None:
        return True
    # NaN из pandas/xlrd
    if isinstance(value, float) and value != value:
        return True
    return isinstance(value, str) and not value.strip()


def _is_dash(value) -> bool:
    return isinstance(value, str) and value.strip() in _DASH_VALUES


def _numeric_text(text: str) -> str | None:
    cleaned = text.strip().replace("\xa0", "").replace(" ", "").replace(",", ".")
    if not cleaned or cleaned in _DASH_VALUES:
        return None
    try:
        float(cleaned)
    except ValueError:
        return None
    return cleaned


def _is_number_like(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return _numeric_text(value) is not None
    return False


def _coerce_scalar(value):
    if _is_dash(value):
        return None
    if isinstance(value, str):
        numeric = _numeric_text(value)
        if numeric is not None:
            return float(numeric)
        return value.strip()
    return value


def _clean_name(name) -> str:
    return re.sub(r"\s+", " ", str(name)).strip().lower()


def _dedupe(names: list[str]) -> list[str]:
    seen: dict[str, int] = {}
    result = []
    for name in names:
        name = name or "unnamed"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        result.append(name)
    return result


def _is_total_row(values) -> bool:
    for value in values[:2]:
        if isinstance(value, str) and value.strip().lower().startswith(_TOTAL_MARKERS):
            return True
    return False


def _finalize(records: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(records, columns=columns)
    df = df.dropna(axis=1, how="all")
    non_empty = df.notna().sum(axis=1)
    df = df[non_empty > 1]
    return _force_numeric_amounts(df.reset_index(drop=True))


_AMOUNT_NAME_MARKERS = (
    "сумма",
    "долг",
    "оплат",
    "стоим",
    "цена",
    "выручк",
    "дефицит",
    "поступлен",
    "задолжен",
)


def _force_numeric_amounts(df: pd.DataFrame) -> pd.DataFrame:
    """Денежные колонки и почти-числовые object → float."""
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            continue
        name = str(col).lower()
        coerced = df[col].map(_coerce_scalar)
        numeric = pd.to_numeric(coerced, errors="coerce")
        ratio = float(numeric.notna().mean()) if len(df) else 0.0
        amount_like = any(marker in name for marker in _AMOUNT_NAME_MARKERS)
        if amount_like and numeric.notna().sum() >= 1:
            df[col] = numeric
        elif ratio >= 0.6:
            df[col] = numeric
    return df


def _parse_grouped_rows(rows: list[tuple[tuple, int]]) -> pd.DataFrame | None:
    """Разворачивает иерархический отчёт 1С (outline-уровни) в плоскую таблицу.

    Шапка такого отчёта — несколько строк с названиями уровней группировки
    в первой колонке; первая из них несёт названия колонок-показателей,
    а строка уровня-листа — названия колонок-атрибутов. Строки данных
    сгруппированы по outline-уровням; промежуточные уровни — это субитоги,
    поэтому в результат попадают только строки уровня-листа, а путь по
    вышестоящим уровням становится колонками.
    """
    header_rows: list[tuple] = []
    data_start: int | None = None

    for idx, (values, _level) in enumerate(rows):
        if any(_is_number_like(v) for v in values):
            data_start = idx
            break
        first = "" if _is_blank(values[0]) else str(values[0]).strip().lower()
        if not first or first.startswith(_METADATA_MARKERS):
            continue
        header_rows.append(values)

    if data_start is None or len(header_rows) < 2:
        return None

    grouping_names = _dedupe([_clean_name(v[0]) for v in header_rows])
    depth = len(grouping_names)

    titles: dict[int, str] = {}
    title_owner: dict[int, int] = {}
    for row_idx, values in enumerate(header_rows):
        for j in range(1, len(values)):
            if j in titles:
                continue
            cell = values[j]
            if isinstance(cell, str) and cell.strip() and not _is_dash(cell):
                titles[j] = _clean_name(cell)
                title_owner[j] = row_idx

    if not titles:
        return None

    attr_count = [0] * depth
    for j, owner in title_owner.items():
        if owner > 0:
            attr_count[owner] += 1
    leaf_level = max(range(1, depth), key=lambda i: attr_count[i]) if depth > 1 else 0
    if attr_count[leaf_level] == 0:
        leaf_level = depth - 1

    ordered_titles = dict(sorted(titles.items()))
    stack: dict[int, str] = {}
    records: list[dict] = []

    for values, level in rows[data_start:]:
        name_val = "" if _is_blank(values[0]) else str(values[0]).strip()
        if _is_total_row(values):
            if level in stack:
                del stack[level]
            continue
        if level < leaf_level:
            stack[level] = name_val
            for key in list(stack):
                if key > level:
                    del stack[key]
            continue
        if level > leaf_level:
            continue

        record = {grouping_names[k]: stack.get(k) for k in range(leaf_level)}
        record[grouping_names[leaf_level]] = name_val
        for j, title in ordered_titles.items():
            if j < len(values):
                record[title] = _coerce_scalar(values[j])
        records.append(record)

    if not records:
        return None

    columns = (
        grouping_names[: leaf_level + 1]
        + [t for t in ordered_titles.values() if t not in grouping_names]
    )
    return _finalize(records, columns)


def _find_header_idx(matrix: list[tuple]) -> int:
    best_idx, best_score = 0, -1
    for i, values in enumerate(matrix[:20]):
        score = sum(
            1
            for v in values
            if isinstance(v, str) and v.strip() and not _is_dash(v) and not _is_number_like(v)
        )
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx


def _merge_subheader(matrix: list[tuple], header_idx: int, headers: list[str]) -> int:
    """Склеивает двухстрочную шапку («К оплате» → «До отгрузки» и т.п.)."""
    if header_idx + 1 >= len(matrix):
        return header_idx

    sub = matrix[header_idx + 1]
    if any(_is_number_like(v) for v in sub):
        return header_idx

    sub_strings = {
        j: str(v).strip()
        for j, v in enumerate(sub)
        if isinstance(v, str) and v.strip() and not _is_dash(v)
    }
    orphan_cols = [j for j in sub_strings if j < len(headers) and not headers[j]]
    if len(orphan_cols) < 2:
        return header_idx

    last_named = ""
    for j in range(len(headers)):
        if headers[j]:
            last_named = headers[j]
        if j in sub_strings:
            parent = headers[j] or last_named
            headers[j] = f"{parent} — {sub_strings[j]}" if parent else sub_strings[j]
    return header_idx + 1


def _parse_flat_rows(rows: list[tuple[tuple, int]]) -> pd.DataFrame | None:
    matrix = [values for values, _ in rows]
    if not matrix:
        return None

    width = max(len(r) for r in matrix)
    matrix = [tuple(r) + (None,) * (width - len(r)) for r in matrix]

    keep_cols = [
        j for j in range(width) if any(not _is_blank(r[j]) for r in matrix)
    ]
    matrix = [tuple(r[j] for j in keep_cols) for r in matrix]

    header_idx = _find_header_idx(matrix)
    headers = [
        _clean_name(v) if not _is_blank(v) else ""
        for v in matrix[header_idx]
    ]
    data_start = _merge_subheader(matrix, header_idx, headers) + 1
    columns = _dedupe(headers)

    records = []
    for values in matrix[data_start:]:
        if _is_total_row(values):
            continue
        record = {
            columns[j]: _coerce_scalar(values[j])
            for j in range(len(columns))
        }
        records.append(record)

    if not records:
        return None

    df = pd.DataFrame(records, columns=columns)
    unnamed_all_empty = [
        c for c in df.columns if c.startswith("unnamed") and df[c].isna().all()
    ]
    df = df.drop(columns=unnamed_all_empty)
    df = df.dropna(axis=1, how="all")
    non_empty = df.notna().sum(axis=1)
    df = df[non_empty > 1]
    return _force_numeric_amounts(df.reset_index(drop=True))


def _parse_csv(file_path: str) -> dict[str, pd.DataFrame]:
    """CSV из 1С: обычно cp1251 и разделитель «;» — всё это нюхаем."""
    from pathlib import Path

    df = None
    last_error: Exception | None = None
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            df = pd.read_csv(
                file_path,
                sep=None,
                engine="python",
                encoding=encoding,
                dtype=str,
            )
            break
        except (UnicodeDecodeError, pd.errors.ParserError) as exc:
            last_error = exc
    if df is None:
        raise ValueError(f"Не удалось прочитать CSV: {last_error}")

    # та же чистка, что в xlsx-парсере: тире -> пусто, «1 234,56» -> число
    for col in df.columns:
        df[col] = df[col].map(_coerce_scalar)
        numeric_ratio = pd.to_numeric(df[col], errors="coerce").notna().mean()
        if numeric_ratio >= 0.8:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df.columns = _dedupe([_clean_name(c) for c in df.columns])
    df = df.dropna(axis=1, how="all")
    df = _force_numeric_amounts(df)
    name = Path(file_path).stem
    return {name: df} if not df.empty else {}


_NS_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _xlsx_sheet_outlines(file_path: str) -> dict[str, dict[int, int]]:
    """outlineLevel строк из XML — в read_only openpyxl row_dimensions нет."""
    outlines: dict[str, dict[int, int]] = {}
    try:
        with zipfile.ZipFile(file_path) as zf:
            rels: dict[str, str] = {}
            try:
                root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            except KeyError:
                return outlines
            for rel in root:
                rid = rel.attrib.get("Id")
                target = (rel.attrib.get("Target") or "").replace("\\", "/")
                if not rid or not target:
                    continue
                if target.startswith("/"):
                    path = target.lstrip("/")
                elif target.startswith("xl/"):
                    path = target
                else:
                    path = "xl/" + target.lstrip("./")
                rels[rid] = path

            wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
            for sheet in wb_root.iter():
                if not sheet.tag.endswith("}sheet") and sheet.tag != "sheet":
                    continue
                name = sheet.attrib.get("name")
                rid = sheet.attrib.get(_NS_R_ID) or sheet.attrib.get("id")
                xml_path = rels.get(rid or "")
                if not name or not xml_path or xml_path not in zf.namelist():
                    continue
                levels: dict[int, int] = {}
                with zf.open(xml_path) as handle:
                    for _event, elem in ET.iterparse(handle, events=("end",)):
                        tag = elem.tag
                        if tag.endswith("}row") or tag == "row":
                            row_num = elem.attrib.get("r")
                            level = elem.attrib.get("outlineLevel")
                            if row_num and level:
                                try:
                                    levels[int(row_num)] = int(level)
                                except ValueError:
                                    pass
                            elem.clear()
                if levels:
                    outlines[name] = levels
    except (OSError, zipfile.BadZipFile, ET.ParseError):
        return {}
    return outlines


def _parse_xls(file_path: str) -> dict[str, pd.DataFrame]:
    """Старый .xls: openpyxl не читает, xlrd отдаёт плоскую сетку без outline."""
    xl = pd.ExcelFile(file_path, engine="xlrd")
    sheets: dict[str, pd.DataFrame] = {}
    for sheet_name in xl.sheet_names:
        raw = pd.read_excel(xl, sheet_name=sheet_name, header=None, dtype=object)
        rows: list[tuple[tuple, int]] = []
        for tup in raw.itertuples(index=False, name=None):
            values = tuple(None if (isinstance(v, float) and v != v) else v for v in tup)
            if all(_is_blank(v) for v in values):
                continue
            rows.append((values, 0))
        if not rows:
            continue
        df = _parse_flat_rows(rows)
        if df is not None and not df.empty:
            sheets[str(sheet_name)] = df
    return sheets


def parse_excel(file_path: str) -> dict[str, pd.DataFrame]:
    """Читает все видимые листы и возвращает {имя_листа: DataFrame}.

    Листы с outline-уровнями разбираются как иерархические отчёты 1С,
    остальные — как плоские таблицы с эвристическим поиском шапки.
    CSV обрабатывается отдельной веткой (нюх разделителя и кодировки).
    """
    if str(file_path).lower().endswith(".csv"):
        return _parse_csv(file_path)
    if str(file_path).lower().endswith(".xls"):
        return _parse_xls(file_path)

    wb = openpyxl.load_workbook(
        file_path,
        data_only=True,
        read_only=True,
        keep_links=False,
    )
    outlines = _xlsx_sheet_outlines(file_path)
    sheets: dict[str, pd.DataFrame] = {}

    try:
        for ws in wb.worksheets:
            if getattr(ws, "sheet_state", "visible") != "visible":
                continue

            row_levels = outlines.get(ws.title) or {}
            rows: list[tuple[tuple, int]] = []
            for index, values in enumerate(ws.iter_rows(values_only=True), start=1):
                if all(_is_blank(v) for v in values):
                    continue
                rows.append((values, row_levels.get(index, 0)))

            if not rows:
                continue

            has_outline = any(level > 0 for _, level in rows)
            df = _parse_grouped_rows(rows) if has_outline else None
            if df is None or df.empty:
                df = _parse_flat_rows(rows)

            if df is not None and not df.empty:
                sheets[ws.title] = df
    finally:
        wb.close()

    return sheets
