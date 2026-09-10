"""Профиль дефицита: KPI и дашборд по типовой выгрузке 1С.

Типовая шапка: блок «К оплате» (До обеспечения / До отгрузки / После отгрузки),
«Оплачено по заказу», «Неоплаченный остаток». Итог — сумма колонки
(строка «Итого» в парсере отбрасывается, чтобы не задвоить).
"""
from dataclasses import dataclass

import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.column_resolver import resolve_semantic_column
from services.kpi_engine import (
    _format_number,
    calculate_unique_customers,
    calculate_unique_departments,
)
from services.profile_registry import get_profile
from services.report_engine import ReportEngine
from services.report_profiles.base_profile import ReportProfile

_STAGE_ORDER = (
    ("До обеспечения", ("к оплате", "обеспечен")),
    ("До отгрузки", ("к оплате", "до отгруз")),
    ("После отгрузки", ("к оплате", "после отгруз")),
)


def _norm(name: str) -> str:
    return (
        str(name)
        .lower()
        .replace("ё", "е")
        .replace("—", "-")
        .replace("–", "-")
        .replace("  ", " ")
        .strip()
    )


def _is_money_column(df: pd.DataFrame, col) -> bool:
    if pd.api.types.is_numeric_dtype(df[col]):
        return True
    return pd.to_numeric(df[col], errors="coerce").notna().sum() > 0


def _col_sum(df: pd.DataFrame, col: str) -> float:
    series = pd.to_numeric(df[col], errors="coerce")
    total = series.sum()
    return 0.0 if pd.isna(total) else float(total)


@dataclass(frozen=True)
class DeficitMoneyLayout:
    stages: tuple[tuple[str, str], ...]  # (ярлык, колонка)
    paid: str | None
    unpaid: str | None
    order_sum: str | None

    @property
    def money_column(self) -> str | None:
        return self.unpaid or self.order_sum or self.paid

    def is_empty(self) -> bool:
        return not (self.stages or self.paid or self.unpaid or self.order_sum)


def detect_deficit_money_layout(df: pd.DataFrame) -> DeficitMoneyLayout:
    """Находит денежные блоки типового дефицита по именам колонок."""
    money_cols = [c for c in df.columns if _is_money_column(df, c)]
    norms = {c: _norm(c) for c in money_cols}

    stages: list[tuple[str, str]] = []
    used: set[str] = set()
    for label, needles in _STAGE_ORDER:
        found = None
        for col, name in norms.items():
            if col in used:
                continue
            if all(n in name for n in needles):
                if label == "До отгрузки" and "после" in name:
                    continue
                found = col
                break
        if found is not None:
            stages.append((label, found))
            used.add(found)

    paid = None
    for col, name in norms.items():
        if col in used:
            continue
        if "к оплате" in name:
            continue
        if "не оплач" in name or "неоплач" in name:
            continue
        if "оплачен" in name or name.rstrip(".") == "оплачено":
            paid = col
            used.add(col)
            break

    unpaid = None
    unpaid_needles = (
        ("неоплачен",),
        ("не оплачен",),
        ("сумма долга",),
        ("задолжен",),
        ("остаток",),
        ("дефицит",),
    )
    for needles in unpaid_needles:
        for col, name in norms.items():
            if col in used or "к оплате" in name:
                continue
            if all(n in name for n in needles):
                unpaid = col
                used.add(col)
                break
        if unpaid is not None:
            break

    order_sum = None
    order_ranked: list[tuple[int, str]] = []
    for col, name in norms.items():
        if col in used:
            continue
        if "валют" in name:
            continue
        if any(x in name for x in ("оплач", "долг", "к оплате", "остаток", "дефицит")):
            continue
        if "сумма" not in name and "стоим" not in name:
            continue
        score = 0
        if "руб" in name or "₽" in name:
            score += 4
        if "заказ" in name:
            score += 3
        if "всего" in name:
            score += 2
        if name.endswith("_1") or name.endswith("_2"):
            score -= 3
        order_ranked.append((score, col))
    if order_ranked:
        order_ranked.sort(key=lambda item: (-item[0], list(df.columns).index(item[1])))
        order_sum = order_ranked[0][1]

    return DeficitMoneyLayout(
        stages=tuple(stages),
        paid=paid,
        unpaid=unpaid,
        order_sum=order_sum,
    )


def deficit_kpis(df: pd.DataFrame) -> list[dict]:
    layout = detect_deficit_money_layout(df)
    kpis: list[dict] = []

    def add(name: str, label: str, col: str | None) -> None:
        if not col:
            return
        val = _col_sum(df, col)
        kpis.append(
            {
                "name": name,
                "label": label,
                "value": _format_number(val),
                "raw_value": val,
            }
        )

    add("total_deficit", "Неоплаченный остаток", layout.unpaid)
    add("paid_on_order", "Оплачено по заказу", layout.paid)
    for label, col in layout.stages:
        add(f"to_pay_{label}", f"К оплате · {label}", col)
    add("order_sum", "Сумма заказов, руб.", layout.order_sum)

    customers = calculate_unique_customers(df)
    if customers:
        kpis.append(
            {
                "name": customers["name"],
                "label": "Заказчики",
                "value": customers["formatted"],
                "raw_value": customers["value"],
            }
        )
    departments = calculate_unique_departments(df)
    if departments:
        kpis.append(
            {
                "name": departments["name"],
                "label": "Подразделения",
                "value": departments["formatted"],
                "raw_value": departments["value"],
            }
        )
    return kpis


def _named_tile(title: str, columns: list[str], chart_type: str = "bar") -> Tile:
    return Tile(
        title=title,
        chart_type=chart_type,
        source=TileSource(kind="named_columns", column_names=columns),
        agg="sum",
        unit="auto",
        sort="none",
    )


def _group_tile(
    title: str,
    group_semantic: str | None,
    group_column: str | None,
    value_column: str,
    chart_type: str,
    agg: str = "sum",
    top_n: int = 10,
) -> Tile:
    return Tile(
        title=title,
        chart_type=chart_type,
        source=TileSource(
            kind="group",
            group_semantic=group_semantic,
            group_column=group_column,
            value_column=value_column,
        ),
        agg=agg,
        top_n=top_n,
        unit="auto",
    )


def build_deficit_dashboard_spec(df: pd.DataFrame) -> DashboardSpec | None:
    layout = detect_deficit_money_layout(df)
    if layout.is_empty():
        from services.generic_dashboard import build_generic_spec

        return build_generic_spec(df)

    money = layout.money_column
    payments: list[Tile] = []
    if layout.stages:
        payments.append(
            _named_tile("К оплате", [col for _, col in layout.stages], "bar")
        )
    paid_unpaid = [c for c in (layout.paid, layout.unpaid) if c]
    if len(paid_unpaid) >= 2:
        payments.append(_named_tile("Оплачено и неоплаченный остаток", paid_unpaid, "bar"))
    elif layout.unpaid and layout.order_sum:
        payments.append(
            _named_tile(
                "Сумма заказа и неоплаченный остаток",
                [layout.order_sum, layout.unpaid],
                "bar",
            )
        )

    client_col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
    dept_col = resolve_semantic_column(
        df, "", semantic="department", dtype="categorical"
    )
    mgr_col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")

    if money and client_col:
        payments.append(
            _group_tile(
                "Неоплаченный остаток по заказчикам",
                "client",
                client_col,
                money,
                "hbar",
                top_n=12,
            )
        )
    if money and dept_col:
        payments.append(
            _group_tile(
                "Остаток по подразделениям",
                "department",
                dept_col,
                money,
                "pie",
                top_n=8,
            )
        )

    structure: list[Tile] = []
    if money and mgr_col:
        structure.append(
            _group_tile(
                "Остаток по ответственным",
                "manager",
                mgr_col,
                money,
                "hbar",
                top_n=12,
            )
        )
    if layout.order_sum and client_col:
        structure.append(
            _group_tile(
                "Сумма заказов по заказчикам",
                "client",
                client_col,
                layout.order_sum,
                "hbar",
                top_n=12,
            )
        )
    if dept_col:
        structure.append(
            Tile(
                title="Количество заказов по подразделениям",
                chart_type="bar",
                source=TileSource(
                    kind="group",
                    group_semantic="department",
                    group_column=dept_col,
                ),
                agg="count",
                top_n=12,
                unit="auto",
            )
        )

    from services import data_tools

    dates = data_tools.detect_date_columns(df).get("columns") or []
    if dates and money:
        structure.append(
            Tile(
                title="Динамика остатка по месяцам",
                chart_type="area",
                source=TileSource(
                    kind="period",
                    period="month",
                    value_column=money,
                ),
                unit="auto",
                sort="none",
            )
        )

    tabs: list[Tab] = []
    if payments:
        tabs.append(Tab(title="Платежи", tiles=payments[:8]))
    if structure:
        tabs.append(Tab(title="Структура", tiles=structure[:8]))
    if not tabs:
        from services.generic_dashboard import build_generic_spec

        return build_generic_spec(df)
    return DashboardSpec(tabs=tabs)


class DeficitProfile(ReportProfile):
    def get_insights(self, df):
        config = get_profile("deficit_report")
        if config is None:
            return []
        return ReportEngine(config).get_insights(df)

    def get_kpis(self, df):
        return deficit_kpis(df)

    def get_dashboard_spec(self, df):
        return build_deficit_dashboard_spec(df)
