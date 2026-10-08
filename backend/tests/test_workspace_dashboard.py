from services import db_service
from services.workspace_service import (
    attach_file,
    build_workspace_dashboard,
    create_workspace,
)


def test_workspace_dashboard_isolates_sources(sales_df, deficit_df):
    db_service.init_db()
    from services.storage_service import save_upload

    sales_id = save_upload(b"sales", "sales.xlsx")
    deficit_id = save_upload(b"def", "Дефицит_тест.xlsx")

    def load_df(file_id: str, path: str):
        return sales_df if file_id == sales_id else deficit_df

    def get_path(file_id: str) -> str:
        return file_id

    ws = create_workspace()
    attach_file(ws, sales_id, load_df, sales_id)
    attach_file(ws, deficit_id, load_df, deficit_id)

    payload = build_workspace_dashboard(ws, load_df, get_path)
    roles = {src["role"] for src in payload["sources"]}
    assert roles == {"sales_pipeline", "deficit_report"}

    sales_src = next(s for s in payload["sources"] if s["role"] == "sales_pipeline")
    deficit_src = next(s for s in payload["sources"] if s["role"] == "deficit_report")

    sales_titles = {tab["title"] for tab in sales_src["tabs"]}
    assert "Этапы продаж" in sales_titles or any("Этап" in t for t in sales_titles)

    deficit_titles = {tab["title"] for tab in deficit_src["tabs"]}
    assert "Платежи" in deficit_titles or "Структура" in deficit_titles

    for tab in sales_src["tabs"]:
        assert tab.get("source_file_id") == sales_id
    for tab in deficit_src["tabs"]:
        assert tab.get("source_file_id") == deficit_id

    assert sales_src["metadata"]["rows"] == len(sales_df)
    assert deficit_src["metadata"]["rows"] == len(deficit_df)
