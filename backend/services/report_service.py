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


# ---------------------------------------------------------------------------
# Полный подробный отчёт (для отдельной страницы в UI)
# ---------------------------------------------------------------------------

_SAMPLE_CELL_LIMIT = 100


def _format_stat_number(value: float) -> str:
    if pd.isna(value):
        return ""
    if float(value).is_integer():
        return f"{int(value):,}".replace(",", " ")
    return f"{float(value):,.2f}".replace(",", " ")


def _safe_sample_cell(value) -> Any:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    if isinstance(value, str) and len(value) > _SAMPLE_CELL_LIMIT:
        return value[:_SAMPLE_CELL_LIMIT] + "…"
    return value


def _data_quality(df: pd.DataFrame) -> dict[str, Any]:
    total_cells = int(df.size)
    null_cells = int(df.isna().sum().sum())
    per_column = df.isna().sum().sort_values(ascending=False)

    worst_columns = [
        {
            "column": str(col),
            "nulls": int(count),
            "pct": round(count / max(len(df), 1) * 100, 1),
        }
        for col, count in per_column.head(5).items()
        if count > 0
    ]

    return {
        "total_cells": total_cells,
        "null_cells": null_cells,
        "null_pct": round(null_cells / max(total_cells, 1) * 100, 1),
        "duplicates": int(df.duplicated().sum()),
        "worst_columns": worst_columns,
    }


def _columns_overview(df: pd.DataFrame) -> list[dict[str, Any]]:
    from services.data_tools import detect_date_columns

    date_cols = set(detect_date_columns(df)["columns"])
    overview: list[dict[str, Any]] = []

    for col in df.columns:
        series = df[col]
        entry: dict[str, Any] = {
            "column": str(col),
            "filled": int(series.notna().sum()),
            "unique": int(series.nunique()),
            "type": "Текст",
            "stats": "",
        }

        if pd.api.types.is_numeric_dtype(series):
            entry["type"] = "Число"
            entry["stats"] = (
                f"мин {_format_stat_number(series.min())} · "
                f"макс {_format_stat_number(series.max())} · "
                f"среднее {_format_stat_number(series.mean())}"
            )
        elif col in date_cols:
            entry["type"] = "Дата"
            parsed = pd.to_datetime(series, errors="coerce", dayfirst=True).dropna()
            if not parsed.empty:
                entry["stats"] = f"{parsed.min():%d.%m.%Y} — {parsed.max():%d.%m.%Y}"
        else:
            top = series.dropna().value_counts().head(3)
            entry["stats"] = "; ".join(f"{name} ({int(count)})" for name, count in top.items())

        overview.append(entry)

    return overview


def get_full_report(df: pd.DataFrame, filename: str | None = None) -> dict[str, Any]:
    """Агрегирует всё для подробного отчёта: KPI, графики, инсайты,
    качество данных, обзор колонок, образец строк и текстовое резюме."""
    from services.insights_service import _get_date_period
    from services.summary_service import build_report_narrative

    report_type, profile = get_profile_for_df(df)

    metadata = {
        "filename": filename,
        "rows": int(len(df)),
        "columns": int(len(df.columns)),
        "period": _get_date_period(df) or "Не определен",
    }

    kpis = profile.get_kpis(df)
    insights = profile.get_insights(df)
    quality = _data_quality(df)

    summary = ""
    if hasattr(profile, "get_summary"):
        summary = profile.get_summary(df)

    sample = [
        {str(k): _safe_sample_cell(v) for k, v in row.items()}
        for row in df.head(20).to_dict(orient="records")
    ]

    return {
        "report_type": report_type,
        "metadata": metadata,
        "summary": summary,
        "narrative": build_report_narrative(report_type, kpis, insights, metadata, quality),
        "kpis": kpis,
        "charts": profile.get_charts(df),
        "insights": insights,
        "data_quality": quality,
        "columns_overview": _columns_overview(df),
        "sample": sample,
    }
