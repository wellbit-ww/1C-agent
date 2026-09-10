"""Дашборд и парсинг новых семейств из examples/."""
from pathlib import Path

import pytest

from services.dashboard_engine import render_spec
from services.excel_service import read_excel
from services.generic_dashboard import build_generic_spec
from services.report_detector import detect_report_type
from services.report_service import get_profile_for_df

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"

SAMPLES = {
    "pdo_report": "Отчет ПДО 01.07.2024 к понедельнику в работе.xlsx",
    "warranty": "Гарантия 2026.xlsx",
    "sales_forecast": "Прогноз продаж СИО+ОАПЛиС на II кв. 2024.xlsx",
    "supplier_orders": "Заказы поставщикам 02_12_2024.xlsx",
    "planned_receipts": "Планируемые поступления ОАПЛиС II - й кв. 2024г..xlsx",
    "incoming_requests": "Входящие запросы_I кв 2024.xlsx",
    "deficit_report": "Дефицит СМЭ_02.12.2024 (1).xlsx",
    "contracts": "2024.03.12 Состояние дел по договорам ОАПЛИС_дефицит.xlsx",
    "xls_deficit": "Дефицит СООК_2 декабря.xls",
}


def _path(name: str) -> Path:
    p = EXAMPLES / name
    if not p.exists():
        pytest.skip(f"нет файла examples/{name}")
    return p


@pytest.mark.parametrize("expected, filename", [
    ("pdo_report", SAMPLES["pdo_report"]),
    ("warranty", SAMPLES["warranty"]),
    ("sales_forecast", SAMPLES["sales_forecast"]),
    ("supplier_orders", SAMPLES["supplier_orders"]),
    ("planned_receipts", SAMPLES["planned_receipts"]),
    ("incoming_requests", SAMPLES["incoming_requests"]),
    ("deficit_report", SAMPLES["deficit_report"]),
    ("deficit_report", SAMPLES["contracts"]),
])
def test_filename_detects_family(expected, filename):
    df = read_excel(str(_path(filename)))
    assert detect_report_type(df, filename=filename) == expected
    assert detect_report_type(df, filename=filename) != "sales_pipeline" or expected == "sales_pipeline"


def test_xls_parses(xls_filename=SAMPLES["xls_deficit"]):
    df = read_excel(str(_path(xls_filename)))
    assert len(df) > 0
    assert len(df.columns) > 3


@pytest.mark.parametrize("filename", list(SAMPLES.values()))
def test_dashboard_has_working_tiles(filename):
    path = _path(filename)
    df = read_excel(str(path))
    _, profile = get_profile_for_df(df, filename=filename)
    spec = profile.get_dashboard_spec(df)
    if spec is None:
        spec = build_generic_spec(df)
    assert spec is not None, f"нет спеки для {filename}"
    rendered = render_spec(df, spec)
    ok = [
        tile
        for tab in rendered["tabs"]
        for tile in tab["tiles"]
        if "plotly_json" in tile
    ]
    assert ok, f"все тайлы с ошибкой: {filename} {rendered}"


def test_sales_pipeline_unchanged(sales_df):
    assert detect_report_type(sales_df, filename="Этапы продаж.xlsx") == "sales_pipeline"
    _, profile = get_profile_for_df(sales_df, filename="Этапы продаж.xlsx")
    spec = profile.get_dashboard_spec(sales_df)
    assert [t.title for t in spec.tabs] == ["Воронка", "Менеджеры", "Клиенты"]
