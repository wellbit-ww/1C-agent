"""Тесты движка дашбордов: spec-валидация, воронка, единицы, рендер."""
import json

import pandas as pd
import pytest
from pydantic import ValidationError

from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services.dashboard_engine import _fmt_scaled, render_spec


def _tile(**overrides) -> Tile:
    base = {
        "title": "Тест",
        "chart_type": "bar",
        "source": {"kind": "group", "group_column": "компания", "value_column": "сумма по сделке"},
    }
    base.update(overrides)
    return Tile(**base)


class TestSpecValidation:
    def test_valid_spec(self):
        spec = DashboardSpec(tabs=[Tab(title="Вкладка", tiles=[_tile()])])
        assert spec.tabs[0].tiles[0].title == "Тест"

    def test_rejects_bad_chart_type(self):
        with pytest.raises(ValidationError):
            _tile(chart_type="scatter3d")

    def test_rejects_empty_tabs(self):
        with pytest.raises(ValidationError):
            DashboardSpec(tabs=[])


class TestUnitFormatting:
    @pytest.mark.parametrize(
        "value, scale, suffix, expected",
        [
            (18_254_222_243.3, 1e9, "млрд", "18.25 млрд"),
            (7_902_260.7, 1e6, "млн", "7.9 млн"),
            (2_500.0, 1e3, "тыс.", "2.5 тыс."),
            (42.0, 1.0, "", "42"),
        ],
    )
    def test_fmt_scaled(self, value, scale, suffix, expected):
        assert _fmt_scaled(value, scale, suffix) == expected


class TestFunnel:
    def test_columns_pattern_sums(self, sales_df):
        tile = _tile(
            title="Этапы по сумме",
            chart_type="hbar",
            source={"kind": "columns_pattern", "columns_pattern": "(сумма)"},
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out

        # контрольная сумма: значение этапа = сумме соответствующей колонки
        stats = tile_out["stats"]
        assert stats["count"] == 10  # десять этапов воронки
        expected_new = float(sales_df["новая сделка (сумма)"].sum())
        fig = json.loads(tile_out["plotly_json"])
        labels = fig["data"][0]["y"]  # hbar: категории по y
        values_scaled = fig["data"][0]["x"]
        idx = labels.index("новая сделка")
        assert values_scaled[idx] * 1e9 == pytest.approx(expected_new, rel=0.01)

    def test_current_stage_counts_deals_not_qty_column(self, sales_df):
        tile = _tile(
            title="Текущий этап",
            chart_type="hbar",
            source={"kind": "current_stage", "columns_pattern": "(сумма)"},
            agg="count",
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out
        fig = json.loads(tile_out["plotly_json"])
        labels = list(fig["data"][0]["y"])
        values = list(fig["data"][0]["x"])
        # hbar переворачивает: первая стадия сверху
        assert labels[-1] == "новая сделка"
        assert values[-1] == pytest.approx(1002)

    def test_current_stage_uses_deal_amount(self, sales_df):
        tile = _tile(
            title="Сумма на этапе",
            chart_type="hbar",
            source={
                "kind": "current_stage",
                "columns_pattern": "(сумма)",
                "value_column": "сумма по сделке",
            },
            sort="none",
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        attributed = tile_out["stats"]["total"]
        sum_cols = [c for c in sales_df.columns if str(c).endswith("(сумма)")]
        has_stage = (
            sales_df[sum_cols]
            .apply(pd.to_numeric, errors="coerce")
            .fillna(0)
            .ne(0)
            .any(axis=1)
        )
        expected = float(sales_df.loc[has_stage, "сумма по сделке"].sum())
        assert attributed == pytest.approx(expected, rel=0.001)
        assert expected < float(sales_df["сумма по сделке"].sum())

    def test_current_stage_not_equal_to_column_sum_when_cumulative(self):
        df = pd.DataFrame(
            {
                "сумма по сделке": [100.0, 200.0],
                "лид (сумма)": [100.0, 200.0],
                "оплата (сумма)": [pd.NA, 200.0],
            }
        )
        stock = _tile(
            chart_type="hbar",
            source={
                "kind": "current_stage",
                "columns_pattern": "(сумма)",
                "value_column": "сумма по сделке",
            },
            sort="none",
        )
        throughput = _tile(
            chart_type="hbar",
            source={"kind": "columns_pattern", "columns_pattern": "(сумма)"},
            sort="none",
        )
        spec = DashboardSpec(tabs=[Tab(title="T", tiles=[throughput, stock])])
        tiles = render_spec(df, spec)["tabs"][0]["tiles"]
        thru = json.loads(tiles[0]["plotly_json"])
        curr = json.loads(tiles[1]["plotly_json"])
        thru_map = dict(zip(thru["data"][0]["y"], thru["data"][0]["x"]))
        curr_map = dict(zip(curr["data"][0]["y"], curr["data"][0]["x"]))
        assert thru_map["лид"] == pytest.approx(300)
        assert curr_map["лид"] == pytest.approx(100)
        assert curr_map["оплата"] == pytest.approx(200)


class TestRender:
    def test_group_bar(self, sales_df):
        result = render_spec(
            sales_df,
            DashboardSpec(tabs=[Tab(title="T", tiles=[_tile(top_n=5)])]),
        )
        tile_out = result["tabs"][0]["tiles"][0]
        assert tile_out["chart_type"] == "bar"
        fig = json.loads(tile_out["plotly_json"])
        assert len(fig["data"][0]["x"]) == 5
        assert tile_out["stats"]["top"][0][0] == "АЛАБУГА МАШИНЕРИ ООО"

    def test_mean_agg(self, sales_df):
        tile = _tile(agg="mean", unit="mln")
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        stats = result["tabs"][0]["tiles"][0]["stats"]
        # средний чек топ-клиента должен быть положительным и разумным
        assert stats["top"][0][1] > 0

    def test_target_line_rendered(self, sales_df):
        tile = _tile(target_line=18_254_222_243.3, unit="mlrd")
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        fig = json.loads(result["tabs"][0]["tiles"][0]["plotly_json"])
        assert fig["layout"].get("shapes"), "линия-ориентир должна быть на фигуре"

    def test_period_tile(self, sales_df):
        tile = _tile(
            title="Динамика",
            chart_type="area",
            source={"kind": "period", "period": "month", "value_column": "сумма по сделке"},
        )
        result = render_spec(sales_df, DashboardSpec(tabs=[Tab(title="T", tiles=[tile])]))
        tile_out = result["tabs"][0]["tiles"][0]
        assert "error" not in tile_out
        assert tile_out["stats"]["count"] == 15  # 15 месяцев в файле

    def test_bad_tile_does_not_crash_dashboard(self, sales_df):
        bad = _tile(title="Битый", source={"kind": "group", "group_column": "несуществующая"})
        good = _tile()
        result = render_spec(
            sales_df,
            DashboardSpec(tabs=[Tab(title="T", tiles=[bad, good])]),
        )
        tiles = result["tabs"][0]["tiles"]
        assert "error" in tiles[0]
        assert "plotly_json" in tiles[1]
