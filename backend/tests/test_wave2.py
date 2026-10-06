"""Волна 2: follow-up по периоду, сравнение с метрикой, fallback без числа."""
from pathlib import Path

import pytest

from services import chat_service
from services.excel_service import read_excel

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "examples"
SALES_ALL = ROOT / "backend" / "uploads" / "4d54079843af4c23acb95e716712d5a2.xlsx"
PDO = EXAMPLES / "Отчет ПДО 01.07.2024 к понедельнику в работе.xlsx"
WARRANTY = EXAMPLES / "Гарантия 2026.xlsx"


def _ban_router(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("быстрый путь не должен звать роутер")

    monkeypatch.setattr(chat_service, "_llm_classify", fail)
    monkeypatch.setattr(chat_service, "classify", fail)


def test_period_followup_keeps_deals_intent(monkeypatch):
    if not SALES_ALL.exists():
        pytest.skip("нет Test1kv25-2kv26ALL.xlsx в uploads")
    _ban_router(monkeypatch)
    df = read_excel(str(SALES_ALL))
    history = [
        {"role": "user", "content": "Сколько сделок было в январе 2025 по отделам?"},
        {"role": "assistant", "content": "За январь 2025 — 130 сделок"},
    ]
    first = chat_service.handle_question(
        df, "Сколько сделок было в январе 2025 по отделам?"
    )["answer"]
    text = chat_service.handle_question(
        df, "а за 10 июня 2025?", history=history
    )["answer"]
    assert "130" in first or "сделк" in first.lower()
    assert "2954" not in text
    assert "7" in text
    assert "подраздел" in text.lower() or "отдел" in text.lower()


def test_pdo_compare_uses_product_count(monkeypatch):
    if not PDO.exists():
        pytest.skip("нет файла ПДО в examples/")
    _ban_router(monkeypatch)
    df = read_excel(str(PDO))
    text = chat_service.handle_question(df, "Сравни ССМУ и УПМ")["answer"]
    assert "Не удалось посчитать" not in text
    assert "697" in text.replace(" ", "")
    assert "105" in text.replace(" ", "")
    assert "больше" in text.lower()


def test_warranty_alabuga_counts_rows_not_error(monkeypatch):
    if not WARRANTY.exists():
        pytest.skip("нет файла гарантии в examples/")
    _ban_router(monkeypatch)
    df = read_excel(str(WARRANTY))
    text = chat_service.handle_question(df, "Сколько у Алабуги?")["answer"]
    assert "Не удалось посчитать" not in text
    assert "Числовая колонка не найдена" not in text
    assert "32" in text


def test_rewrite_period_followup_text():
    rewritten = chat_service._rewrite_followup(
        "а за 10 июня 2025?",
        [{"role": "user", "content": "Сколько сделок было в январе 2025 по отделам?"}],
    )
    assert rewritten != "а за 10 июня 2025?"
    assert "10" in rewritten and "2025" in rewritten
    assert "январ" not in rewritten.lower().replace("ё", "е")
