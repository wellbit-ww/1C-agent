import pandas as pd
from typing import Any
from services.profile_registry import get_profile, ReportConfig
from services.kpi_engine import run_kpis
from services.chart_service import (
    create_bar_chart, create_pie_chart, create_line_chart,
    create_monthly_trend_chart, create_region_chart, create_manager_chart, create_top_clients_chart
)
from services.data_tools import group_sum, get_top_n, group_by_month
from services.summary_service import generate_summary

class ReportEngine:
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
            "row_count": "Количество строк"
        }
        translated = []
        for k in kpis:
            translated.append({
                "label": translations.get(k["name"], k["name"]),
                "value": k["formatted"],
                "raw_value": k["value"],
                "name": k["name"]
            })
        return translated

    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        raw_kpis = run_kpis(df, self.config.kpis)
        return self._translate_kpis(raw_kpis)
        
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
                # For deficit we need a column
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
        for ins in self.config.insights:
            if ins == "top_customer":
                data = get_top_n(df, "лучший клиент", semantic="client", n=1)
                if "groups" in data and data["groups"]:
                    top_name = next(iter(data["groups"]))
                    insights.append(f"Топ клиент: {top_name}")
            elif ins == "top_manager":
                data = get_top_n(df, "лучший менеджер", semantic="manager", n=1)
                if "groups" in data and data["groups"]:
                    top_name = next(iter(data["groups"]))
                    insights.append(f"Топ менеджер: {top_name}")
            elif ins == "best_month":
                data = group_by_month(df, "динамика по месяцам")
                if "groups" in data and data["groups"]:
                    best_m = max(data["groups"].items(), key=lambda x: x[1])
                    insights.append(f"Самый сильный месяц: {best_m[0]}")
            elif ins == "top_department":
                data = group_sum(df, "дефицит по подразделениям", top_n=1)
                if "groups" in data and data["groups"]:
                    top_name = next(iter(data["groups"]))
                    insights.append(f"Топ подразделение по дефициту: {top_name}")
        return insights
        
    def get_summary(self, df: pd.DataFrame) -> str:
        kpis = run_kpis(df, self.config.kpis)
        insights = self.get_insights(df)
        return generate_summary(self.config.name, kpis, insights)
