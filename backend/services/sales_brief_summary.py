"""Текстовая аналитическая справка по файлу воронки продаж (блок под контекстом файла)."""

import re

import pandas as pd

from models.dashboard_spec import Tile, TileSource
from services.dashboard_engine import (
    _COMPANY_ROW_LABEL,
    _conversion_percent,
    _deal_count_in_frame,
    _department_labels,
    _deals_dynamics_prepare,
    _file_span_column_label,
    _named_column,
    _pivot_date_column,
    _resolve_group_column,
    _sum_named,
)

_CONVERSION_CORE_EXCLUDE = frozenset({"Сервис", "СООК", "СС"})


def _format_int(value: float) -> str:
    return f"{int(round(value)):,}".replace(",", " ")


def _format_money(value: float) -> str:
    rounded = round(float(value))
    return f"{_format_int(rounded)} руб."


def _format_percent(value: int) -> str:
    return f"{int(value)} %"


def _expand_span_label(span: str) -> str:
    if match := re.fullmatch(r"(\d+) кв (\d+)", span.strip()):
        return f"{match.group(1)} квартал {match.group(2)}"
    if match := re.fullmatch(r"(\d+) пг (\d+)", span.strip()):
        return f"{match.group(1)} полугодие {match.group(2)}"
    if re.fullmatch(r"\d{2}\.\d{4}", span.strip()):
        month, year = span.split(".")
        return f"{month}.{year}"
    return span


def _describe_period(df: pd.DataFrame) -> str:
    date_col = _pivot_date_column(df)
    if not date_col or date_col not in df.columns:
        return "рассматриваемый период"
    dates = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True).dropna()
    if dates.empty:
        return "рассматриваемый период"
    start = dates.min()
    end = dates.max()
    span = _expand_span_label(_file_span_column_label(dates))
    start_s = start.strftime("%d.%m.%Y")
    end_s = end.strftime("%d.%m.%Y")
    if start_s == end_s:
        return f"{span} ({start_s})"
    return f"{span}, с {start_s} по {end_s}"


def _summary_tile() -> Tile:
    return Tile(
        title="Справка",
        chart_type="table",
        source=TileSource(
            kind="deals_conversion",
            group_semantic="department",
            period="quarter",
        ),
        agg="sum",
        top_n=50,
        sort="desc",
    )


def _dept_masks(work: pd.DataFrame, group_col: str) -> tuple[pd.Series, pd.Series]:
    names = _department_labels(work[group_col].astype(str).str.strip())
    blank = work[group_col].astype(str).str.strip().str.lower().isin({"", "nan", "-", "none"})
    return names, blank


def build_sales_brief_summary(df: pd.DataFrame) -> str:
    tile = _summary_tile()
    prep = _deals_dynamics_prepare(df, tile)
    if prep.get("error"):
        return ""

    work = prep["work"]
    deal_n = prep["deal_n"]
    deal_s = _named_column(df, "сумма по сделке")
    zk_n = _named_column(df, "количество зк")
    if not deal_s or not zk_n:
        return ""

    group_col = _resolve_group_column(df, tile)
    if not group_col or group_col not in work.columns:
        return ""

    period_label = _describe_period(df)
    deals_total = _deal_count_in_frame(work, deal_n)
    potential = _sum_named(work, deal_s)

    names, blank = _dept_masks(work, group_col)
    dept_frame = work.loc[~blank].assign(_dept=names.loc[~blank])
    dept_potential = (
        dept_frame.groupby("_dept", sort=False)
        .apply(lambda part: _sum_named(part, deal_s), include_groups=False)
        .sort_values(ascending=False)
    )
    dept_potential = dept_potential[dept_potential.index != _COMPANY_ROW_LABEL]

    best_dept = "—"
    best_share = 0
    best_amount = 0.0
    if not dept_potential.empty and potential > 0:
        best_dept = str(dept_potential.index[0])
        best_amount = float(dept_potential.iloc[0])
        best_share = int(round(100.0 * best_amount / potential))

    conv_all = _conversion_percent(_sum_named(work, zk_n), deals_total)

    core_mask = ~blank & ~names.isin(_CONVERSION_CORE_EXCLUDE)
    core_deals = _deal_count_in_frame(work.loc[core_mask], deal_n)
    core_zk = _sum_named(work.loc[core_mask], zk_n)
    conv_core = _conversion_percent(core_zk, core_deals)

    best_conv_dept = "—"
    best_conv_value = 0
    for dept in dept_potential.index:
        if dept in _CONVERSION_CORE_EXCLUDE:
            continue
        mask = names == dept
        value = _conversion_percent(_sum_named(work.loc[mask], zk_n), _deal_count_in_frame(work.loc[mask], deal_n))
        if value > best_conv_value:
            best_conv_value = value
            best_conv_dept = str(dept)

    parts = [
        (
            f"**Заведено сделок** в *{period_label}* — "
            f"**{_format_int(deals_total)}**."
        ),
        f"**Общий потенциал сделок** — *{_format_money(potential)}*",
        (
            f"**Лучшее подразделение по потенциалу** — **{best_dept}** "
            f"(доля *{_format_percent(best_share)}*, сумма *{_format_money(best_amount)}*)."
        ),
        (
            "**Конверсия сделок в заказы**\n"
            f"- *с учётом Сервиса и СООК* — **{_format_percent(conv_all)}**\n"
            f"- *без Сервиса и СООК* — **{_format_percent(conv_core)}**"
        ),
        (
            f"**Лучшая конверсия** (без Сервиса и СООК) — "
            f"**{_format_percent(best_conv_value)}**, подразделение **{best_conv_dept}**."
        ),
    ]

    return "\n\n".join(parts)
