from services.chat_workspace_router import route_workspace_question


def test_route_deficit_keywords(deficit_df, sales_df):
    role, reason = route_workspace_question(
        "Сколько неоплаченный остаток у Алабуги?",
        sales_df,
        deficit_df,
        "sales.xlsx",
        "Дефицит_тест.xlsx",
    )
    assert role == "deficit_report"
    assert reason == "keywords_deficit"


def test_route_sales_keywords(deficit_df, sales_df):
    role, _ = route_workspace_question(
        "Динамика сделок по кварталам и конверсия ЗК",
        sales_df,
        deficit_df,
    )
    assert role == "sales_pipeline"
