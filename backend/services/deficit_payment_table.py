"""Сводка «Поступление оплаты» для вкладки «Оплата» (дефицит).

План — лист 3 книги (часто «План», колонка «План поступлений, р.»).
Факт — сумма «Оплачено по заказу» по таблице «Дефицит» (первый лист),
с разбивкой по периодам (месяц / квартал / полугодие) как на «Продажах».
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from models.dashboard_spec import Tile
from services.column_resolver import resolve_semantic_column
from services.deficit_sales_table import (
    _COMPANY_TOTAL_LABEL,
    _ROW_ORDER_SPLIT,
    _fulfillment_percent,
    _normalize_forecast_label,
    _norm,
    _order_id_column,
    _part_order_stats,
    _period_column_label,
    _period_range_keys,
    _resolve_order_date_series,
    _sales_column_mode,
    _sales_row_label,
    _to_float,
)
from services.report_profiles.deficit_profile import _col_sum, detect_deficit_money_layout

_ATTR_PAYMENT_PLAN = "deficit_payment_plan"
_ATTR_PAYMENT_PLAN_COMPANY = "deficit_payment_plan_company"
_COMPANY_MARKERS = ("совтест", "итого", "всего")


def _pick_plan_worksheet(workbook) -> object | None:
    if len(workbook.worksheets) >= 3:
        return workbook.worksheets[2]
    for ws in workbook.worksheets:
        if "план" in _norm(ws.title):
            return ws
    return None


def _extract_plan_from_rows(rows: list[tuple]) -> tuple[dict[str, float], float | None]:
    header_row = -1
    col_service = col_plan = None
    for i, row in enumerate(rows):
        labels = [_norm(str(c or "")) for c in row]
        if not any(labels):
            continue
        for j, label in enumerate(labels):
            if "план" in label and "поступ" in label:
                header_row = i
                col_plan = j
            if col_service is None and ("служб" in label or label in {"подразделение", "подразделения"}):
                col_service = j
        if header_row >= 0 and col_plan is not None:
            break
    if header_row < 0 or col_plan is None:
        return {}, None

    if col_service is None:
        col_service = col_plan - 1 if col_plan > 0 else 0

    by_dept: dict[str, float] = {}
    company_total: float | None = None
    for row in rows[header_row + 1 :]:
        if col_service >= len(row) or col_plan >= len(row):
            continue
        raw_name = str(row[col_service] or "").strip()
        if not raw_name:
            continue
        amount = _to_float(row[col_plan])
        if amount is None:
            continue
        key = _norm(raw_name)
        if any(marker in key for marker in _COMPANY_MARKERS):
            company_total = amount
            continue
        mapped = _normalize_forecast_label(raw_name)
        if mapped:
            by_dept[mapped] = by_dept.get(mapped, 0.0) + amount
    return by_dept, company_total


def _rows_from_workbook_sheets(file_path: str) -> list[tuple] | None:
    path = Path(file_path)
    suffix = path.suffix.lower()
    if suffix not in {".xlsx", ".xls", ".xlsm"}:
        return None
    if suffix == ".xls":
        from services.excel_parser import parse_excel

        sheets = parse_excel(file_path)
        if len(sheets) < 3:
            return None
        frame = list(sheets.values())[2]
        return [tuple(frame.iloc[i].tolist()) for i in range(min(len(frame), 80))]
    try:
        import openpyxl

        wb = openpyxl.load_workbook(file_path, read_only=True, data_only=True)
    except Exception:
        return None
    try:
        ws = _pick_plan_worksheet(wb)
        if ws is None:
            return None
        rows: list[tuple] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i > 80:
                break
            rows.append(tuple(row))
        return rows
    finally:
        wb.close()


def parse_payment_receipt_plan_sheet(
    file_path: str,
) -> tuple[dict[str, float], float | None]:
    """План поступлений по службам и строка компании с 3-го листа дефицита."""
    rows = _rows_from_workbook_sheets(file_path)
    if not rows:
        return {}, None
    return _extract_plan_from_rows(rows)


def attach_deficit_payment_plan(df: pd.DataFrame, file_path: str) -> pd.DataFrame:
    parsed, company = parse_payment_receipt_plan_sheet(file_path)
    if parsed:
        df.attrs[_ATTR_PAYMENT_PLAN] = parsed
    if company is not None:
        df.attrs[_ATTR_PAYMENT_PLAN_COMPANY] = company
    return df


def _plan_total(df: pd.DataFrame) -> float | None:
    company = df.attrs.get(_ATTR_PAYMENT_PLAN_COMPANY)
    if company is not None:
        return float(company)
    plans: dict[str, float] = dict(df.attrs.get(_ATTR_PAYMENT_PLAN) or {})
    if not plans:
        return None
    return float(sum(plans.values()))


def _normalize_dept_amounts(by_dept: dict[str, float]) -> dict[str, float]:
    """Службы отдельно (СИО и ОАПЛиС не склеиваем); «Сервис» → СЕРВИС."""
    out: dict[str, float] = {}
    for key, amount in by_dept.items():
        label = _sales_row_label(key)
        out[label] = out.get(label, 0.0) + float(amount or 0.0)
    return out


def _aggregate_paid_by_periods(df: pd.DataFrame, tile: Tile | None = None) -> dict:
    """Оплачено по службам; колонки = периоды файла с выбранной гранулярностью."""
    from services.dashboard_engine import _deals_zk_bucket_key, _department_labels

    layout = detect_deficit_money_layout(df)
    paid_col = layout.paid
    dept_col = resolve_semantic_column(df, "", semantic="department", dtype="categorical")
    if not dept_col or not paid_col or dept_col not in df.columns:
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
        total = _part_order_stats(part, paid_col, oid)
        per_period: dict[str, dict[str, float]] = {}
        if period_keys and "_period" in part.columns:
            for key in period_keys:
                slot = part.loc[part["_period"] == key]
                per_period[str(key)] = (
                    _part_order_stats(slot, paid_col, oid)
                    if not slot.empty
                    else {"count": 0.0, "sum": 0.0}
                )
        by_dept[label] = {"total": total, "periods": per_period}

    return {
        "periods": periods_meta,
        "by_dept": by_dept,
        "child_mode": mode,
    }


def build_deficit_payment_by_department_table(
    df: pd.DataFrame, tile: Tile | None = None
) -> dict | None:
    """Таблица служб: план × факт по периодам × % выполнения."""
    layout = detect_deficit_money_layout(df)
    if not layout.paid:
        return None
    plans = _normalize_dept_amounts(dict(df.attrs.get(_ATTR_PAYMENT_PLAN) or {}))
    packed = _aggregate_paid_by_periods(df, tile)
    by_dept = packed.get("by_dept") or {}
    periods = list(packed.get("periods") or [])
    if not plans and not by_dept:
        return None

    labels = [lab for lab in _ROW_ORDER_SPLIT if lab in plans or lab in by_dept]
    for lab in sorted({*plans, *by_dept}):
        if lab not in labels:
            labels.append(lab)

    show_period_total = len(periods) != 1
    columns = ["План поступлений, р."]
    column_kinds: list[str] = ["money"]
    year_spans: list[dict] = [{"label": "", "count": 1}]
    for meta in periods:
        columns.append(meta["label"])
        column_kinds.append("money")
        year_spans.append({"label": meta["label"], "count": 1})
    if show_period_total:
        columns.append("Фактические поступления р.")
        column_kinds.append("money")
        year_spans.append(
            {"label": "Всего" if periods else "", "count": 1}
        )
    columns.append("Выполнение плана")
    column_kinds.append("percent")
    year_spans.append({"label": "", "count": 1})

    fulfill_col = len(columns) - 1
    fact_col = (
        fulfill_col - 1
        if show_period_total
        else (1 if periods else fulfill_col - 1)
    )

    rows: list[dict] = []
    totals = [0.0] * len(columns)
    period_sums = [0.0] * len(periods)

    for label in labels:
        info = by_dept.get(label, {"total": {"sum": 0.0}, "periods": {}})
        plan = float(plans.get(label) or 0.0)
        fact = float((info.get("total") or {}).get("sum", 0) or 0)
        if plan == 0 and fact == 0 and not any(
            float((info.get("periods") or {}).get(str(m["key"]), {}).get("sum", 0) or 0)
            for m in periods
        ):
            continue
        values: list = [plan]
        for i, meta in enumerate(periods):
            slot_sum = float(
                (info.get("periods") or {}).get(str(meta["key"]), {}).get("sum", 0) or 0
            )
            values.append(slot_sum)
            period_sums[i] += slot_sum
        if show_period_total:
            values.append(fact)
        pct = _fulfillment_percent(fact, plan if plan > 0 else None)
        values.append(pct)
        rows.append({"label": label, "values": values})
        totals[fact_col] = float(totals[fact_col] or 0) + fact

    if not rows:
        return None

    company_plan = _plan_total(df)
    if company_plan is None:
        company_plan = float(sum(float(r["values"][0] or 0) for r in rows))
    company_fact = float(_col_sum(df, layout.paid))
    totals[0] = company_plan
    for i, value in enumerate(period_sums):
        totals[1 + i] = value
    totals[fact_col] = company_fact
    totals[fulfill_col] = _fulfillment_percent(
        company_fact, company_plan if company_plan > 0 else None
    )

    return {
        "index_label": "Службы",
        "columns": columns,
        "column_kinds": column_kinds,
        "year_spans": year_spans if periods else None,
        "rows": rows,
        "totals": totals,
        "totals_label": _COMPANY_TOTAL_LABEL,
        "totals_emphasis": True,
        "forecast_column_index": 0,
        "order_sum_column_index": fact_col,
        "fulfill_column_index": fulfill_col,
    }


def build_deficit_payment_fulfillment_table(department_table: dict) -> dict | None:
    """Компактная таблица «Службы × % оплаты» (СИО и ОАПЛиС раздельно)."""
    rows_in = list(department_table.get("rows") or [])
    if not rows_in:
        return None
    fulfill_col = int(
        department_table.get("fulfill_column_index")
        if department_table.get("fulfill_column_index") is not None
        else max(0, len(department_table.get("columns") or []) - 1)
    )
    company_pct = None
    totals = list(department_table.get("totals") or [])
    if len(totals) > fulfill_col and totals[fulfill_col] is not None:
        company_pct = int(totals[fulfill_col])

    rows: list[dict] = []
    for row in rows_in:
        values = list(row.get("values") or [])
        pct = values[fulfill_col] if len(values) > fulfill_col else None
        if pct is None:
            continue
        rows.append(
            {
                "label": str(row.get("label") or ""),
                "values": [int(pct)],
            }
        )
    if not rows:
        return None

    return {
        "index_label": "Службы",
        "columns": ["Оплата, р."],
        "column_kinds": ["percent"],
        "rows": rows,
        "totals": [company_pct] if company_pct is not None else None,
        "totals_label": str(
            department_table.get("totals_label") or _COMPANY_TOTAL_LABEL
        ),
        "totals_emphasis": True,
    }


def build_deficit_payment_share_table(department_table: dict) -> dict | None:
    """Таблица оплаты и доли поступлений; при нескольких периодах — колонки периодов."""
    rows_in = list(department_table.get("rows") or [])
    if not rows_in:
        return None
    from services.deficit_sales_table import _period_money_columns

    period_cols = _period_money_columns(department_table)
    fact_col = int(
        department_table.get("order_sum_column_index")
        if department_table.get("order_sum_column_index") is not None
        else 1
    )
    totals_in = list(department_table.get("totals") or [])
    company_fact = (
        float(totals_in[fact_col] or 0) if len(totals_in) > fact_col else 0.0
    )
    if company_fact <= 0:
        company_fact = float(
            sum(
                float((row.get("values") or [0] * (fact_col + 1))[fact_col] or 0)
                for row in rows_in
            )
        )

    show_period_total = len(period_cols) != 1
    columns: list[str] = []
    column_kinds: list[str] = []
    year_spans: list[dict] = []
    for label, _col_i in period_cols:
        columns.append(label)
        column_kinds.append("money")
        year_spans.append({"label": label, "count": 1})
    if show_period_total or not period_cols:
        columns.append("Оплата, р.")
        column_kinds.append("money")
        year_spans.append({"label": "Всего" if period_cols else "", "count": 1})
    columns.append("Доля поступлений")
    column_kinds.append("percent1")
    year_spans.append({"label": "", "count": 1})

    share_col = len(columns) - 1
    order_sum_col = share_col - 1 if (show_period_total or not period_cols) else 0

    rows: list[dict] = []
    totals = [0.0] * len(columns)
    period_sums = [0.0] * len(period_cols)

    for row in rows_in:
        values_in = list(row.get("values") or [])
        fact = float(values_in[fact_col] or 0) if len(values_in) > fact_col else 0.0
        values: list = []
        for i, (_label, col_i) in enumerate(period_cols):
            slot = float(values_in[col_i] or 0) if len(values_in) > col_i else 0.0
            values.append(slot)
            period_sums[i] += slot
        if show_period_total or not period_cols:
            values.append(fact)
        share = round(100.0 * fact / company_fact, 1) if company_fact > 0 else 0.0
        values.append(share)
        rows.append({"label": str(row.get("label") or ""), "values": values})
        totals[order_sum_col] = float(totals[order_sum_col] or 0) + fact

    if not rows:
        return None

    for i, value in enumerate(period_sums):
        totals[i] = value
    totals[order_sum_col] = company_fact
    totals[share_col] = 100.0 if company_fact > 0 else 0.0

    return {
        "index_label": "Службы",
        "columns": columns,
        "column_kinds": column_kinds,
        "year_spans": year_spans if period_cols else None,
        "rows": rows,
        "totals": totals,
        "totals_label": str(
            department_table.get("totals_label") or _COMPANY_TOTAL_LABEL
        ),
        "totals_emphasis": True,
        "order_sum_column_index": order_sum_col,
    }


def build_deficit_payment_receipt_table(df: pd.DataFrame, tile: Tile) -> dict:
    """Итог план/факт; при нескольких периодах — разбивка факта как на «Продажах»."""
    from services.deficit_sales_table import _allocate_percentages, _period_money_columns

    layout = detect_deficit_money_layout(df)
    plan = _plan_total(df)
    if plan is None:
        return {"error": "Не нашёл план поступлений на 3-м листе файла дефицита"}
    if not layout.paid:
        return {"error": "В таблице «Дефицит» нет колонки «Оплачено по заказу»"}
    fact = _col_sum(df, layout.paid)
    pct = _fulfillment_percent(fact, plan if plan > 0 else None)
    bucket = (tile.source.period or "quarter") if tile else "quarter"

    department_table = build_deficit_payment_by_department_table(df, tile)
    period_bars: list[dict] = []
    if department_table:
        totals = list(department_table.get("totals") or [])
        period_cols = _period_money_columns(department_table)
        amounts = [
            float(totals[col_i] or 0) if len(totals) > col_i else 0.0
            for _label, col_i in period_cols
        ]
        allocated = (
            _allocate_percentages(amounts, plan, target=pct)
            if plan > 0 and pct is not None
            else [0] * len(amounts)
        )
        for (label, _col_i), amount, bar_pct in zip(period_cols, amounts, allocated):
            period_bars.append(
                {"label": label, "order_sum": amount, "pct": bar_pct}
            )

    columns = ["План"]
    column_kinds = ["money"]
    values: list = [plan]
    if len(period_bars) > 1:
        for bar in period_bars:
            columns.append(bar["label"])
            column_kinds.append("money")
            values.append(bar["order_sum"])
    else:
        columns.append("Факт")
        column_kinds.append("money")
        values.append(fact)
    columns.append("Выполнение плана")
    column_kinds.append("percent")
    values.append(pct)

    return {
        "groups": {"Поступление оплаты": fact},
        "table": {
            "layout": "payment_receipt",
            "columns": columns,
            "column_kinds": column_kinds,
            "rows": [{"label": "", "values": values}],
        },
        "plan": plan,
        "fact": fact,
        "fulfill_pct": pct,
        "period_bars": period_bars,
        "bucket_period": bucket,
    }


def build_deficit_payment_by_department(df: pd.DataFrame, tile: Tile) -> dict:
    table = build_deficit_payment_by_department_table(df, tile)
    if not table:
        return {"error": "Нет данных плана или оплат по службам"}
    fact_col = int(
        table.get("order_sum_column_index")
        if table.get("order_sum_column_index") is not None
        else 1
    )
    return {
        "groups": {
            row["label"]: float((row.get("values") or [0] * (fact_col + 1))[fact_col] or 0)
            for row in table.get("rows") or []
        },
        "table": table,
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }


def build_deficit_payment_fulfillment_board(df: pd.DataFrame, tile: Tile) -> dict:
    dept = build_deficit_payment_by_department_table(df, tile)
    if not dept:
        return {"error": "Нет данных для выполнения плана поступлений"}
    table = build_deficit_payment_fulfillment_table(dept)
    if not table:
        return {"error": "Нет данных для выполнения плана поступлений"}
    return {
        "groups": {
            row["label"]: float((row.get("values") or [0])[0] or 0)
            for row in table.get("rows") or []
        },
        "table": table,
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }


def build_deficit_payment_share(df: pd.DataFrame, tile: Tile) -> dict:
    dept = build_deficit_payment_by_department_table(df, tile)
    if not dept:
        return {"error": "Нет данных для доли поступлений"}
    table = build_deficit_payment_share_table(dept)
    if not table:
        return {"error": "Нет данных для доли поступлений"}
    order_sum_col = int(
        table.get("order_sum_column_index")
        if table.get("order_sum_column_index") is not None
        else 0
    )
    return {
        "groups": {
            row["label"]: float(
                (row.get("values") or [0] * (order_sum_col + 1))[order_sum_col] or 0
            )
            for row in table.get("rows") or []
        },
        "table": table,
        "bucket_period": (tile.source.period or "quarter") if tile else "quarter",
    }
