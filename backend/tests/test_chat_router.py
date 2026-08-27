"""Тесты роутера чата: быстрый путь (без LLM), fallback, LLM-путь (skip без Ollama)."""
import pytest

from services import chat_service

from conftest import requires_ollama


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


class TestFastPathStats:
    """Ключевые слова исполняются детерминированно, LLM не вызывается."""

    def test_row_count(self, sales_df):
        result = chat_service.handle_question(sales_df, "Сколько строк в таблице?")
        assert "2394" in _digits(result["answer"])
        assert result["charts"] == []

    def test_total_revenue(self, sales_df):
        result = chat_service.handle_question(sales_df, "Общая выручка")
        assert "18254222243" in _digits(result["answer"])

    def test_top_clients(self, sales_df):
        result = chat_service.handle_question(sales_df, "Топ-5 клиентов")
        assert "АЛАБУГА" in result["answer"]
        assert "1." in result["answer"]

    def test_deficit_sum(self, deficit_df):
        result = chat_service.handle_question(deficit_df, "Какой общий дефицит?")
        assert _digits(result["answer"]), "ответ должен содержать сумму"


class TestFastPathCharts:
    def test_bar_by_managers(self, sales_df):
        result = chat_service.handle_question(sales_df, "График продаж по менеджерам")
        assert [c["chart_type"] for c in result["charts"]] == ["bar"]

    def test_pie_by_departments_no_llm(self, sales_df):
        # формулировка пользователя из бага 26.08 — должна идти быстрым путём
        result = chat_service.handle_question(
            sales_df, "Разбивка долга по службам в виде круговой"
        )
        assert [c["chart_type"] for c in result["charts"]] == ["pie"]

    def test_explicit_pie_over_period(self, sales_df):
        # «круговая по месяцам» — pie по месячным срезам, а не line
        result = chat_service.handle_question(
            sales_df, "Выручка по месяцам в виде круговой диаграммы"
        )
        assert [c["chart_type"] for c in result["charts"]] == ["pie"]

    def test_trend_line(self, sales_df):
        result = chat_service.handle_question(sales_df, "Динамика выручки по месяцам")
        assert [c["chart_type"] for c in result["charts"]] == ["line"]


class TestFallback:
    def test_unknown_question_gives_hints(self, sales_df, monkeypatch):
        # LLM недоступна/не распознала -> подсказки вместо пустого ответа
        monkeypatch.setattr(chat_service, "_llm_classify", lambda *a, **kw: None)
        result = chat_service.handle_question(sales_df, "qwerty asdfgh")
        assert result["charts"] == []
        assert len(result["answer"]) > 30


@requires_ollama
class TestLlmPath:
    def test_compound_question(self, deficit_df):
        result = chat_service.handle_question(
            deficit_df, "Какой общий дефицит и кто топ-заказчик?"
        )
        assert len(result["answer"]) > 20

    def test_free_form_chart(self, sales_df):
        result = chat_service.handle_question(
            sales_df, "покажи пожалуйста кто у нас основные компании по выручке наглядно"
        )
        assert result["charts"], "LLM должен был запросить диаграмму"
