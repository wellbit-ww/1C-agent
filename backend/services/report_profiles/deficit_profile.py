"""Профиль дефицита: KPI и дашборд по типовой выгрузке 1С.

Шапка KPI (6 карточек): прогноз продаж, сумма продаж, нарушенные сроки
поставки, неоплаченный остаток, план и факт оплаты — с периодом из данных.
Денежные колонки: «Оплачено по заказу», «Неоплаченный остаток», сумма заказа
(строка «Итого» в парсере отбрасывается, чтобы не задвоить).
"""
from dataclasses import dataclass

import pandas as pd

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.kpi_engine import _format_number
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
        if any(
            x in name
            for x in (
                "оплач",
                "долг",
                "к оплате",
                "остаток",
                "дефицит",
                "очередн",
                "платеж",
                "этап",
                "предоплат",
            )
        ):
            continue
        if "сумма" not in name and "стоим" not in name:
            continue
        if "заказчик" in name:
            continue
        score = 0
        if "заказ" in name:
            score += 6
        if "всего" in name:
            score += 6
        if "руб" in name or "₽" in name:
            score += 1
        if name in {"сумма", "сумма (руб)", "сумма руб"}:
            score -= 4
        if name.endswith("_1") or name.endswith("_2"):
            score -= 8
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


def _format_period_span(dates: pd.Series) -> str | None:
    valid = pd.to_datetime(dates, errors="coerce", dayfirst=True).dropna()
    if valid.empty:
        return None
    start = valid.min()
    end = valid.max()
    if start.year == end.year and start.month == end.month:
        return start.strftime("%m.%Y")
    return f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"


def _kpi_period_label(df: pd.DataFrame) -> str | None:
    """Период для подписей KPI — по датам заказов, иначе по любой дате файла."""
    try:
        from services.deficit_sales_table import _resolve_order_date_series

        order_dates = _resolve_order_date_series(df)
    except Exception:
        order_dates = None
    if order_dates is not None:
        span = _format_period_span(order_dates)
        if span:
            return span
    from services.insights_service import _get_date_period

    return _get_date_period(df)


def _label_with_period(base: str, period: str | None, prep: str) -> str:
    if not period:
        return base
    return f"{base} {prep} {period}"


def _sales_forecast_total(df: pd.DataFrame) -> float | None:
    company = df.attrs.get("deficit_sales_forecast_company") or {}
    raw = company.get("forecast") if isinstance(company, dict) else None
    if raw is not None:
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
    by_dept = df.attrs.get("deficit_sales_forecast") or {}
    if isinstance(by_dept, dict) and by_dept:
        return float(sum(float(v or 0) for v in by_dept.values()))
    return None


def _payment_plan_total(df: pd.DataFrame) -> float | None:
    from services.deficit_payment_table import _plan_total

    return _plan_total(df)


def _find_named_date_column(df: pd.DataFrame, *prefs: str) -> str | None:
    by_name = {_norm(c): str(c) for c in df.columns}
    for pref in prefs:
        needle = _norm(pref)
        if needle in by_name:
            return by_name[needle]
    for pref in prefs:
        needle = _norm(pref)
        for name, col in by_name.items():
            if needle in name:
                return col
    return None


def _late_delivery_count(df: pd.DataFrame) -> int | None:
    """Число заказов, у которых дата реализации позже даты поставки."""
    delivery_col = _find_named_date_column(df, "дата поставки")
    if not delivery_col:
        return None
    realization_col = _find_named_date_column(df, "дата реализации")
    if realization_col:
        realization = pd.to_datetime(df[realization_col], errors="coerce", dayfirst=True)
    else:
        try:
            from services.deficit_sales_table import _resolve_order_date_series

            realization = _resolve_order_date_series(df)
        except Exception:
            realization = None
    if realization is None:
        return None
    delivery = pd.to_datetime(df[delivery_col], errors="coerce", dayfirst=True)
    both = realization.notna() & delivery.notna()
    if not bool(both.any()):
        return 0
    return int((both & (realization > delivery)).sum())


def _append_kpi(
    kpis: list[dict],
    *,
    name: str,
    label: str,
    value: float | int | None,
    as_int: bool = False,
) -> None:
    if value is None:
        return
    if as_int:
        ival = int(value)
        kpis.append(
            {
                "name": name,
                "label": label,
                "value": _format_number(float(ival)),
                "raw_value": float(ival),
            }
        )
        return
    fval = float(value)
    kpis.append(
        {
            "name": name,
            "label": label,
            "value": _format_number(fval),
            "raw_value": fval,
        }
    )


def deficit_kpis(df: pd.DataFrame) -> list[dict]:
    layout = detect_deficit_money_layout(df)
    period = _kpi_period_label(df)
    kpis: list[dict] = []

    _append_kpi(
        kpis,
        name="sales_forecast",
        label=_label_with_period("Прогноз продаж", period, "на"),
        value=_sales_forecast_total(df),
    )
    _append_kpi(
        kpis,
        name="order_sum",
        label=_label_with_period("Общая сумма продаж", period, "в"),
        value=_col_sum(df, layout.order_sum) if layout.order_sum else None,
    )
    late = _late_delivery_count(df)
    _append_kpi(
        kpis,
        name="late_deliveries",
        label="Нарушены сроки поставки",
        value=late if late is not None else None,
        as_int=True,
    )
    _append_kpi(
        kpis,
        name="total_deficit",
        label=_label_with_period("Неоплаченный остаток по заказам", period, "в"),
        value=_col_sum(df, layout.unpaid) if layout.unpaid else None,
    )
    _append_kpi(
        kpis,
        name="payment_plan",
        label=_label_with_period("План оплаты", period, "в"),
        value=_payment_plan_total(df),
    )
    _append_kpi(
        kpis,
        name="paid_on_order",
        label=_label_with_period("Факт оплаты", period, "в"),
        value=_col_sum(df, layout.paid) if layout.paid else None,
    )
    return kpis


def _deficit_sales_table_tile() -> Tile:
    return Tile(
        title="Продажи по подразделениям",
        chart_type="table",
        source=TileSource(kind="deficit_sales_by_department", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_sales_forecast_vitrine_tile() -> Tile:
    return Tile(
        title="Прогноз продаж по службам",
        chart_type="table",
        source=TileSource(kind="deficit_sales_forecast_vitrine", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_sales_fulfillment_board_tile() -> Tile:
    return Tile(
        title="Выполнение прогноза по подразделениям",
        chart_type="table",
        source=TileSource(kind="deficit_sales_fulfillment_board", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_payment_receipt_tile() -> Tile:
    return Tile(
        title="Поступление оплаты",
        chart_type="table",
        source=TileSource(kind="deficit_payment_receipt", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_payment_by_department_tile() -> Tile:
    return Tile(
        title="Поступления по службам",
        chart_type="table",
        source=TileSource(kind="deficit_payment_by_department", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_payment_fulfillment_board_tile() -> Tile:
    return Tile(
        title="Выполнение плана поступлений",
        chart_type="table",
        source=TileSource(kind="deficit_payment_fulfillment_board", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_payment_share_tile() -> Tile:
    return Tile(
        title="Доля поступлений по службам",
        chart_type="table",
        source=TileSource(kind="deficit_payment_share", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def deficit_payment_tab_tiles(period: str = "quarter") -> list[Tile]:
    """Тайлы вкладки «Оплата» — структура как у «Продаж», с тем же периодом."""
    if period not in {"half", "quarter", "month"}:
        period = "quarter"
    tiles = [
        _deficit_payment_receipt_tile(),
        _deficit_payment_by_department_tile(),
        _deficit_payment_fulfillment_board_tile(),
        _deficit_payment_share_tile(),
    ]
    return [
        t.model_copy(
            update={"source": t.source.model_copy(update={"period": period})}
        )
        for t in tiles
    ]


def _deficit_sales_share_tile() -> Tile:
    return Tile(
        title="Доля подразделений в продажах",
        chart_type="table",
        source=TileSource(kind="deficit_sales_share", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def _deficit_sales_summary_tile() -> Tile:
    return Tile(
        title="Выполнение прогноза",
        chart_type="table",
        source=TileSource(kind="deficit_sales_summary", period="quarter"),
        agg="sum",
        top_n=50,
        sort="none",
    )


def build_deficit_dashboard_spec(df: pd.DataFrame) -> DashboardSpec | None:
    layout = detect_deficit_money_layout(df)
    if layout.is_empty():
        from services.generic_dashboard import build_generic_spec

        return build_generic_spec(df)

    tabs: list[Tab] = [
        # «Платежи» в UI — «Продажи»; «Структура» — пустой дашборд «Оплата» справа.
        Tab(
            title="Платежи",
            tiles=[
                _deficit_sales_table_tile(),
                _deficit_sales_summary_tile(),
                _deficit_sales_forecast_vitrine_tile(),
                _deficit_sales_fulfillment_board_tile(),
                _deficit_sales_share_tile(),
            ],
        ),
        Tab(title="Структура", tiles=deficit_payment_tab_tiles("quarter")),
    ]
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
        from services.workspace_service import normalize_deficit_dashboard_spec

        spec = build_deficit_dashboard_spec(df)
        return normalize_deficit_dashboard_spec(spec) if spec else spec
