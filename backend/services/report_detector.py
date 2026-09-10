import pandas as pd
from services.business_dictionary import detect_entities


def _name(filename: str | None) -> str:
    return (filename or "").lower()


def detect_report_type(df: pd.DataFrame, filename: str | None = None) -> str:
    """Тип отчёта: сначала имя файла (надёжнее), затем колонки.

    Нельзя считать sales_pipeline всё, где есть «сумма» + заказчик —
    иначе чужие выгрузки получают воронку этапов продаж.
    """
    name = _name(filename)
    if "пдо" in name:
        return "pdo_report"
    if "гарант" in name:
        return "warranty"
    if "прогноз" in name:
        return "sales_forecast"
    if "поставщик" in name and "заказ" in name:
        return "supplier_orders"
    if "поступлен" in name:
        return "planned_receipts"
    if "входящ" in name:
        return "incoming_requests"
    if "дефицит" in name or ("договор" in name and "состояни" in name):
        return "deficit_report"
    if "этап" in name and "продаж" in name:
        return "sales_pipeline"

    cols = [str(c).lower() for c in df.columns]
    joined = " ".join(cols)

    if any(c.endswith("(сумма)") for c in cols) or (
        "сумма по сделке" in joined and any("этап" in c or "канал" in c for c in cols)
    ):
        return "sales_pipeline"

    if any(
        marker in joined
        for marker in (
            "дефицит",
            "неоплачен",
            "не оплачено",
            "сумма долга",
            "задолжен",
        )
    ):
        return "deficit_report"

    if "чел.час" in joined or "готовности" in joined:
        return "pdo_report"
    if "срок гарантии" in joined or "сервисный инженер" in joined:
        return "warranty"
    if "потенциальная сумма" in joined:
        return "sales_forecast"
    if "поставщик" in joined and any("%" in c or "оплат" in c for c in cols):
        return "supplier_orders"

    entities = detect_entities(df.columns.tolist())
    sales_hits = int("revenue" in entities) + int("customer" in entities)
    deficit_hits = int("deficit" in entities) + int("customer" in entities)

    if sales_hits >= 2 and sales_hits > deficit_hits:
        # без колонок воронки это не этапы продаж — универсальный отчёт
        return "unknown"
    if deficit_hits >= 2:
        return "deficit_report"
    return "unknown"
