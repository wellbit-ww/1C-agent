import pandas as pd
from typing import Any

from services.profile_registry import ReportConfig
from services.kpi_engine import run_kpis
from services.chart_service import (
    create_bar_chart,
    create_pie_chart,
    create_monthly_trend_chart,
    create_manager_chart,
)
from services.column_resolver import resolve_semantic_column
from services.data_tools import group_sum, get_top_n, group_by_month
from services.report_profiles.base_profile import ReportProfile
from services.summary_service import generate_summary


def _top_group(df: pd.DataFrame, group_col, value_col):
    if not group_col or not value_col:
        return None
    values = pd.to_numeric(df[value_col], errors="coerce")
    grouped = values.groupby(df[group_col], dropna=True).sum()
    clean = grouped.dropna()
    if clean.empty:
        return None
    name = clean.idxmax()
    return str(name), float(clean.loc[name])


def _deficit_column(df: pd.DataFrame):
    for col in df.columns:
        lower = str(col).lower()
        if "дефицит" in lower or "остаток" in lower or "задолженность" in lower or "не оплачен" in lower or "неоплачен" in lower:
            if pd.api.types.is_numeric_dtype(df[col]):
                return col
    return resolve_semantic_column(df, "", semantic="amount", dtype="numeric")


class ReportEngine(ReportProfile):
    def __init__(self, config: ReportConfig):
        self.config = config

    def _translate_kpis(self, kpis: list[dict]) -> list[dict]:
        translations = {
            "total_revenue": "Общая выручка",
            "average_check": "Средний чек",
            "total_deficit": "Общий дефицит",
            "unique_customers": "Уникальные клиенты",
            "unique_managers": "Уникальные менеджеры",
            "unique_departments": "Уникальные подразделения",
            "row_count": "Количество строк",
        }
        translated = []
        for k in kpis:
            translated.append({
                "label": translations.get(k["name"], k["name"]),
                "value": k["formatted"],
                "raw_value": k["value"],
                "name": k["name"],
            })
        return translated

    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        if self.config.name == "deficit_report":
            from services.report_profiles.deficit_profile import deficit_kpis

            return deficit_kpis(df)
        return self._translate_kpis(run_kpis(df, self.config.kpis))

    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        charts = []
        for c in self.config.charts:
            if c == "revenue_by_customer":
                data = group_sum(df, "выручка по клиентам", top_n=10)
                if "error" not in data:
                    charts.append(create_bar_chart(data, title="Выручка по клиентам"))
            elif c == "revenue_by_manager":
                data = group_sum(df, "выручка по менеджерам", top_n=10)
                if "error" not in data:
                    charts.append(create_manager_chart(data))
            elif c == "monthly_trend":
                data = group_by_month(df, "динамика по месяцам")
                if "error" not in data:
                    charts.append(create_monthly_trend_chart(data))
            elif c == "deficit_by_manager":
                data = group_sum(df, "дефицит по менеджерам", top_n=10)
                if "error" not in data:
                    charts.append(create_bar_chart(data, title="Дефицит по менеджерам"))
            elif c == "deficit_by_department":
                data = group_sum(df, "дефицит по подразделениям", top_n=10)
                if "error" not in data:
                    charts.append(create_pie_chart(data, title="Дефицит по подразделениям"))
        return charts

    def get_insights(self, df: pd.DataFrame) -> list[str]:
        insights = []
        deficit = self.config.name == "deficit_report"
        unpaid_col = None
        order_col = None
        unpaid_phrase = "дефициту"
        if deficit:
            from services.report_profiles.deficit_profile import detect_deficit_money_layout

            layout = detect_deficit_money_layout(df)
            unpaid_col = layout.unpaid or _deficit_column(df)
            order_col = layout.order_sum
            if unpaid_col and "дефицит" in str(unpaid_col).lower() and "остаток" not in str(unpaid_col).lower():
                unpaid_phrase = "дефициту"
            else:
                unpaid_phrase = "неоплаченному остатку"

        def _line(kind: str, phrase: str, top) -> str:
            if top is None:
                return ""
            name, value = top
            from services.kpi_engine import _format_number

            return f"{kind} по {phrase}: {name} ({_format_number(value)})"

        for ins in self.config.insights:
            if ins == "top_customer":
                client_col = resolve_semantic_column(
                    df, "", semantic="client", dtype="categorical"
                )
                if deficit and unpaid_col:
                    line = _line("Топ клиент", unpaid_phrase, _top_group(df, client_col, unpaid_col))
                    if line:
                        insights.append(line)
                    if order_col and order_col != unpaid_col:
                        line = _line(
                            "Топ клиент",
                            "сумме заказов",
                            _top_group(df, client_col, order_col),
                        )
                        if line:
                            insights.append(line)
                else:
                    data = get_top_n(df, "лучший клиент", semantic="client", n=1)
                    top_name = next(iter(data["groups"]), None) if "groups" in data else None
                    if top_name is not None:
                        insights.append(f"Топ клиент: {top_name}")
            elif ins == "top_manager":
                if deficit and unpaid_col:
                    mgr_col = resolve_semantic_column(
                        df, "", semantic="manager", dtype="categorical"
                    )
                    line = _line(
                        "Топ менеджер",
                        unpaid_phrase,
                        _top_group(df, mgr_col, unpaid_col),
                    )
                    if line:
                        insights.append(line)
                else:
                    data = get_top_n(df, "лучший менеджер", semantic="manager", n=1)
                    top_name = next(iter(data["groups"]), None) if "groups" in data else None
                    if top_name is not None:
                        insights.append(f"Топ менеджер: {top_name}")
            elif ins == "best_month":
                data = group_by_month(df, "динамика по месяцам")
                if "groups" in data and data["groups"]:
                    best_m = max(data["groups"].items(), key=lambda x: x[1])
                    insights.append(f"Самый сильный месяц: {best_m[0]}")
            elif ins == "top_department":
                dept_col = next(
                    (
                        c
                        for c in df.columns
                        if "подразделение" in str(c).lower() or "отдел" in str(c).lower()
                    ),
                    None,
                )
                line = _line(
                    "Топ подразделение",
                    unpaid_phrase if deficit else "дефициту",
                    _top_group(df, dept_col, unpaid_col),
                )
                if line:
                    insights.append(line)
        return insights

    def get_summary(self, df: pd.DataFrame) -> str:
        kpis = run_kpis(df, self.config.kpis)
        return generate_summary(self.config.name, kpis, self.get_insights(df))

    def get_dashboard_spec(self, df: pd.DataFrame):
        if self.config.name == "sales_pipeline":
            from services.report_profiles.sales_profile import build_sales_dashboard_spec

            return build_sales_dashboard_spec(df)
        if self.config.name == "deficit_report":
            from services.report_profiles.deficit_profile import build_deficit_dashboard_spec

            return build_deficit_dashboard_spec(df)
        from services.generic_dashboard import build_generic_spec

        return build_generic_spec(df)
