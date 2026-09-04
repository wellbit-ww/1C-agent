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


class TestDeficitFile:
    def test_deficit_semantic(self, deficit_df):
        col = resolve_semantic_column(deficit_df, "общий дефицит", "deficit", dtype="numeric")
        assert col is not None
        assert col in deficit_df.columns

    def test_client(self, deficit_df):
        col = resolve_semantic_column(deficit_df, "топ заказчиков", "client", dtype="categorical")
        assert col is not None
        assert col in deficit_df.columns
