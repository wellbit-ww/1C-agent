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
    grouped = df.groupby(group_col, dropna=True)[value_col].sum()
    clean = grouped.dropna()
    if clean.empty:
        return None
    return clean.idxmax()


def _deficit_column(df: pd.DataFrame):
    for col in df.columns:
        lower = str(col).lower()
        if "дефицит" in lower or "остаток" in lower or "задолженность" in lower:
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
        def_col = _deficit_column(df) if deficit else None
        for ins in self.config.insights:
            if ins == "top_customer":
                if deficit and def_col:
                    client_col = resolve_semantic_column(
                        df, "", semantic="client", dtype="categorical"
                    )
                    top_name = _top_group(df, client_col, def_col)
                else:
                    data = get_top_n(df, "лучший клиент", semantic="client", n=1)
                    top_name = next(iter(data["groups"]), None) if "groups" in data else None
                if top_name is not None:
                    insights.append(f"Топ клиент: {top_name}")
            elif ins == "top_manager":
                if deficit and def_col:
                    mgr_col = resolve_semantic_column(
                        df, "", semantic="manager", dtype="categorical"
                    )
                    top_name = _top_group(df, mgr_col, def_col)
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
                top_name = _top_group(df, dept_col, def_col)
                if top_name is not None:
                    insights.append(f"Топ подразделение по дефициту: {top_name}")
        return insights

    def get_summary(self, df: pd.DataFrame) -> str:
        kpis = run_kpis(df, self.config.kpis)
        return generate_summary(self.config.name, kpis, self.get_insights(df))

    def get_dashboard_spec(self, df: pd.DataFrame):
        if self.config.name == "sales_pipeline":
            from services.report_profiles.sales_profile import build_sales_dashboard_spec

            return build_sales_dashboard_spec(df)
        from services.generic_dashboard import build_generic_spec

        return build_generic_spec(df)
