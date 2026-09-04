import pandas as pd
from typing import Any
from services.report_profiles.base_profile import ReportProfile
from services.data_tools import group_sum, get_top_n
from services.chart_service import create_bar_chart, create_pie_chart
from services.column_resolver import resolve_semantic_column

class DeficitProfile(ReportProfile):
    def _format_number(self, value: float) -> str:
        if float(value).is_integer():
            return f"{int(value):,}".replace(",", " ")
        return f"{float(value):,.2f}".replace(",", " ")

    def _get_deficit_col(self, df: pd.DataFrame) -> str | None:
        for col in df.columns:
            if "дефицит" in str(col).lower() or "остаток" in str(col).lower() or "задолженность" in str(col).lower():
                if pd.api.types.is_numeric_dtype(df[col]):
                    return col
        return resolve_semantic_column(df, "", semantic="amount", dtype="numeric")

    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        kpis = []
        
        def_col = self._get_deficit_col(df)
        if def_col:
            total_def = df[def_col].sum()
            kpis.append({"label": "Общий дефицит", "value": self._format_number(total_def)})
            
        client_col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
        if client_col:
            kpis.append({"label": "Количество заказчиков", "value": df[client_col].nunique()})
            
        dept_col = next((c for c in df.columns if "подразделение" in str(c).lower()), None)
        if dept_col:
            kpis.append({"label": "Количество подразделений", "value": df[dept_col].nunique()})
            
        return kpis
        
    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        charts = []
        
        def_col = self._get_deficit_col(df)
        if not def_col:
            return charts
            
        dept_col = next((c for c in df.columns if "подразделение" in str(c).lower()), None)
        if dept_col:
            dept_data = df.groupby(dept_col)[def_col].sum().sort_values(ascending=False).head(10)
            charts.append(create_pie_chart({"groups": dept_data.to_dict()}, title="Дефицит по подразделениям"))
            
        mgr_col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")
        if mgr_col:
            mgr_data = df.groupby(mgr_col)[def_col].sum().sort_values(ascending=False).head(10)
            charts.append(create_bar_chart({"groups": mgr_data.to_dict()}, title="Дефицит по менеджерам"))
            
        client_col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
        if client_col:
            client_data = df.groupby(client_col)[def_col].sum().sort_values(ascending=False).head(10)
            charts.append(create_bar_chart({"groups": client_data.to_dict()}, title="Топ заказчиков по дефициту"))
            
        return charts
        
    def _top_group(self, df: pd.DataFrame, group_col: str, value_col: str):
        grouped = df.groupby(group_col)[value_col].sum()
        clean = grouped.dropna()
        if clean.empty:
            return None
        return clean.idxmax()

    def get_insights(self, df: pd.DataFrame) -> list[str]:
        insights = []
        
        def_col = self._get_deficit_col(df)
        if not def_col:
            return insights
            
        dept_col = next((c for c in df.columns if "подразделение" in str(c).lower()), None)
        if dept_col:
            top_dept = self._top_group(df, dept_col, def_col)
            if top_dept is not None:
                insights.append(f"Подразделение с максимальным дефицитом: {top_dept}")
            
        mgr_col = resolve_semantic_column(df, "", semantic="manager", dtype="categorical")
        if mgr_col:
            top_mgr = self._top_group(df, mgr_col, def_col)
            if top_mgr is not None:
                insights.append(f"Менеджер с максимальным дефицитом: {top_mgr}")
            
        client_col = resolve_semantic_column(df, "", semantic="client", dtype="categorical")
        if client_col:
            top_client = self._top_group(df, client_col, def_col)
            if top_client is not None:
                insights.append(f"ТОП заказчик по дефициту: {top_client}")
            
        return insights

    def get_dashboard_spec(self, df: pd.DataFrame):
        from services.generic_dashboard import build_generic_spec
        return build_generic_spec(df)
