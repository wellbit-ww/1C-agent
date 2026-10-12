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


def _orders_for_display(
    orders: dict[str, dict[str, float]], row_order: tuple[str, ...]
) -> dict[str, dict[str, float]]:
    if _COMBINED_SIO_APLIS not in row_order:
        return orders
    out = dict(orders)
    sio = orders.get("СИО", {"count": 0.0, "sum": 0.0})
    apl = orders.get("ОАПЛиС", {"count": 0.0, "sum": 0.0})
    out[_COMBINED_SIO_APLIS] = {
        "count": float(sio.get("count", 0) or 0) + float(apl.get("count", 0) or 0),
        "sum": float(sio.get("sum", 0) or 0) + float(apl.get("sum", 0) or 0),
    }
    return out


def _order_id_column(df: pd.DataFrame) -> str | None:
    for col in df.columns:
        name = _norm(col)
        if name == "заказ клиента" or name.startswith("заказ клиента"):
            return str(col)
    return None


def _aggregate_orders(df: pd.DataFrame) -> dict[str, dict[str, float]]:
    layout = detect_deficit_money_layout(df)
    order_col = layout.order_sum
    dept_col = resolve_semantic_column(df, "", semantic="department", dtype="categorical")
    if not dept_col or not order_col or dept_col not in df.columns:
        return {}

    from services.dashboard_engine import _department_labels

    work = df.copy()
    blank = work[dept_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    work = work.loc[~blank]
    if work.empty:
        return {}

    shorts = _department_labels(work[dept_col].astype(str).str.strip())
    work["_bucket"] = shorts.map(_sales_row_label)
    oid = _order_id_column(df)

    buckets: dict[str, dict[str, float]] = {}
    for bucket, part in work.groupby("_bucket", sort=False):
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
        buckets[str(bucket)] = {
            "count": count,
            "sum": 0.0 if pd.isna(total) else float(total),
        }
    return buckets


def _forecast_for_label(
    label: str,
    vitrine: dict[str, float],
    manual: dict[str, float],
) -> float | None:
    if label in manual:
        return manual[label]
    return vitrine.get(label)


def build_deficit_sales_table(df: pd.DataFrame, tile: Tile) -> dict:
    orders = _aggregate_orders(df)
    if not orders:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display_orders = _orders_for_display(orders, row_order)
    rows: list[dict] = []
    total_forecast = 0.0
    total_count = 0.0
    total_sum = 0.0
    has_forecast = False
    shown = set(row_order)

    for label in row_order:
        bucket = display_orders.get(label, {"count": 0.0, "sum": 0.0})
        count = bucket["count"]
        order_sum = bucket["sum"]
        forecast = _forecast_for_label(label, vitrine, manual)
        if forecast is not None and forecast > 0:
            has_forecast = True
        pct = _fulfillment_percent(order_sum, forecast if forecast and forecast > 0 else None)
        rows.append(
            {
                "label": label,
                "values": [
                    forecast if forecast is not None else 0,
                    int(count) if count == int(count) else count,
                    order_sum,
                    pct if pct is not None else None,
                ],
            }
        )
        if forecast is not None and forecast > 0:
            total_forecast += forecast
        total_count += count
        total_sum += order_sum

    # Extra departments not in the fixed row order
    skip_when_combined = {"СИО", "ОАПЛиС"} if _COMBINED_SIO_APLIS in row_order else set()
    for label, bucket in orders.items():
        if label in shown or label in skip_when_combined:
            continue
        if not bucket["count"] and not bucket["sum"]:
            continue
        rows.append(
            {
                "label": label,
                "values": [
                    0,
                    int(bucket["count"])
                    if bucket["count"] == int(bucket["count"])
                    else bucket["count"],
                    bucket["sum"],
                    None,
                ],
            }
        )

    total_pct = _fulfillment_percent(total_sum, total_forecast if has_forecast else None)
    totals = [
        total_forecast if has_forecast else total_sum and 0 or 0,
        int(total_count) if total_count == int(total_count) else total_count,
        total_sum,
        total_pct,
    ]
    if not has_forecast:
        totals[0] = 0

    return {
        "groups": {row["label"]: float(row["values"][2] or 0) for row in rows},
        "table": {
            "index_label": "Службы",
            "columns": [
                "Прогноз продаж, р.",
                "Количество",
                "Общая сумма",
                "Выполнение прогноза",
            ],
            "column_kinds": ["money", "count", "money", "percent"],
            "year_spans": [
                {"label": "", "count": 1},
                {"label": "Заказы, зарегистрированные в 1С", "count": 2},
                {"label": "", "count": 1},
            ],
            "rows": rows,
            "totals": totals,
            "totals_label": "Всего",
            "forecast_column_index": 0,
        },
    }


def build_deficit_sales_forecast_vitrine(df: pd.DataFrame, tile: Tile) -> dict:
    """Компактная таблица как на листе «Прогноз»: прогноз, суммы заказов, % + строка компании."""
    orders = _aggregate_orders(df)
    if not orders:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display_orders = _orders_for_display(orders, row_order)

    rows: list[dict] = []
    service_forecast_sum = 0.0
    has_service_forecast = False
    for label in row_order:
        bucket = display_orders.get(label, {"count": 0.0, "sum": 0.0})
        order_sum = bucket["sum"]
        forecast = _forecast_for_label(label, vitrine, manual)
        if forecast is not None and forecast > 0:
            has_service_forecast = True
            service_forecast_sum += forecast
        pct = _fulfillment_percent(order_sum, forecast if forecast and forecast > 0 else None)
        rows.append(
            {
                "label": label,
                "values": [
                    forecast if forecast is not None else 0,
                    order_sum,
                    pct if pct is not None else None,
                ],
            }
        )

    company_meta = dict(df.attrs.get(_ATTR_FORECAST_COMPANY) or {})
    total_forecast = company_meta.get("forecast")
    if total_forecast is None and has_service_forecast:
        total_forecast = service_forecast_sum
    total_forecast_f = float(total_forecast) if total_forecast is not None else None

    all_order_sum = sum(float(b.get("sum") or 0) for b in orders.values())
    total_pct = _fulfillment_percent(
        all_order_sum,
        total_forecast_f if total_forecast_f and total_forecast_f > 0 else None,
    )
    totals_label = str(company_meta.get("label") or _COMPANY_TOTAL_LABEL)

    return {
        "groups": {row["label"]: float(row["values"][1] or 0) for row in rows},
        "table": {
            "index_label": "Службы",
            "columns": [
                "Прогноз продаж, р.",
                "Суммы заказов, р.",
                "Выполнение прогноза",
            ],
            "column_kinds": ["money", "money", "percent"],
            "rows": rows,
            "totals": [
                total_forecast_f if total_forecast_f is not None else 0,
                all_order_sum,
                total_pct,
            ],
            "totals_label": totals_label,
            "totals_emphasis": True,
        },
    }


def build_deficit_sales_fulfillment_board(df: pd.DataFrame, tile: Tile) -> dict:
    """Компактный рейтинг: компания + службы с % выполнения (как на листе прогноза)."""
    data = build_deficit_sales_forecast_vitrine(df, tile)
    if "error" in data:
        return data
    table = data["table"]
    totals = list(table.get("totals") or [0, 0, None])
    company_pct = totals[2] if len(totals) > 2 else None
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
        pct = values[2] if len(values) > 2 else None
        if pct is None:
            continue
        pct_i = int(pct)
        highlight = company_pct_i is not None and pct_i > company_pct_i
        rows.append(
            {
                "label": row["label"],
                "values": [pct_i],
                "highlight": highlight,
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
    }


def build_deficit_sales_share_table(df: pd.DataFrame, tile: Tile) -> dict:
    """Суммы заказов и доля в продажах по службам + строка компании."""
    orders = _aggregate_orders(df)
    if not orders:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    vitrine: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    manual: dict[str, float] = dict(tile.source.sales_forecasts or {})
    row_order = _display_row_order(vitrine, manual)
    display_orders = _orders_for_display(orders, row_order)

    total_sum = sum(float(b.get("sum") or 0) for b in orders.values())
    rows: list[dict] = []
    for label in row_order:
        order_sum = float(display_orders.get(label, {}).get("sum") or 0)
        share = round(100.0 * order_sum / total_sum, 1) if total_sum > 0 else 0.0
        rows.append({"label": label, "values": [order_sum, share]})

    skip_when_combined = {"СИО", "ОАПЛиС"} if _COMBINED_SIO_APLIS in row_order else set()
    shown = set(row_order)
    for label, bucket in orders.items():
        if label in shown or label in skip_when_combined:
            continue
        order_sum = float(bucket.get("sum") or 0)
        if not order_sum:
            continue
        share = round(100.0 * order_sum / total_sum, 1) if total_sum > 0 else 0.0
        rows.append({"label": label, "values": [order_sum, share]})

    company_meta = dict(df.attrs.get(_ATTR_FORECAST_COMPANY) or {})
    totals_label = str(company_meta.get("label") or _COMPANY_TOTAL_LABEL)

    return {
        "groups": {row["label"]: float(row["values"][0] or 0) for row in rows},
        "table": {
            "index_label": "Подразделение",
            "columns": ["Суммы заказов, р.", "Доля в продажах"],
            "column_kinds": ["money", "percent1"],
            "rows": rows,
            "totals": [total_sum, 100.0 if total_sum > 0 else 0.0],
            "totals_label": totals_label,
            "totals_emphasis": True,
        },
    }


def build_deficit_sales_summary(df: pd.DataFrame, tile: Tile) -> dict:
    """Итог: прогноз = сумма прогнозов по службам (из большой таблицы / витрины)."""
    data = build_deficit_sales_table(df, tile)
    if "error" in data:
        return data
    table = data["table"]
    totals = table.get("totals") or [0, 0, 0, None]
    forecast = float(totals[0] or 0)
    order_sum = float(totals[2] or 0)
    pct = _fulfillment_percent(order_sum, forecast if forecast > 0 else None)
    return {
        "groups": {"Всего": order_sum},
        "table": {
            "columns": [
                "Прогноз продаж, р.",
                "Общая сумма заказов, р.",
                "Выполнение прогноза",
            ],
            "column_kinds": ["money", "money", "percent"],
            "rows": [
                {
                    "label": "",
                    "values": [forecast, order_sum, pct],
                }
            ],
        },
    }
