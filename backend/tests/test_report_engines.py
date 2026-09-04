"""Прямые тесты kpi_engine, report_engine и summary_service."""
import pandas as pd

from services.kpi_engine import run_kpis
from services.profile_registry import get_profile
from services.report_engine import ReportEngine
from services.summary_service import build_report_narrative, generate_summary


def _sales_df():
    return pd.DataFrame(
        {
            "компания": ["Альфа", "Бета", "Альфа"],
            "ответственный": ["Иванов", "Петров", "Иванов"],
            "сумма по сделке": [100.0, 50.0, 25.0],
            "дата начала сделки": ["2024-01-01", "2024-02-01", "2024-03-01"],
        }
    )


def _deficit_df():
    return pd.DataFrame(
        {
            "заказчик": ["X", "Y", "Z"],
            "подразделение": ["А", "Б", "А"],
            "менеджер": ["Иванов", "Петров", "Иванов"],
            "дефицит": [10.0, 50.0, 5.0],
        }
    )


class TestKpiEngine:
    def test_run_known_kpis(self):
        df = _sales_df()
        results = run_kpis(df, ["row_count", "total_revenue", "unique_customers"])
        names = {item["name"]: item for item in results}
        assert names["row_count"]["value"] == 3
        assert names["total_revenue"]["value"] == 175.0
        assert names["unique_customers"]["value"] == 2

    def test_skips_unknown_and_missing(self):
        df = pd.DataFrame({"текст": ["a", "b"]})
        results = run_kpis(df, ["no_such_kpi", "total_revenue", "row_count"])
        assert [item["name"] for item in results] == ["row_count"]


class TestReportEngine:
    def test_sales_kpis_and_insights(self):
        config = get_profile("sales_pipeline")
        engine = ReportEngine(config)
        df = _sales_df()
        kpis = engine.get_kpis(df)
        labels = [k["label"] for k in kpis]
        assert "Общая выручка" in labels
        insights = engine.get_insights(df)
        assert any("Альфа" in text or "Иванов" in text for text in insights)
        spec = engine.get_dashboard_spec(df)
        assert spec.tabs

    def test_deficit_top_department(self):
        config = get_profile("deficit_report")
        engine = ReportEngine(config)
        insights = engine.get_insights(_deficit_df())
        assert any("Б" in text for text in insights)

    def test_deficit_all_nan_group_is_empty(self):
        config = get_profile("deficit_report")
        engine = ReportEngine(config)
        df = pd.DataFrame(
            {
                "подразделение": [None, None],
                "дефицит": [10.0, 20.0],
                "менеджер": [None, None],
                "заказчик": [None, None],
            }
        )
        assert engine.get_insights(df) == []


class TestSummaryService:
    def test_generate_summary_sales(self):
        text = generate_summary(
            "sales_pipeline",
            [
                {"name": "total_revenue", "formatted": "175"},
                {"name": "average_check", "formatted": "58"},
            ],
            ["Топ клиент: Альфа"],
        )
        assert "175" in text
        assert "Альфа" in text

    def test_build_report_narrative(self):
        text = build_report_narrative(
            "deficit_report",
            [{"label": "Общий дефицит", "formatted": "65"}],
            ["Топ подразделение по дефициту: Б"],
            {"filename": "deficit.xlsx", "rows": 3, "columns": 4, "period": "2024"},
            {
                "null_pct": 0.0,
                "null_cells": 0,
                "total_cells": 12,
                "duplicates": 0,
                "worst_columns": [],
            },
        )
        assert "Дефицит" in text
        assert "Б" in text
        assert "РЕКОМЕНДАЦИИ" in text
