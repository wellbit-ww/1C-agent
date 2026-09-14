"""Срез по вопросу, сравнение заказчиков и команды роутера entity/compare."""
from models.file_context import FileContext
from services import chat_service
from services.chat_question_pack import (
    build_answer_facts,
    wants_file_overview,
    wants_named_compare,
    wants_payment_overview,
    wants_rank_compare,
)
from services.insights_service import _format_number
from services.report_profiles.deficit_profile import _col_sum, detect_deficit_money_layout


def _digits(text: str) -> str:
    return "".join(ch for ch in text if ch.isdigit())


def _fail_llm(*_a, **_kw):
    raise AssertionError("этот вопрос не должен идти в LLM")


class TestDetectors:
    def test_compare_and_rank_and_overview(self):
        assert wants_named_compare("Сравни Алабугу и Робел")
        assert wants_named_compare("Алабуга и Робел кто должен больше")
        assert not wants_named_compare("Сколько оплатил и сколько осталось КЭАЗ")
        assert wants_rank_compare("Кто больше должен и кто больше заказал")
        assert wants_rank_compare("Кто больше должен?")
        assert not wants_rank_compare("Сколько у Алабуги неоплаченный остаток?")
        assert wants_payment_overview("Что происходит с оплатами в целом")
        assert not wants_payment_overview("Сколько оплатил КЭАЗ")
        assert wants_file_overview("Что в файле?")
        assert wants_file_overview("Расскажи про файл")
        assert not wants_file_overview("Сколько у Алабуги неоплаченный остаток?")


class TestNamedCompare:
    def test_alabuga_vs_robel(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        layout = detect_deficit_money_layout(deficit_df)
        ala = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        rob = deficit_df["заказчик"].astype(str).str.contains("РОБЕЛ", case=False, na=False)
        ala_unpaid = _col_sum(deficit_df.loc[ala], layout.unpaid)
        rob_unpaid = _col_sum(deficit_df.loc[rob], layout.unpaid)
        ala_order = _col_sum(deficit_df.loc[ala], layout.order_sum)
        rob_order = _col_sum(deficit_df.loc[rob], layout.order_sum)
        combined_unpaid = ala_unpaid + rob_unpaid

        text = chat_service.handle_question(
            deficit_df, "Сравни Алабугу и Робел"
        )["answer"]
        assert "Не удалось сгруппировать" not in text
        assert "АЛАБУГА" in text.upper()
        assert "РОБЕЛ" in text.upper()
        assert _digits(_format_number(ala_unpaid)) in _digits(text)
        assert _digits(_format_number(rob_unpaid)) in _digits(text)
        assert _digits(_format_number(ala_order)) in _digits(text)
        assert _digits(_format_number(rob_order)) in _digits(text)
        assert _digits(_format_number(combined_unpaid)) not in _digits(text)
        assert "остаток" in text.lower()
        assert "сумма заказов" in text.lower()


class TestRankCompare:
    def test_who_owes_vs_who_ordered(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        text = chat_service.handle_question(
            deficit_df, "Кто больше должен и кто больше заказал"
        )["answer"]
        assert "Не удалось сгруппировать" not in text
        assert "должен" in text.lower()
        assert "заказал" in text.lower()
        unpaid_block, order_block = text.split("**Кто больше заказал**", 1)
        assert "РОБЕЛ" in unpaid_block.upper()
        assert "АЛАБУГА" in order_block.upper()

    def test_sales_rank_does_not_invent_remainder(self, sales_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        text = chat_service.handle_question(
            sales_df, "Кто больше должен и кто больше заказал"
        )["answer"]
        assert "могут не совпадать" not in text
        assert "остатка" in text.lower() or "должен" in text.lower()
        assert "АЛАБУГА" in text.upper()


class TestAnswerTemplate:
    def test_entity_has_units_and_meaning(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        text = chat_service.handle_question(
            deficit_df, "Сколько у Алабуги неоплаченный остаток?"
        )["answer"]
        assert "руб." in text
        assert "АЛАБУГА" in text.upper()
        assert "не вся" in text.lower() or "разн" in text.lower()

    def test_gap_note_when_money_is_nan(self):
        import pandas as pd
        from services.chat_answers import money_gap_note

        frame = pd.DataFrame({"оплачено по заказу": [100.0, None]})
        note = money_gap_note(frame, ["оплачено по заказу"])
        assert "пусто" in note
        assert "1 из 2" in note


class TestPaymentOverviewPack:
    def test_general_prompt_gets_payment_slice(self, deficit_df, monkeypatch):
        captured = {}

        def fake_ask(prompt, **_k):
            captured["prompt"] = prompt
            return "По файлу оплаты и остаток посчитаны pandas."

        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", fake_ask)
        ctx = FileContext(
            summary="Карточка дефицита",
            facts=["Строк: 89", "Неоплаченный остаток: 1"],
            report_kind="Дефицит / задолженность",
        )
        result = chat_service.handle_question(
            deficit_df,
            "Что происходит с оплатами в целом",
            file_context=ctx,
        )
        prompt = captured["prompt"]
        assert "Срез по вопросу" in prompt
        assert "Неоплаченный остаток" in prompt
        assert "Оплачено" in prompt or "оплачено" in prompt.lower()
        assert "Карточка дефицита" in prompt
        assert "pandas" in result["answer"].lower()


class TestRouterActions:
    def test_llm_compare_action_is_executed(self, deficit_df, monkeypatch):
        monkeypatch.setattr(
            chat_service,
            "_llm_classify",
            lambda *a, **k: [{"action": "compare"}],
        )
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        text = chat_service._execute_actions(
            deficit_df,
            "Сравни Алабугу и Робел",
            [{"action": "compare"}],
        )["answer"]
        assert "АЛАБУГА" in text.upper()
        assert "РОБЕЛ" in text.upper()

    def test_group_with_two_names_remaps_to_compare(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        text = chat_service._execute_actions(
            deficit_df,
            "Сравни Алабугу и Робел",
            [{"action": "stat", "operation": "group", "agg": "sum"}],
        )["answer"]
        assert "Не удалось сгруппировать" not in text
        assert "АЛАБУГА" in text.upper()

    def test_group_with_one_name_remaps_to_entity(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        text = chat_service._execute_actions(
            deficit_df,
            "Сколько осталось у КЭАЗ",
            [{"action": "stat", "operation": "group", "agg": "sum"}],
        )["answer"]
        assert "Не удалось сгруппировать" not in text
        assert "КЭАЗ" in text.upper()
        assert "остаток" in text.lower()


class TestEntityPlusCount:
    def test_kuskov_remainder_and_order_count(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        mask = deficit_df["ответственный"].astype(str).str.contains(
            "Кусков", case=False, na=False
        )
        if not mask.any():
            mask = deficit_df.apply(
                lambda row: row.astype(str).str.contains("Кусков", case=False).any(),
                axis=1,
            )
        n = int(mask.sum())
        text = chat_service.handle_question(
            deficit_df, "Какой остаток у Кускова и сколько заказов"
        )["answer"]
        assert "Кусков" in text
        assert "остаток" in text.lower()
        assert str(n) in text


class TestAnswerFacts:
    def test_slice_does_not_use_file_total_for_named_client(self, deficit_df):
        layout = detect_deficit_money_layout(deficit_df)
        pack = build_answer_facts(
            deficit_df,
            "Сколько у Алабуги неоплаченный остаток?",
            file_context=FileContext(facts=["Строк: 89"]),
        )
        ala = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        ala_unpaid = _col_sum(deficit_df.loc[ala], layout.unpaid)
        total = _col_sum(deficit_df, layout.unpaid)
        assert _digits(_format_number(ala_unpaid)) in _digits(pack)
        assert abs(ala_unpaid - total) > 1
        assert "АЛАБУГА" in pack.upper() or "Алабуг" in pack
