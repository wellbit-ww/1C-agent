"""Выбор файла (сделки vs дефицит) для вопроса в workspace."""
from __future__ import annotations

import re

import pandas as pd

from services.report_detector import detect_report_type
from services.workspace_service import DEFICIT_REPORT, SALES_REPORT

_DEFICIT_HINTS = (
    "дефицит",
    "неоплачен",
    "не оплач",
    "остаток",
    "оплатил",
    "оплачено",
    "задолжен",
    "долг",
    "заказчик",
    "контрагент",
    "сауп",
    "уп-",
)

_SALES_HINTS = (
    "этап",
    "воронк",
    "сделк",
    "зк",
    "заказ",
    "конверси",
    "потенциал",
    "выигран",
    "проигран",
    "отменен",
    "отменён",
    "в работе",
    "полугод",
    "квартал",
    "динамик",
)


def _score_hints(question: str, hints: tuple[str, ...]) -> int:
    text = question.lower().replace("ё", "е")
    return sum(1 for hint in hints if hint in text)


def route_workspace_question(
    question: str,
    sales_df: pd.DataFrame | None,
    deficit_df: pd.DataFrame | None,
    sales_filename: str = "",
    deficit_filename: str = "",
) -> tuple[str, str]:
    """Возвращает (role, reason). role: sales_pipeline | deficit_report."""
    if sales_df is None and deficit_df is None:
        raise ValueError("Нет данных в workspace")
    if sales_df is not None and deficit_df is None:
        return SALES_REPORT, "single_sales"
    if deficit_df is not None and sales_df is None:
        return DEFICIT_REPORT, "single_deficit"

    deficit_score = _score_hints(question, _DEFICIT_HINTS)
    sales_score = _score_hints(question, _SALES_HINTS)

    from services.chat_lookup import wants_order_lookup

    if wants_order_lookup(question):
        deficit_score += 2

    if re.search(r"уп-\d", question.lower()):
        sales_score += 1

    if deficit_score > sales_score:
        return DEFICIT_REPORT, "keywords_deficit"
    if sales_score > deficit_score:
        return SALES_REPORT, "keywords_sales"

    # Неясно — по умолчанию сделки, если есть оба (меньше риска путаницы с оплатами в sales)
    if "дефицит" in (deficit_filename or "").lower():
        return DEFICIT_REPORT, "default_deficit_filename"
    return SALES_REPORT, "default_sales"


def pick_dataframe(
    role: str,
    sales_df: pd.DataFrame | None,
    deficit_df: pd.DataFrame | None,
) -> pd.DataFrame:
    if role == DEFICIT_REPORT:
        if deficit_df is None:
            raise ValueError("В workspace нет файла дефицита")
        return deficit_df
    if sales_df is None:
        raise ValueError("В workspace нет файла сделок")
    return sales_df
