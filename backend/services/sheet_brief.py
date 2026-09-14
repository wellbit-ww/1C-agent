"""Роль листа книги: рабочие данные, витрина 1С, сводка, справочник.

Цифры витрины 1С нельзя складывать с пересчётом pandas по рабочему листу.
"""
from __future__ import annotations

import pandas as pd

ROLE_LABELS = {
    "data": "рабочие данные",
    "dashboard": "готовая витрина 1С",
    "summary": "сводная таблица 1С",
    "reference": "справочник",
}

_AMOUNT = ("сумма", "долг", "дефицит", "выручк", "оплат", "стоим", "остаток")


def classify_sheet_role(name: str, frame: pd.DataFrame, *, active: bool) -> str:
    n = str(name).lower()
    if any(marker in n for marker in ("дашборд", "dashboard", "витрин")):
        return "dashboard"
    if any(marker in n for marker in ("справоч", "классиф", "словарь")):
        return "reference"
    if active:
        return "data"
    if any(marker in n for marker in ("таблиц",)) and len(frame) <= 30:
        return "summary"
    return "data"


def grain_note(role: str, *, is_active: bool, active_name: str) -> str:
    if role in {"dashboard", "summary"}:
        extra = f" Чат и дашборд агента считают по «{active_name}»." if active_name else ""
        return (
            "Это готовая витрина 1С, другое зерно: числа на листе уже посчитаны "
            f"в 1С, их нельзя складывать с рабочими строками.{extra}"
        )
    if role == "reference":
        return "Справочник, не факты для суммирования."
    if is_active:
        return "Рабочий лист: чат и дашборд считают отсюда."
    if active_name:
        return f"Отдельный лист данных. По умолчанию чат считает по «{active_name}»."
    return "Лист данных."


def _cell(value, limit: int = 40) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    text = str(value).replace("\n", " ").strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def _sample_line(frame: pd.DataFrame) -> str:
    if frame.empty:
        return ""
    row = frame.iloc[0].to_dict()
    bits = []
    for key, value in list(row.items())[:6]:
        text = _cell(value)
        if text:
            bits.append(f"{key}={text}")
    if not bits:
        return ""
    return "На витрине 1С в образце: " + " · ".join(bits)


def _first_amount_col(frame: pd.DataFrame) -> str | None:
    for col in frame.columns:
        name = str(col).lower()
        if not any(marker in name for marker in _AMOUNT):
            continue
        if pd.to_numeric(frame[col], errors="coerce").notna().sum() == 0:
            continue
        return str(col)
    return None


def build_sheet_facts(
    frame: pd.DataFrame,
    role: str,
    *,
    is_active: bool,
    active_name: str,
) -> list[str]:
    facts = [f"{int(len(frame))} строк, {int(len(frame.columns))} колонок"]
    note = grain_note(role, is_active=is_active, active_name=active_name)
    if note:
        facts.append(note)
    if role in {"dashboard", "summary"}:
        sample = _sample_line(frame)
        if sample:
            facts.append(sample)
        return facts[:5]
    if role == "reference":
        cols = ", ".join(f"«{c}»" for c in list(frame.columns)[:6])
        if cols:
            facts.append(f"Колонки: {cols}")
        return facts[:5]
    amount = _first_amount_col(frame)
    if amount and not is_active:
        from services.insights_service import _format_number

        total = float(pd.to_numeric(frame[amount], errors="coerce").sum())
        facts.append(f"Сумма «{amount}» на этом листе: {_format_number(total)}")
        if active_name:
            facts.append(f"Это сумма этого листа, не листа «{active_name}».")
    return facts[:5]


def enrich_sheet_card(
    card: dict,
    frame: pd.DataFrame,
    *,
    active_name: str,
) -> dict:
    role = classify_sheet_role(
        str(card.get("name") or ""),
        frame,
        active=bool(card.get("active")),
    )
    card["role"] = role
    card["role_label"] = ROLE_LABELS[role]
    card["grain_note"] = grain_note(
        role, is_active=bool(card.get("active")), active_name=active_name
    )
    card["facts"] = build_sheet_facts(
        frame,
        role,
        is_active=bool(card.get("active")),
        active_name=active_name,
    )
    return card
