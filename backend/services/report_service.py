import pandas as pd
from typing import Any
import logging

from services.report_detector import detect_report_type
from services.report_profiles.sales_profile import SalesProfile
from services.report_profiles.deficit_profile import DeficitProfile
from services.report_profiles.base_profile import ReportProfile

from services.profile_registry import get_profile
from services.report_engine import ReportEngine
from services.business_dictionary import detect_entities

# Setting up basic logging
logger = logging.getLogger(__name__)

_old_profiles = {
    "sales_pipeline": SalesProfile(),
    "deficit_report": DeficitProfile(),
}

class DefaultProfile(ReportProfile):
    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        return [
            {"label": "Количество строк", "value": len(df)},
            {"label": "Количество колонок", "value": len(df.columns)},
        ]
        
    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        from services.chart_service import create_bar_chart
        
        charts = []
        numeric_cols = df.select_dtypes(include=['number']).columns.tolist()
        cat_cols = df.select_dtypes(include=['object', 'string']).columns.tolist()
        
        if numeric_cols and cat_cols:
            num_col = numeric_cols[0]
            cat_col = cat_cols[0]
            
            # Group by cat_col and sum num_col, get top 10
            grouped = df.groupby(cat_col)[num_col].sum().sort_values(ascending=False).head(10)
            
            data = {
                "groups": grouped.to_dict(),
                "group_column": cat_col,
                "value_column": num_col
            }
            
            charts.append(create_bar_chart(data, title=f"ТОП-10 {cat_col} по {num_col}"))
            
        return charts

    def get_insights(self, df: pd.DataFrame) -> list[str]:
        from services.insights_service import get_basic_insights
        return get_basic_insights(df)

_default_profile = DefaultProfile()

class ConfigAdapterProfile(ReportProfile):
    """Adapter to map new Config-Driven ReportEngine to the old ReportProfile interface."""
    def __init__(self, engine: ReportEngine):
        self.engine = engine

    def get_kpis(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        return self.engine.get_kpis(df)

    def get_charts(self, df: pd.DataFrame) -> list[dict[str, Any]]:
        return self.engine.get_charts(df)

    def get_insights(self, df: pd.DataFrame) -> list[str]:
        return self.engine.get_insights(df)
        
    def get_summary(self, df: pd.DataFrame) -> str:
        return self.engine.get_summary(df)

def get_profile_for_df(df: pd.DataFrame) -> tuple[str, ReportProfile]:
    report_type = detect_report_type(df)
    logger.info(f"Обнаружен тип отчета: {report_type}")
    
    entities = detect_entities(df.columns.tolist())
    logger.info(f"Найденные бизнес-сущности: {entities}")

    # 1. Try to load from Metadata Registry
    config = get_profile(report_type)
    if config:
        logger.info(f"Используется Metadata Profile: {config.name}")
        engine = ReportEngine(config)
        return report_type, ConfigAdapterProfile(engine)

    # 2. Fallback to Old Hardcoded Profiles
    logger.info("Metadata Profile не найден, fallback на старые профили.")
    profile = _old_profiles.get(report_type, _default_profile)
    return report_type, profile
