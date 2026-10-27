"""Дашборд типового дефицита: блоки «К оплате», оплачено, остаток."""
import json

import pandas as pd
import pytest

from services.dashboard_engine import render_spec
from services.excel_service import read_excel
from services.report_profiles.deficit_profile import (
    build_deficit_dashboard_spec,
    deficit_kpis,
    detect_deficit_money_layout,
)
from services.report_service import get_profile_for_df


def _ks_df():
    return pd.DataFrame(
        {
            "заказчик": ["А", "Б", "В"],
            "подразделение": ["СТО", "СТЕ", "СТО"],
            "ответственный": ["Иванов", "Петров", "Иванов"],
            "сумма по заказу в рублях": [100.0, 200.0, 50.0],
            "к оплате — До обеспечения": [10.0, None, 5.0],
            "к оплате — До отгрузки": [40.0, 80.0, None],
            "к оплате — После отгрузки": [50.0, 120.0, 45.0],
            "оплачено по заказу": [60.0, 100.0, 20.0],
            "неоплаченный остаток": [40.0, 100.0, 30.0],
        }
    )


class TestDeficitLayout:
    def test_detects_ks_blocks(self):
        layout = detect_deficit_money_layout(_ks_df())
        assert [label for label, _ in layout.stages] == [
            "До обеспечения",
            "До отгрузки",
            "После отгрузки",
        ]
        assert layout.paid == "оплачено по заказу"
        assert layout.unpaid == "неоплаченный остаток"
        assert layout.order_sum == "сумма по заказу в рублях"

    def test_sme_paid_unpaid_without_stages(self):
        df = pd.DataFrame(
            {
                "заказчик": ["А", "Б"],
                "оплачено": [10.0, 20.0],
                "не оплачено": [5.0, 15.0],
                "сумма заказа": [15.0, 35.0],
            }
        )
        layout = detect_deficit_money_layout(df)
        assert layout.stages == ()
        assert layout.paid == "оплачено"
        assert layout.unpaid == "не оплачено"
        assert layout.order_sum == "сумма заказа"


class TestDeficitKpis:
    def test_payment_blocks_are_sums(self):
        kpis = {item["label"]: item["raw_value"] for item in deficit_kpis(_ks_df())}
        assert kpis["К оплате · До обеспечения"] == pytest.approx(15.0)
        assert kpis["К оплате · До отгрузки"] == pytest.approx(120.0)
        assert kpis["К оплате · После отгрузки"] == pytest.approx(215.0)
        assert kpis["Оплачено по заказу"] == pytest.approx(180.0)
        assert kpis["Неоплаченный остаток"] == pytest.approx(170.0)
        assert kpis["Сумма заказов, руб."] == pytest.approx(350.0)


def test_contract_deficit_uses_summa_vsego_not_stage_rub():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "examples" / (
        "2024.03.12 Состояние дел по договорам ОАПЛИС_дефицит.xlsx"
    )
    if not path.exists():
        pytest.skip("нет файла в examples/")
    df = read_excel(str(path))
    layout = detect_deficit_money_layout(df)
    assert layout.order_sum == "сумма всего"
    assert layout.unpaid == "сумма долга"
    kpis = {item["label"]: item["raw_value"] for item in deficit_kpis(df)}
    assert kpis["Сумма заказов, руб."] == pytest.approx(35_363_915.0)
    assert kpis["Неоплаченный остаток"] == pytest.approx(17_495_275.0)
    from services.report_engine import ReportEngine
    from services.profile_registry import get_profile

    summary = ReportEngine(get_profile("deficit_report")).get_summary(df)
    assert "общий дефицит на сумму 17 495 275" in summary


def test_sook_xls_order_sum_is_summa_zakaza_not_next_payment():
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "examples" / "Дефицит СООК_2 декабря.xls"
    if not path.exists():
        pytest.skip("нет файла в examples/")
    df = read_excel(str(path))
    layout = detect_deficit_money_layout(df)
    assert layout.order_sum == "сумма заказа"
    kpis = {item["label"]: item["raw_value"] for item in deficit_kpis(df)}
    assert kpis["Сумма заказов, руб."] == pytest.approx(10_674_920.0)
    spec = build_deficit_dashboard_spec(df)
    titles = [tile.title for tab in spec.tabs for tile in tab.tiles]
    assert all("Неоплаченный остаток" not in title for title in titles)
    from services.report_engine import ReportEngine
    from services.profile_registry import get_profile

    summary = ReportEngine(get_profile("deficit_report")).get_summary(df)
    assert "10 674 920" in summary
    assert "общий дефицит на сумму 0" not in summary
    insights = "\n".join(ReportEngine(get_profile("deficit_report")).get_insights(df))
    assert "неоплаченному остатку" not in insights


class TestDeficitDashboard:
    def test_spec_sales_only_no_payment_charts(self):
        spec = build_deficit_dashboard_spec(_ks_df())
        assert [tab.title for tab in spec.tabs] == ["Платежи", "Структура"]
        assert [t.source.kind for t in spec.tabs[0].tiles] == [
            "deficit_sales_by_department",
            "deficit_sales_summary",
            "deficit_sales_forecast_vitrine",
            "deficit_sales_fulfillment_board",
            "deficit_sales_share",
        ]
        assert [t.source.kind for t in spec.tabs[1].tiles] == [
            "deficit_payment_receipt",
            "deficit_payment_by_department",
            "deficit_payment_fulfillment_board",
            "deficit_payment_share",
        ]

        rendered = render_spec(_ks_df(), spec)
        sales_tile = rendered["tabs"][0]["tiles"][0]
        assert sales_tile["chart_type"] == "table"
        assert sales_tile["table"]["totals_label"] == "Всего"
        structure = next(tab for tab in rendered["tabs"] if tab["title"] == "Структура")
        assert len(structure["tiles"]) == 4

    def test_real_deficit_file(self, deficit_df):
        _, profile = get_profile_for_df(deficit_df, filename="Дефицит по КС.xlsx")
        kpis = {item["label"]: item["raw_value"] for item in profile.get_kpis(deficit_df)}
        assert "К оплате · До обеспечения" in kpis
        assert "К оплате · До отгрузки" in kpis
        assert "К оплате · После отгрузки" in kpis
        assert kpis["Неоплаченный остаток"] == pytest.approx(374_617_149.86, rel=1e-6)
        spec = profile.get_dashboard_spec(deficit_df)
        rendered = render_spec(deficit_df, spec)
        assert [tab["title"] for tab in rendered["tabs"]] == ["Платежи", "Структура"]
        assert rendered["tabs"][0]["tiles"][0]["chart_type"] == "table"
        assert [t.source.kind for t in spec.tabs[1].tiles] == [
            "deficit_payment_receipt",
            "deficit_payment_by_department",
            "deficit_payment_fulfillment_board",
            "deficit_payment_share",
        ]


def test_desktop_ks_file_if_present():
    from pathlib import Path

    path = Path(r"c:\Users\USER2\Desktop\08.06.2026_Дефицит по КС.xlsx")
    if not path.exists():
        pytest.skip("нет файла на рабочем столе")
    df = read_excel(str(path))
    layout = detect_deficit_money_layout(df)
    assert len(layout.stages) == 3
    kpis = {item["label"]: item["raw_value"] for item in deficit_kpis(df)}
    assert kpis["Неоплаченный остаток"] > 0
    spec = build_deficit_dashboard_spec(df)
    rendered = render_spec(df, spec)
    pay_tab = next(tab for tab in rendered["tabs"] if tab["title"] == "Платежи")
    assert pay_tab["tiles"][0]["chart_type"] == "table"
    structure = next(tab for tab in rendered["tabs"] if tab["title"] == "Структура")
    assert structure["tiles"]
