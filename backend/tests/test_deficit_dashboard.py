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


class TestDeficitDashboard:
    def test_spec_has_payment_charts(self):
        spec = build_deficit_dashboard_spec(_ks_df())
        assert [tab.title for tab in spec.tabs] == ["Платежи", "Структура"]
        pay = spec.tabs[0]
        titles = [tile.title for tile in pay.tiles]
        assert "К оплате" in titles
        assert "Оплачено и неоплаченный остаток" in titles
        to_pay = next(t for t in pay.tiles if t.title == "К оплате")
        assert to_pay.source.kind == "named_columns"
        assert len(to_pay.source.column_names) == 3

        rendered = render_spec(_ks_df(), spec)
        pay_out = rendered["tabs"][0]["tiles"]
        assert all("plotly_json" in tile for tile in pay_out)
        k_oplate = next(t for t in pay_out if t["title"] == "К оплате")
        fig = json.loads(k_oplate["plotly_json"])
        labels = list(fig["data"][0]["x"])
        assert labels == ["До обеспечения", "До отгрузки", "После отгрузки"]

    def test_real_deficit_file(self, deficit_df):
        _, profile = get_profile_for_df(deficit_df, filename="Дефицит по КС.xlsx")
        kpis = {item["label"]: item["raw_value"] for item in profile.get_kpis(deficit_df)}
        assert "К оплате · До обеспечения" in kpis
        assert "К оплате · До отгрузки" in kpis
        assert "К оплате · После отгрузки" in kpis
        assert kpis["Неоплаченный остаток"] == pytest.approx(374_617_149.86, rel=1e-6)
        spec = profile.get_dashboard_spec(deficit_df)
        rendered = render_spec(deficit_df, spec)
        pay = next(tab for tab in rendered["tabs"] if tab["title"] == "Платежи")
        ok = [t for t in pay["tiles"] if "plotly_json" in t]
        assert len(ok) >= 3


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
    pay = next(tab for tab in rendered["tabs"] if tab["title"] == "Платежи")
    assert all("error" not in t for t in pay["tiles"])
