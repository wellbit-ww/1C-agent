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

    def test_top_responsible_by_count_and_sum(self, sales_df, monkeypatch):
        def fail_classify(*_a, **_kw):
            raise AssertionError("вопрос не составной — LLM-классификатор не нужен")

        monkeypatch.setattr(chat_service, "_llm_classify", fail_classify)
        result = chat_service.handle_question(
            sales_df, "Топ ответственных по количеству и сумме сделок"
        )
        assert "Не удалось найти лидеров" not in result["answer"]
        assert "ответственн" in result["answer"].lower()
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
    def test_unknown_question_uses_file_facts(self, sales_df, monkeypatch):
        captured = {}

        def fake_ask(prompt):
            captured["prompt"] = prompt
            return "В файле продажи: 2394 строки, главная метрика — выручка."

        monkeypatch.setattr(chat_service, "_llm_classify", lambda *a, **kw: None)
        monkeypatch.setattr(chat_service, "ask_llm", fake_ask)
        from models.file_context import FileContext

        ctx = FileContext(
            summary="Карточка продаж АБВ",
            facts=["Строк: 2394"],
        )
        result = chat_service.handle_question(
            sales_df, "qwerty asdfgh", file_context=ctx
        )
        assert result["charts"] == []
        assert "я пока не понял" not in result["answer"].lower()
        assert "Карточка продаж АБВ" in captured["prompt"]
        assert "Строк: 2394" in captured["prompt"]
        assert "продажи" in result["answer"].lower()

    def test_explicit_help_lists_skills(self, sales_df):
        result = chat_service.handle_question(sales_df, "Что ты умеешь?")
        assert result["charts"] == []
        assert "Вот что я умею" in result["answer"]
        assert "я пока не понял" not in result["answer"].lower()

    def test_unknown_without_ollama_returns_pandas_facts(self, sales_df, monkeypatch):
        from models.file_context import FileContext
        from services.exceptions import OllamaUnavailableError

        monkeypatch.setattr(chat_service, "_llm_classify", lambda *a, **kw: None)

        def boom(_prompt):
            raise OllamaUnavailableError("нет ollama")

        monkeypatch.setattr(chat_service, "ask_llm", boom)
        ctx = FileContext(summary="Фактфайл XYZ", facts=["Строк: 2394"])
        result = chat_service.handle_question(
            sales_df, "расскажи про содержимое файла", file_context=ctx
        )
        assert "я пока не понял" not in result["answer"].lower()
        assert "Строк: 2394" in result["answer"] or "Фактфайл XYZ" in result["answer"]


class TestNarrativeReport:
    def test_detailed_report_is_text_not_chart(self, sales_df, monkeypatch):
        monkeypatch.setattr(
            chat_service,
            "ask_llm",
            lambda *a, **k: "Понимание этого файла. Колонки: клиент. Пустых ячеек: 59.1%",
        )
        result = chat_service.handle_question(
            sales_df,
            "Напиши подробный отчет по продажам за семь кварталов",
        )
        assert result["charts"] == []
        assert "Понимание этого файла" not in result["answer"]
        assert "Пустых ячеек" not in result["answer"]
        assert "Отчёт по продажам" in result["answer"]
        assert "2394" in result["answer"]
        assert "Динамика" in result["answer"]
        assert "Запрошено 7 срезов" in result["answer"]

    def test_quarter_report_without_ollama(self, sales_df, monkeypatch):
        def boom(*a, **k):
            raise chat_service.OllamaUnavailableError("down")

        monkeypatch.setattr(chat_service, "ask_llm", boom)
        result = chat_service.handle_question(
            sales_df,
            "Напиши подробный отчет по продажам за квартал",
        )
        assert result["charts"] == []
        assert "Понимание этого файла" not in result["answer"]
        assert "Отчёт по продажам" in result["answer"]
        assert "Лидеры" in result["answer"]
        assert "Выводы" in result["answer"]

    def test_good_polish_is_kept(self, sales_df, monkeypatch):
        polished = (
            "По итогам выгрузки продажи идут неровно: 2025Q1 слабее 2025Q2, "
            "затем 2025Q3 и 2025Q4, а 2026Q1 замыкает ряд. "
        ) * 8
        monkeypatch.setattr(chat_service, "ask_llm", lambda *a, **k: polished)
        result = chat_service.handle_question(
            sales_df,
            "Напиши подробный отчет по продажам за квартал",
        )
        assert result["answer"] == polished.strip()


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
