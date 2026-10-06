"""Именованный заказчик: оплачено / остаток, без группировки и без LLM."""
import pytest

from services import chat_service
from services.chat_lookup import wants_entity_metrics, wants_order_lookup
from services.insights_service import _format_number
from services.report_profiles.deficit_profile import _col_sum, detect_deficit_money_layout


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _fail_llm(*_a, **_kw):
    raise AssertionError("вопрос по заказчику не должен идти в LLM")


class TestDetect:
    def test_keaz_is_entity_metrics_not_lookup(self):
        q = "Сколько оплатил и сколько осталось КЭАЗ"
        assert wants_entity_metrics(q)
        assert not wants_order_lookup(q)

    def test_alabuga_unpaid_is_entity_metrics(self):
        assert wants_entity_metrics("Сколько у Алабуги неоплаченный остаток?")

    def test_totals_and_tops_are_not_entity(self):
        assert not wants_entity_metrics("Какой общий дефицит?")
        assert not wants_entity_metrics("Топ-5 заказчиков")
        assert not wants_entity_metrics("Сколько строк в таблице?")


class TestKeazPaidAndUnpaid:
    def test_answers_both_metrics(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        q = "Сколько оплатил и сколько осталось КЭАЗ"
        layout = detect_deficit_money_layout(deficit_df)
        mask = deficit_df["заказчик"].astype(str).str.contains("КЭАЗ", case=False, na=False)
        paid = _col_sum(deficit_df.loc[mask], layout.paid)
        unpaid = _col_sum(deficit_df.loc[mask], layout.unpaid)
        result = chat_service.handle_question(deficit_df, q)
        text = result["answer"]
        assert "Не удалось сгруппировать" not in text
        assert "КЭАЗ" in text.upper()
        assert _digits(_format_number(paid)) in _digits(text)
        assert _digits(_format_number(unpaid)) in _digits(text)
        assert "Оплачено" in text
        assert "остаток" in text.lower()


@pytest.mark.parametrize(
    "question, must_have, must_not",
    [
        (
            "Сколько у Алабуги неоплаченный остаток?",
            ["АЛАБУГА", "остаток"],
            ["Не удалось сгруппировать", "общий"],
        ),
        (
            "Сколько оплатил РОБЕЛ?",
            ["РОБЕЛ", "Оплачено"],
            ["Не удалось сгруппировать"],
        ),
        (
            "Сколько осталось у Робела?",
            ["РОБЕЛ", "остаток"],
            ["Не удалось сгруппировать"],
        ),
        (
            "Какая сумма заказов у ИРЗ?",
            ["ИРЗ", "Сумма заказов"],
            ["Не удалось сгруппировать"],
        ),
        (
            "Какой остаток у Кускова?",
            ["Кусков", "остаток"],
            ["Не удалось сгруппировать", "Нашёл"],
        ),
        (
            "Сколько заказов у КЭАЗ",
            ["КЭАЗ", "9"],
            ["Не удалось сгруппировать", "Оплачено"],
        ),
    ],
)
def test_named_customer_questions(deficit_df, monkeypatch, question, must_have, must_not):
    monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
    text = chat_service.handle_question(deficit_df, question)["answer"]
    upper = text.upper()
    for needle in must_have:
        assert needle.upper() in upper or needle in text
    for needle in must_not:
        assert needle not in text
