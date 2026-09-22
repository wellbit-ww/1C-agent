"""Юнит-тесты семантического резолвера колонок."""
from services.column_resolver import resolve_group_and_value_columns, resolve_semantic_column


class TestSalesFile:
    def test_revenue(self, sales_df):
        col = resolve_semantic_column(sales_df, "общая выручка", "revenue", dtype="numeric")
        assert col == "сумма по сделке"

    def test_client(self, sales_df):
        col = resolve_semantic_column(sales_df, "топ клиентов", "client", dtype="categorical")
        assert col == "компания"

    def test_manager(self, sales_df):
        col = resolve_semantic_column(sales_df, "продажи по менеджерам", "manager", dtype="categorical")
        assert col == "ответственный"

    def test_manager_russian_semantic(self, sales_df):
        col = resolve_semantic_column(
            sales_df, "топ ответственных", "ответственный", dtype="categorical"
        )
        assert col == "ответственный"

    def test_department(self, sales_df):
        col = resolve_semantic_column(sales_df, "разбивка по службам", "department", dtype="categorical")
        assert col == "подразделение"

    def test_group_and_value(self, sales_df):
        group, value = resolve_group_and_value_columns(sales_df, "выручка по менеджерам")
        assert group == "ответственный"
        assert value == "сумма по сделке"


class TestLexicalBeatsProfileMetric:
    def test_order_sum_not_next_payment(self):
        import pandas as pd
        from services.column_resolver import resolve_column
        from services.data_tools import get_sum

        df = pd.DataFrame(
            {
                "сумма заказа": [100.0, 200.0],
                "сумма очередного платежа, руб": [10.0, 20.0],
            }
        )
        assert resolve_column(df, "Какая общая сумма заказов?", dtype="numeric") == "сумма заказа"
        result = get_sum(df, "Какая общая сумма заказов?")
        assert result["column"] == "сумма заказа"
        assert result["value"] == 300.0


class TestDeficitFile:
    def test_deficit_semantic(self, deficit_df):
        col = resolve_semantic_column(deficit_df, "общий дефицит", "deficit", dtype="numeric")
        assert col == "неоплаченный остаток"

    def test_compound_deficit_not_paid(self, deficit_df):
        from services.data_tools import get_sum

        result = get_sum(deficit_df, "Какой общий дефицит и кто топ-заказчик?")
        assert result["column"] == "неоплаченный остаток"

    def test_client(self, deficit_df):
        col = resolve_semantic_column(deficit_df, "топ заказчиков", "client", dtype="categorical")
        assert col is not None
        assert col in deficit_df.columns
