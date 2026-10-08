import pytest

from services import db_service
from services.exceptions import SlotOccupiedError, UnsupportedReportError
from services.workspace_service import attach_file, create_workspace, get_workspace_meta


def test_attach_sales_and_deficit_any_order(sales_df, deficit_df):
    db_service.init_db()
    from services.storage_service import save_upload
    import io

    sales_id = save_upload(b"sales", "sales.xlsx")
    deficit_id = save_upload(b"deficit", "Дефицит_тест.xlsx")

    def load_df(file_id: str, path: str):
        if file_id == sales_id:
            return sales_df
        return deficit_df

    ws = create_workspace()
    attach_file(ws, deficit_id, load_df, "deficit.xlsx")
    attach_file(ws, sales_id, load_df, "sales.xlsx")
    meta = get_workspace_meta(ws)
    assert meta["sales_file_id"] == sales_id
    assert meta["deficit_file_id"] == deficit_id


def test_attach_rejects_unknown_report(sales_df):
    db_service.init_db()
    from services.storage_service import save_upload

    pdo_id = save_upload(b"pdo", "Отчет ПДО.xlsx")
    ws = create_workspace()

    import pandas as pd

    pdo_df = pd.DataFrame({"чел.час": [1], "заказчик": ["X"]})

    def load_df(file_id: str, path: str):
        return pdo_df if file_id == pdo_id else sales_df

    with pytest.raises(UnsupportedReportError):
        attach_file(ws, pdo_id, load_df, "pdo.xlsx")


def test_attach_slot_occupied_without_replace(sales_df):
    db_service.init_db()
    from services.storage_service import save_upload

    a = save_upload(b"a", "sales_a.xlsx")
    b = save_upload(b"b", "sales_b.xlsx")
    ws = create_workspace()

    def load_df(file_id: str, path: str):
        return sales_df

    attach_file(ws, a, load_df, "a.xlsx")
    with pytest.raises(SlotOccupiedError):
        attach_file(ws, b, load_df, "b.xlsx", replace=False)
