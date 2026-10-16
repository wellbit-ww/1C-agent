"""Сводная таблица «Продажи по службам» для дефицита (вкладка Платежи / UI «Продажи»)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from models.dashboard_spec import Tile
from services.column_resolver import resolve_semantic_column
from services.report_profiles.deficit_profile import detect_deficit_money_layout

_ATTR_FORECAST = "deficit_sales_forecast"
_ATTR_FORECAST_COMPANY = "deficit_sales_forecast_company"
_COMPANY_TOTAL_LABEL = "Совтест АТЕ"
_COMBINED_SIO_APLIS = "СИО и АПЛиС"

_ROW_ORDER_SPLIT = (
    "СИО",
    "ОАПЛиС",
    "СТО",
    "СМЭ",
    "СТЕ",
    "СООК",
    "СЕРВИС",
)
_ROW_ORDER_COMBINED = (
    _COMBINED_SIO_APLIS,
    "СТО",
    "СМЭ",
    "СТЕ",
    "СООК",
    "СЕРВИС",
)


def _norm(text: str) -> str:
    return str(text or "").lower().replace("ё", "е").strip()


def _sales_row_label(short: str) -> str:
    if short == "Сервис":
        return "СЕРВИС"
    return short


def _to_float(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    text = re.sub(r"[^\d.\-]", "", text)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _fulfillment_percent(order_sum: float, forecast: float | None) -> int | None:
    if forecast is None or forecast <= 0:
        return None
    if order_sum <= 0:
        return 0
    return int(round(100.0 * order_sum / forecast))


def _allocate_percentages(
    amounts: list[float],
    forecast: float,
    target: int | None = None,
) -> list[int]:
    """Целые % по периодам, сумма которых равна общему % (метод наибольших остатков)."""
    n = len(amounts)
    if n == 0 or forecast <= 0:
        return [0] * n
    clean = [max(0.0, float(a or 0)) for a in amounts]
    total = sum(clean)
    if target is None:
        target = int(round(100.0 * total / forecast)) if total > 0 else 0
    target = max(0, int(target))
    if total <= 0 or target == 0:
        return [0] * n

    exact = [100.0 * a / forecast for a in clean]
    floors = [int(x) for x in exact]  # truncate toward 0; amounts >= 0
    # Если из‑за округления вниз суммы floors > target — урежем
    while sum(floors) > target:
        idx = max(range(n), key=lambda i: (floors[i], exact[i] - floors[i]))
        if floors[idx] <= 0:
            break
        floors[idx] -= 1

    rem = target - sum(floors)
    order = sorted(
        range(n),
        key=lambda i: (-(exact[i] - floors[i]), -clean[i], i),
    )
    for i in order:
        if rem <= 0:
            break
        if clean[i] <= 0:
            continue
        floors[i] += 1
        rem -= 1
    # Если остались очки (все нули уже учтены) — добьём крупнейшие суммы
    if rem > 0:
        for i in sorted(range(n), key=lambda j: (-clean[j], j)):
            if rem <= 0:
                break
            floors[i] += 1
            rem -= 1
    return floors


def _normalize_forecast_label(label: str) -> str | None:
    """Приводит подписи листа «Прогноз» к ключам таблицы продаж."""
    text = str(label or "").strip()
    if not text:
        return None
    key = _norm(text).replace(" ", "").replace("+", "и").replace("-", "")
    if key in {"всего", "итого", "совтест", "совтестате", "компания"}:
        return None
    if "сио" in key and ("аплис" in key or "оаплис" in key):
        return _COMBINED_SIO_APLIS
    if key in {"сервис", "сс", "сервиснаяслужба"}:
        return "СЕРВИС"
    short = {
        "сио": "СИО",
        "оаплис": "ОАПЛиС",
        "аплис": "ОАПЛиС",
        "сто": "СТО",
        "смэ": "СМЭ",
        "сте": "СТЕ",
        "соок": "СООК",
    }.get(key)
    return short or text


def _extract_forecast_from_rows(
    rows: list[tuple],
) -> tuple[dict[str, float], dict[str, float | str] | None]:
    header_i = None
    col_forecast = None
    col_service = None
    for i, row in enumerate(rows):
        labels = [_norm(c) for c in row]
        if not any(labels):
            continue
        has_service = any("служб" in label for label in labels)
        has_forecast = any(
            "прогноз" in label and ("продаж" in label or "р." in label or label.endswith("р"))
            for label in labels
        ) or any(label.startswith("прогноз") for label in labels)
        if has_service and has_forecast:
            header_i = i
            for j, label in enumerate(labels):
                if col_service is None and "служб" in label:
                    col_service = j
                if col_forecast is None and "прогноз" in label:
                    col_forecast = j
            break
    if header_i is None or col_service is None or col_forecast is None:
        return {}, None

    out: dict[str, float] = {}
    company: dict[str, float | str] | None = None
    for row in rows[header_i + 1 :]:
        if col_service >= len(row):
            continue
        name = row[col_service]
        if name is None:
            continue
        raw_name = str(name).strip()
        mapped = _normalize_forecast_label(raw_name)
        if mapped is None:
            key = _norm(raw_name).replace(" ", "")
            if key in {"совтест", "совтестате", "всего", "итого"} or key.startswith("совтест"):
                forecast = _to_float(row[col_forecast] if col_forecast < len(row) else None)
                if forecast is not None:
                    label = raw_name if "ате" in _norm(raw_name) else _COMPANY_TOTAL_LABEL
                    company = {"label": label, "forecast": forecast}
                break
            continue
        forecast = _to_float(row[col_forecast] if col_forecast < len(row) else None)
        if forecast is None:
            continue
        out[mapped] = forecast
    return out, company


def parse_sales_forecast_vitrine(file_path: str) -> dict[str, float]:
    """Ищет витрину «Службы × Прогноз продаж» на любом листе (часто «Прогноз»)."""
    parsed, _ = parse_sales_forecast_sheet(file_path)
    return parsed


def parse_sales_forecast_sheet(
    file_path: str,
) -> tuple[dict[str, float], dict[str, float | str] | None]:
    """Прогноз по службам и строка компании (Совтест) с листа «Прогноз»."""
    path = Path(file_path)
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return {}, None
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception:
        return {}, None

    try:
        sheets = sorted(
            wb.worksheets,
            key=lambda ws: (0 if "прогноз" in _norm(ws.title) else 1, ws.title),
        )
        for ws in sheets:
            rows: list[tuple] = []
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i > 60:
                    break
                rows.append(tuple(row))
            parsed, company = _extract_forecast_from_rows(rows)
            if parsed:
                return parsed, company
    finally:
        wb.close()
    return {}, None


def attach_deficit_sales_forecast(df: pd.DataFrame, file_path: str) -> pd.DataFrame:
    parsed, company = parse_sales_forecast_sheet(file_path)
    if parsed:
        df.attrs[_ATTR_FORECAST] = parsed
    if company:
        df.attrs[_ATTR_FORECAST_COMPANY] = company
    return df


def _has_combined_sio_forecast(
    vitrine: dict[str, float], manual: dict[str, float]
) -> bool:
    return _COMBINED_SIO_APLIS in vitrine or _COMBINED_SIO_APLIS in manual


def _display_row_order(
    vitrine: dict[str, float], manual: dict[str, float]
) -> tuple[str, ...]:
    if _has_combined_sio_forecast(vitrine, manual):
        return _ROW_ORDER_COMBINED
    return _ROW_ORDER_SPLIT


def _order_id_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = _norm(col)
        if name == "заказ клиента" or name.startswith("заказ клиента"):
            return str(col)
    return None


_ORDER_DATE_SKIP = ("аттестац", "пнр", "поставк", "отгрузк", "очередн")
_ORDER_DATE_PREF = (
    "дата заказа",
    "дата регистрации",
    "дата реализации",
    "дата оплаты",
)


def _parse_dates_from_order_text(series: pd.Series) -> pd.Series:
    """Достаёт дату из «Заказ клиента … от 24.07.2026 …»."""
    extracted = (
        series.astype(str)
        .str.extract(r"от\s+(\d{1,2}[./]\d{1,2}[./]\d{2,4})", expand=False)
    )
    return pd.to_datetime(extracted, dayfirst=True, errors="coerce")


def _resolve_order_date_series(df: pd.DataFrame) -> pd.Series | None:
    """Дата регистрации заказа в 1С: из текста заказа или подходящей колонки."""
    oid = _order_id_column(df)
    if oid and oid in df.columns:
        parsed = _parse_dates_from_order_text(df[oid])
        dated = int(parsed.notna().sum())
        if dated > 0 and dated >= max(1, int(len(df) * 0.5)):
            return parsed

    by_name = {_norm(c): str(c) for c in df.columns}
    for pref in _ORDER_DATE_PREF:
        col = by_name.get(pref)
        if not col:
            continue
        parsed = pd.to_datetime(df[col], dayfirst=True, errors="coerce")
        if int(parsed.notna().sum()) > 0:
            return parsed

    from services import data_tools

    for col in data_tools.detect_date_columns(df).get("columns") or []:
        name = _norm(col)
        if any(skip in name for skip in _ORDER_DATE_SKIP):
            continue
        parsed = pd.to_datetime(df[col], dayfirst=True, errors="coerce")
        if int(parsed.notna().sum()) > 0:
            return parsed
    return None


def _sales_column_mode(tile: Tile | None) -> str:
    """Гранулярность колонок по всему файлу: month / quarter / half."""
    from services.dashboard_engine import _deals_zk_bucket_mode

    return _deals_zk_bucket_mode(tile) if tile is not None else "quarter"


def _period_range_keys(dates: pd.Series, mode: str) -> list:
    """Все периоды от первой до последней даты в файле (включая пустые)."""
    valid = dates.dropna()
    if valid.empty:
        return []
    start = valid.min()
    end = valid.max()
    if mode == "month":
        return list(pd.period_range(start.to_period("M"), end.to_period("M"), freq="M"))
    if mode == "quarter":
        return list(pd.period_range(start.to_period("Q"), end.to_period("Q"), freq="Q"))
    # half-years as (year, half) tuples, contiguous
    keys: list[tuple[int, int]] = []
    y, m = int(start.year), int(start.month)
    end_y, end_m = int(end.year), int(end.month)
    cur = (y, 1 if m <= 6 else 2)
    last = (end_y, 1 if end_m <= 6 else 2)
    while cur <= last:
        keys.append(cur)
        year, half = cur
        cur = (year + 1, 1) if half == 2 else (year, 2)
    return keys


def _period_column_label(bucket, mode: str) -> str:
    from services.data_tools import _period_axis_label

    if mode == "month":
        return _period_axis_label(bucket, "month")
    if mode == "quarter":
        return _period_axis_label(bucket, "quarter")
    if isinstance(bucket, tuple) and len(bucket) == 2:
        return f"{bucket[1]} полуг. {bucket[0]}"
    return str(bucket)


def _part_order_stats(part: pd.DataFrame, order_col: str, oid: str | None) -> dict[str, float]:
    if oid and oid in part.columns:
        ids = (
            part[oid]
            .dropna()
            .astype(str)
            .str.strip()
            .replace({"nan": "", "None": ""})
        )
        count = float(ids[ids != ""].nunique())
    else:
        count = float(len(part))
    total = pd.to_numeric(part[order_col], errors="coerce").sum()
    return {
        "count": count,
        "sum": 0.0 if pd.isna(total) else float(total),
    }


def _aggregate_orders_by_periods(
    df: pd.DataFrame, tile: Tile | None = None
) -> dict:
    """Заказы по службам; колонки = все периоды файла с выбранной гранулярностью."""
    from services.dashboard_engine import _deals_zk_bucket_key, _department_labels

    layout = detect_deficit_money_layout(df)
    order_col = layout.order_sum
    dept_col = resolve_semantic_column(df, "", semantic="department", dtype="categorical")
    if not dept_col or not order_col or dept_col not in df.columns:
        return {"periods": [], "by_dept": {}}

    work = df.copy()
    blank = work[dept_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    work = work.loc[~blank]
    if work.empty:
        return {"periods": [], "by_dept": {}}

    shorts = _department_labels(work[dept_col].astype(str).str.strip())
    work["_dept"] = shorts.map(_sales_row_label)
    oid = _order_id_column(df)
    dates = _resolve_order_date_series(work)
    mode = _sales_column_mode(tile)

    period_keys: list = []
    if dates is not None:
        work = work.copy()
        work["_order_date"] = dates
        dated = work["_order_date"].notna()
        if dated.any():
            period_keys = _period_range_keys(work.loc[dated, "_order_date"], mode)
            work.loc[dated, "_period"] = _deals_zk_bucket_key(
                work.loc[dated, "_order_date"], mode
            )

    periods_meta = [
        {"key": key, "label": _period_column_label(key, mode)} for key in period_keys
    ]

    by_dept: dict[str, dict] = {}
    for dept, part in work.groupby("_dept", sort=False):
        label = str(dept)
        total = _part_order_stats(part, order_col, oid)
        per_period: dict[str, dict[str, float]] = {}
        if period_keys and "_period" in part.columns:
            for key in period_keys:
                slot = part.loc[part["_period"] == key]
                per_period[str(key)] = (
                    _part_order_stats(slot, order_col, oid)
                    if not slot.empty
                    else {"count": 0.0, "sum": 0.0}
                )
        by_dept[label] = {"total": total, "periods": per_period}

    return {
        "periods": periods_meta,
        "by_dept": by_dept,
        "child_mode": mode,
    }


def _aggregate_orders(df: pd.DataFrame, tile: Tile | None = None) -> dict[str, dict[str, float]]:
    """Совместимость: итоги по службам (сумма всех видимых подпериодов)."""
    packed = _aggregate_orders_by_periods(df, tile)
    return {
        label: dict(info["total"])
        for label, info in (packed.get("by_dept") or {}).items()
    }


def _orders_matrix_for_display(
    packed: dict, row_order: tuple[str, ...]
) -> dict[str, dict]:
    by_dept = dict(packed.get("by_dept") or {})
    periods = list(packed.get("periods") or [])
    if _COMBINED_SIO_APLIS not in row_order:
        return by_dept
    sio = by_dept.get("СИО", {"total": {"count": 0.0, "sum": 0.0}, "periods": {}})
    apl = by_dept.get("ОАПЛиС", {"total": {"count": 0.0, "sum": 0.0}, "periods": {}})
    merged_periods: dict[str, dict[str, float]] = {}
    for meta in periods:
        key = str(meta["key"])
        a = sio.get("periods", {}).get(key, {"count": 0.0, "sum": 0.0})
        b = apl.get("periods", {}).get(key, {"count": 0.0, "sum": 0.0})
        merged_periods[key] = {
            "count": float(a.get("count", 0) or 0) + float(b.get("count", 0) or 0),
            "sum": float(a.get("sum", 0) or 0) + float(b.get("sum", 0) or 0),
        }
    out = dict(by_dept)
    out[_COMBINED_SIO_APLIS] = {
        "total": {
            "count": float(sio.get("total", {}).get("count", 0) or 0)
            + float(apl.get("total", {}).get("count", 0) or 0),
            "sum": float(sio.get("total", {}).get("sum", 0) or 0)
            + float(apl.get("total", {}).get("sum", 0) or 0),
        },
        "periods": merged_periods,
    }
    return out


def _forecast_for_label(
    label: str,
    vitrine: dict[str, float],
    manual: dict[str, float],
) -> float | None:
    if label in manual:
        return manual[label]
    return vitrine.get(label)


def _as_count(value: float) -> float | int:
    return int(value) if value == int(value) else value


def build_deficit_sales_table(df: pd.DataFrame, tile: Tile) -> dict:
    packed = _aggregate_orders_by_periods(df, tile)
    by_dept_raw = packed.get("by_dept") or {}
    if not by_dept_raw:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display = _orders_matrix_for_display(packed, row_order)
    periods = list(packed.get("periods") or [])

    # Один период — без отдельного «Всего за период» (дубль единственной колонки).
    show_period_total = len(periods) != 1

    columns = ["Прогноз продаж, р."]
    column_kinds: list[str] = ["money"]
    year_spans: list[dict] = [{"label": "", "count": 1}]
    for meta in periods:
        columns.extend(["Количество", "Общая сумма"])
        column_kinds.extend(["count", "money"])
        year_spans.append({"label": meta["label"], "count": 2})
    if show_period_total:
        columns.extend(["Количество", "Общая сумма"])
        column_kinds.extend(["count", "money"])
        year_spans.append(
            {
                "label": "Всего за период" if periods else "Заказы, зарегистрированные в 1С",
                "count": 2,
            }
        )
    columns.append("Выполнение прогноза")
    column_kinds.append("percent")
    year_spans.append({"label": "", "count": 1})

    fulfill_col = len(columns) - 1
    if show_period_total:
        order_count_col = fulfill_col - 2
        order_sum_col = fulfill_col - 1
    elif periods:
        order_count_col = 1
        order_sum_col = 2
    else:
        order_count_col = fulfill_col - 2
        order_sum_col = fulfill_col - 1

    rows: list[dict] = []
    totals = [0.0] * len(columns)
    total_forecast = 0.0
    has_forecast = False
    shown = set(row_order)
    period_totals = [{"count": 0.0, "sum": 0.0} for _ in periods]

    def _append_row(label: str, info: dict, forecast: float | None) -> None:
        nonlocal total_forecast, has_forecast
        total = info.get("total") or {"count": 0.0, "sum": 0.0}
        count = float(total.get("count", 0) or 0)
        order_sum = float(total.get("sum", 0) or 0)
        values: list = [forecast if forecast is not None else 0]
        for i, meta in enumerate(periods):
            slot = (info.get("periods") or {}).get(str(meta["key"]), {"count": 0.0, "sum": 0.0})
            values.append(_as_count(float(slot.get("count", 0) or 0)))
            values.append(float(slot.get("sum", 0) or 0))
            period_totals[i]["count"] += float(slot.get("count", 0) or 0)
            period_totals[i]["sum"] += float(slot.get("sum", 0) or 0)
        if show_period_total:
            values.append(_as_count(count))
            values.append(order_sum)
        pct = _fulfillment_percent(order_sum, forecast if forecast and forecast > 0 else None)
        values.append(pct if pct is not None else None)
        rows.append({"label": label, "values": values})
        if forecast is not None and forecast > 0:
            has_forecast = True
            total_forecast += forecast
        totals[order_count_col] = float(totals[order_count_col] or 0) + count
        totals[order_sum_col] = float(totals[order_sum_col] or 0) + order_sum

    for label in row_order:
        info = display.get(label, {"total": {"count": 0.0, "sum": 0.0}, "periods": {}})
        forecast = _forecast_for_label(label, vitrine, manual)
        _append_row(label, info, forecast)

    skip_when_combined = {"СИО", "ОАПЛиС"} if _COMBINED_SIO_APLIS in row_order else set()
    for label, info in (packed.get("by_dept") or {}).items():
        if label in shown or label in skip_when_combined:
            continue
        total = info.get("total") or {}
        if not total.get("count") and not total.get("sum"):
            continue
        _append_row(label, info, None)

    totals[0] = total_forecast if has_forecast else 0
    for i, slot in enumerate(period_totals):
        totals[1 + i * 2] = _as_count(slot["count"])
        totals[2 + i * 2] = slot["sum"]
    if show_period_total:
        totals[order_count_col] = _as_count(float(totals[order_count_col] or 0))
    totals[fulfill_col] = _fulfillment_percent(
        float(totals[order_sum_col] or 0),
        total_forecast if has_forecast else None,
    )

    return {
        "groups": {
            row["label"]: float(row["values"][order_sum_col] or 0) for row in rows
        },
        "table": {
            "index_label": "Службы",
            "columns": columns,
            "column_kinds": column_kinds,
            "year_spans": year_spans,
            "rows": rows,
            "totals": totals,
            "totals_label": "Всего",
            "forecast_column_index": 0,
            "order_sum_column_index": order_sum_col,
            "order_count_column_index": order_count_col,
            "fulfill_column_index": fulfill_col,
        },
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }


def build_deficit_sales_forecast_vitrine(df: pd.DataFrame, tile: Tile) -> dict:
    """Компактная таблица: прогноз + суммы по подпериодам + итог + %."""
    packed = _aggregate_orders_by_periods(df, tile)
    by_dept_raw = packed.get("by_dept") or {}
    if not by_dept_raw:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display = _orders_matrix_for_display(packed, row_order)
    periods = list(packed.get("periods") or [])

    show_period_total = len(periods) != 1

    columns = ["Прогноз продаж, р."]
    column_kinds: list[str] = ["money"]
    year_spans: list[dict] = [{"label": "", "count": 1}]
    for meta in periods:
        columns.append(meta["label"])
        column_kinds.append("money")
        year_spans.append({"label": meta["label"], "count": 1})
    if show_period_total:
        columns.append("Суммы заказов, р.")
        column_kinds.append("money")
        year_spans.append({"label": "Всего", "count": 1})
    columns.append("Выполнение прогноза")
    column_kinds.append("percent")
    year_spans.append({"label": "", "count": 1})

    fulfill_col = len(columns) - 1
    order_sum_col = fulfill_col - 1 if show_period_total else (1 if periods else fulfill_col - 1)

    rows: list[dict] = []
    totals = [0.0] * len(columns)
    service_forecast_sum = 0.0
    has_service_forecast = False
    period_sums = [0.0] * len(periods)

    for label in row_order:
        info = display.get(label, {"total": {"count": 0.0, "sum": 0.0}, "periods": {}})
        order_sum = float((info.get("total") or {}).get("sum", 0) or 0)
        forecast = _forecast_for_label(label, vitrine, manual)
        values: list = [forecast if forecast is not None else 0]
        for i, meta in enumerate(periods):
            slot_sum = float(
                (info.get("periods") or {}).get(str(meta["key"]), {}).get("sum", 0) or 0
            )
            values.append(slot_sum)
            period_sums[i] += slot_sum
        if show_period_total:
            values.append(order_sum)
        pct = _fulfillment_percent(order_sum, forecast if forecast and forecast > 0 else None)
        values.append(pct if pct is not None else None)
        rows.append({"label": label, "values": values})
        if forecast is not None and forecast > 0:
            has_service_forecast = True
            service_forecast_sum += forecast
        totals[order_sum_col] = float(totals[order_sum_col] or 0) + order_sum

    company_meta = dict(df.attrs.get(_ATTR_FORECAST_COMPANY) or {})
    total_forecast = company_meta.get("forecast")
    if total_forecast is None and has_service_forecast:
        total_forecast = service_forecast_sum
    total_forecast_f = float(total_forecast) if total_forecast is not None else None
    all_order_sum = float(totals[order_sum_col] or 0)
    totals[0] = total_forecast_f if total_forecast_f is not None else 0
    for i, value in enumerate(period_sums):
        totals[1 + i] = value
    totals[fulfill_col] = _fulfillment_percent(
        all_order_sum,
        total_forecast_f if total_forecast_f and total_forecast_f > 0 else None,
    )
    totals_label = str(company_meta.get("label") or _COMPANY_TOTAL_LABEL)

    return {
        "groups": {row["label"]: float(row["values"][order_sum_col] or 0) for row in rows},
        "table": {
            "index_label": "Службы",
            "columns": columns,
            "column_kinds": column_kinds,
            "year_spans": year_spans if periods else None,
            "rows": rows,
            "totals": totals,
            "totals_label": totals_label,
            "totals_emphasis": True,
            "forecast_column_index": 0,
            "order_sum_column_index": order_sum_col,
            "fulfill_column_index": fulfill_col,
        },
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }


def build_deficit_sales_fulfillment_board(df: pd.DataFrame, tile: Tile) -> dict:
    """Компактный рейтинг: компания + службы с % выполнения (как на листе прогноза)."""
    data = build_deficit_sales_forecast_vitrine(df, tile)
    if "error" in data:
        return data
    table = data["table"]
    fulfill_col = int(table.get("fulfill_column_index") or len(table.get("columns") or []) - 1)
    totals = list(table.get("totals") or [])
    company_pct = totals[fulfill_col] if len(totals) > fulfill_col else None
    company_pct_i = int(company_pct) if company_pct is not None else None
    company_label = str(table.get("totals_label") or _COMPANY_TOTAL_LABEL)

    rows: list[dict] = []
    if company_pct_i is not None:
        rows.append(
            {
                "label": company_label,
                "values": [company_pct_i],
                "emphasis": True,
            }
        )

    for row in table.get("rows") or []:
        values = list(row.get("values") or [])
        pct = values[fulfill_col] if len(values) > fulfill_col else None
        if pct is None:
            continue
        rows.append(
            {
                "label": row["label"],
                "values": [int(pct)],
            }
        )

    if not rows:
        return {"error": "Нет данных по выполнению прогноза"}

    return {
        "groups": {row["label"]: float(row["values"][0] or 0) for row in rows},
        "table": {
            "index_label": "Подразделение",
            "columns": ["Выполнение прогноза"],
            "column_kinds": ["percent"],
            "rows": rows,
        },
        "bucket_period": data.get("bucket_period"),
    }


def build_deficit_sales_share_table(df: pd.DataFrame, tile: Tile) -> dict:
    """Суммы заказов по подпериодам и доля в продажах + строка компании."""
    packed = _aggregate_orders_by_periods(df, tile)
    by_dept_raw = packed.get("by_dept") or {}
    if not by_dept_raw:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display = _orders_matrix_for_display(packed, row_order)
    periods = list(packed.get("periods") or [])

    show_period_total = len(periods) != 1
    columns = [meta["label"] for meta in periods]
    column_kinds = ["money"] * len(periods)
    year_spans = [{"label": meta["label"], "count": 1} for meta in periods]
    if show_period_total:
        columns.append("Суммы заказов, р.")
        column_kinds.append("money")
        year_spans.append({"label": "Всего", "count": 1})
    columns.append("Доля в продажах")
    column_kinds.append("percent1")
    year_spans.append({"label": "", "count": 1})

    share_col = len(columns) - 1
    order_sum_col = share_col - 1 if show_period_total else (0 if periods else share_col - 1)

    total_sum = sum(
        float((info.get("total") or {}).get("sum", 0) or 0)
        for info in by_dept_raw.values()
    )
    rows: list[dict] = []
    totals = [0.0] * len(columns)
    period_sums = [0.0] * len(periods)
    shown = set(row_order)
    skip_when_combined = {"СИО", "ОАПЛиС"} if _COMBINED_SIO_APLIS in row_order else set()

    def _append(label: str, info: dict) -> None:
        order_sum = float((info.get("total") or {}).get("sum", 0) or 0)
        values: list = []
        for i, meta in enumerate(periods):
            slot_sum = float(
                (info.get("periods") or {}).get(str(meta["key"]), {}).get("sum", 0) or 0
            )
            values.append(slot_sum)
            period_sums[i] += slot_sum
        share = round(100.0 * order_sum / total_sum, 1) if total_sum > 0 else 0.0
        if show_period_total:
            values.append(order_sum)
        values.append(share)
        rows.append({"label": label, "values": values})
        totals[order_sum_col] = float(totals[order_sum_col] or 0) + order_sum

    for label in row_order:
        _append(label, display.get(label, {"total": {"sum": 0.0}, "periods": {}}))
    for label, info in by_dept_raw.items():
        if label in shown or label in skip_when_combined:
            continue
        if not (info.get("total") or {}).get("sum"):
            continue
        _append(label, info)

    for i, value in enumerate(period_sums):
        totals[i] = value
    totals[share_col] = 100.0 if total_sum > 0 else 0.0
    company_meta = dict(df.attrs.get(_ATTR_FORECAST_COMPANY) or {})
    totals_label = str(company_meta.get("label") or _COMPANY_TOTAL_LABEL)

    return {
        "groups": {row["label"]: float(row["values"][order_sum_col] or 0) for row in rows},
        "table": {
            "index_label": "Подразделение",
            "columns": columns,
            "column_kinds": column_kinds,
            "year_spans": year_spans if periods else None,
            "rows": rows,
            "totals": totals,
            "totals_label": totals_label,
            "totals_emphasis": True,
            "order_sum_column_index": order_sum_col,
        },
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }


def _period_money_columns(table: dict) -> list[tuple[str, int]]:
    """Подписи и индексы денежных колонок периодов (после прогноза, до итога/%)."""
    spans = list(table.get("year_spans") or [])
    kinds = list(table.get("column_kinds") or [])
    skip = {
        "",
        "всего",
        "всего за период",
        "заказы, зарегистрированные в 1с",
    }
    out: list[tuple[str, int]] = []
    idx = 0
    for span in spans:
        label = str(span.get("label") or "").strip()
        count = int(span.get("count") or 0)
        key = label.lower()
        if label and key not in skip:
            if count == 2:
                # Количество + Общая сумма
                out.append((label, idx + 1))
            elif count == 1 and idx < len(kinds) and kinds[idx] == "money":
                out.append((label, idx))
        idx += count
    return out


def build_deficit_sales_summary(df: pd.DataFrame, tile: Tile) -> dict:
    """Итог: прогноз + сумма заказов; при нескольких периодах — разбивка суммы."""
    data = build_deficit_sales_table(df, tile)
    if "error" in data:
        return data
    table = data["table"]
    totals = list(table.get("totals") or [])
    forecast_col = int(table.get("forecast_column_index") or 0)
    order_sum_col = int(
        table.get("order_sum_column_index")
        if table.get("order_sum_column_index") is not None
        else 2
    )
    forecast = float(totals[forecast_col] or 0) if len(totals) > forecast_col else 0.0
    order_sum = float(totals[order_sum_col] or 0) if len(totals) > order_sum_col else 0.0
    pct = _fulfillment_percent(order_sum, forecast if forecast > 0 else None)

    period_cols = _period_money_columns(table)
    period_amounts = [
        float(totals[col_i] or 0) if len(totals) > col_i else 0.0
        for _label, col_i in period_cols
    ]
    period_pcts = (
        _allocate_percentages(period_amounts, forecast, target=pct)
        if forecast > 0 and pct is not None
        else [0] * len(period_amounts)
    )
    period_bars: list[dict] = [
        {"label": label, "order_sum": amount, "pct": bar_pct}
        for (label, _col_i), amount, bar_pct in zip(
            period_cols, period_amounts, period_pcts
        )
    ]

    columns = ["Прогноз продаж, р."]
    column_kinds = ["money"]
    values: list = [forecast]
    if len(period_bars) > 1:
        for bar in period_bars:
            columns.append(bar["label"])
            column_kinds.append("money")
            values.append(bar["order_sum"])
    else:
        columns.append("Общая сумма заказов, р.")
        column_kinds.append("money")
        values.append(order_sum)
    columns.append("Выполнение прогноза")
    column_kinds.append("percent")
    values.append(pct)

    return {
        "groups": {"Всего": order_sum},
        "table": {
            "columns": columns,
            "column_kinds": column_kinds,
            "rows": [{"label": "", "values": values}],
        },
        "period_bars": period_bars,
        "forecast": forecast,
        "order_sum": order_sum,
        "fulfill_pct": pct,
        "bucket_period": data.get("bucket_period"),
    }
