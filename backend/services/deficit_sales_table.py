"""Сводная таблица «Продажи по службам» для дефицита (вкладка Платежи / UI «Продажи»)."""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from models.dashboard_spec import Tile
from services.column_resolver import resolve_semantic_column
from services.report_profiles.deficit_profile import detect_deficit_money_layout

_ATTR_FORECAST = "deficit_sales_forecast"

_ROW_ORDER = (
    "СИО",
    "ОАПЛиС",
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


def parse_sales_forecast_vitrine(file_path: str) -> dict[str, float]:
    """Ищет на первом листе витрину со столбцом «Прогноз продаж» (если есть)."""
    path = Path(file_path)
    if path.suffix.lower() not in {".xlsx", ".xls"}:
        return {}
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception:
        return {}
    try:
        ws = wb.worksheets[0]
        rows = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 40:
                break
            rows.append(tuple(row))
    finally:
        wb.close()

    header_i = None
    col_forecast = None
    col_service = None
    for i, row in enumerate(rows):
        labels = [_norm(c) for c in row]
        if not any(labels):
            continue
        if any("служб" in label for label in labels) and any(
            "прогноз" in label and "продаж" in label for label in labels
        ):
            header_i = i
            for j, label in enumerate(labels):
                if "служб" in label:
                    col_service = j
                if "прогноз" in label and "продаж" in label:
                    col_forecast = j
            break
    if header_i is None or col_service is None or col_forecast is None:
        return {}

    out: dict[str, float] = {}
    for row in rows[header_i + 1 :]:
        if col_service >= len(row):
            continue
        name = row[col_service]
        if name is None:
            continue
        label = str(name).strip()
        if not label or _norm(label) in {"всего", "итого"}:
            break
        forecast = _to_float(row[col_forecast] if col_forecast < len(row) else None)
        if forecast is None:
            continue
        out[label] = forecast
    return out


def attach_deficit_sales_forecast(df: pd.DataFrame, file_path: str) -> pd.DataFrame:
    parsed = parse_sales_forecast_vitrine(file_path)
    if parsed:
        df.attrs[_ATTR_FORECAST] = parsed
    return df


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


def build_deficit_sales_table(df: pd.DataFrame, tile: Tile) -> dict:
    orders = _aggregate_orders(df)
    if not orders:
        return {"error": "Нет данных по подразделениям и суммам заказов"}

    forecast_map: dict[str, float] = dict(df.attrs.get(_ATTR_FORECAST) or {})
    rows: list[dict] = []
    total_forecast = 0.0
    total_count = 0.0
    total_sum = 0.0
    has_forecast = False

    for label in _ROW_ORDER:
        bucket = orders.get(label, {"count": 0.0, "sum": 0.0})
        count = bucket["count"]
        order_sum = bucket["sum"]
        forecast = forecast_map.get(label)
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

    for label, bucket in orders.items():
        if label in _ROW_ORDER:
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
        },
    }
