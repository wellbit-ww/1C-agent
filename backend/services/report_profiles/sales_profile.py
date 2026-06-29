import pandas as pd
from typing import Any
from services.report_profiles.base_profile import ReportProfile
from services.data_tools import group_sum, get_top_n, group_by_month
from services.chart_service import create_bar_chart, create_region_chart, create_monthly_trend_chart
from services.column_resolver import resolve_semantic_column

class SalesProfile(ReportProfile):
    def _format_number(self, value: float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}".replace(",", " ")
        return f"{float(value):,.2f}".replace(",", " ")

    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        kpis = []
        kpis.append({"label": "Количество сделок", "value": len(df)})
        
        sum_col = resolve_semantic_column(df, "", semantic="sales", dtype="numeric")
        if sum_col:
            total_sum = df[sum_col].sum()
            avg_check = df[sum_col].mean()
            kpis.append({"label": "Сумма сделок", "value": self._format_number(total_sum)})
            kpis.append({"label": "Средний чек", "value": self._format_number(avg_check)})
            
        client_col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
        if client_col:
            kpis.append({"label": "Количество клиентов", "value": df[client_col].nunique()})
            
        manager_col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")
        if manager_col:
            kpis.append({"label": "Количество менеджеров", "value": df[manager_col].nunique()})
            
        return kpis
        
    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        charts = []
        
        # Продажи по менеджерам
        mgr_data = group_sum(df, "продажи по менеджерам", top_n=10)
        if "error" not in mgr_data:
            charts.append(create_bar_chart(mgr_data, title="Продажи по менеджерам"))
            
        # Продажи по клиентам
        client_data = group_sum(df, "продажи по клиентам", top_n=10)
        if "error" not in client_data:
            charts.append(create_bar_chart(client_data, title="Продажи по клиентам"))
            
        # Продажи по этапам
        # Try to find 'этап' column
        stage_col = next((c for c in df.columns if "этап" in str(c).lower()), None)
        if stage_col:
            stage_data = group_sum(df, f"продажи по {stage_col}")
            if "error" not in stage_data:
                charts.append(create_bar_chart(stage_data, title="Продажи по этапам"))
                
        # Динамика продаж
        trend_data = group_by_month(df, "продажи по месяцам")
        if "error" not in trend_data:
            charts.append(create_monthly_trend_chart(trend_data))
            
        return charts
        
    def get_insights(self, df: pd.DataFrame) -> list[str]:
        insights = []
        
        manager_data = get_top_n(df, "лучший менеджер", semantic="manager", n=1)
        if "groups" in manager_data and manager_data["groups"]:
            top_mgr = next(iter(manager_data["groups"]))
            insights.append(f"Топ менеджер по выручке: {top_mgr}")
            
        client_data = get_top_n(df, "крупнейший клиент", semantic="client", n=1)
        if "groups" in client_data and client_data["groups"]:
            top_client = next(iter(client_data["groups"]))
            insights.append(f"Крупнейший клиент: {top_client}")
            
        stage_col = next((c for c in df.columns if "этап" in str(c).lower()), None)
        sum_col = resolve_semantic_column(df, "", semantic="sales", dtype="numeric")
        if stage_col and sum_col:
            stage_sum = df.groupby(stage_col)[sum_col].sum().sort_values(ascending=False)
            if not stage_sum.empty:
                insights.append(f"Этап с максимальной суммой: {stage_sum.index[0]}")
                
        trend_data = group_by_month(df, "динамика по месяцам")
        if "groups" in trend_data and trend_data["groups"]:
            best_month = max(trend_data["groups"].items(), key=lambda x: x[1])
            insights.append(f"Самый сильный месяц: {best_month[0]}")
            
        return insights
