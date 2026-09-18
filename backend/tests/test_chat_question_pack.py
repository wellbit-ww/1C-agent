"""Срез по вопросу, сравнение заказчиков и команды роутера entity/compare."""
import pandas as pd

from models.file_context import FileContext
from services import chat_service
from services.chat_question_pack import (
    build_answer_facts,
    wants_file_overview,
    wants_named_compare,
    wants_payment_overview,
    wants_product_catalog,
    wants_quarter_deals,
    wants_department_deals,
    wants_period_deals,
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
        assert wants_product_catalog("Сколько и каких шкафов заказали?")
        assert wants_product_catalog("Сколько и каких шкафов было продано?")
        assert not wants_named_compare("сколько и каких шкафов заказали?")
        assert not wants_named_compare("сколько и каих шкафов заказали?")
        assert not wants_product_catalog("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_product_catalog("Сколько строк в таблице?")
        assert not wants_product_catalog("Какие заказчики в файле?")
        assert wants_quarter_deals("Сколько сделок в первом квартале?")
        assert wants_quarter_deals("сколько сделок было во 2 квартале 2024")
        assert wants_quarter_deals("разбивка сделок по кварталам")
        assert wants_period_deals("сколько сделок в январе 2025")
        assert wants_period_deals("сколько сделок за неделю с 13 по 19 января")
        assert wants_period_deals("сколько сделок 15.01.2025")
        assert wants_period_deals("сколько сделок с 1 по 15 марта 2025")
        assert not wants_period_deals("динамика выручки по месяцам")
        assert not wants_period_deals("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_period_deals("разбивка сделок по кварталам")
        assert wants_department_deals("Сколько сделок в подразделении COOK")
        assert wants_department_deals("сколько сделок в СИО")
        assert wants_department_deals("сколько сделок в СМ")
        assert not wants_department_deals("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_department_deals("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_quarter_deals("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_quarter_deals("Сколько у Алабуги неоплаченный остаток?")
        assert not wants_quarter_deals("Динамика по кварталам")


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


def _qr_cabinets_df():
    return pd.DataFrame(
        {
            "сделка": [
                "Конвекционная печь JTR-1200",
                "Шкаф сухого хранения SDB1106NM",
                "Шкаф сухого хранения SDB1106NM",
                "Шкаф сухого хранения SDB1106NM",
                "Шкаф сухого хранения SDB1106NM",
                "Шкаф сухого хранения SDB1106NM",
                None,
                "ШСХ SDB702NM",
                "Шкаф SDB302NM",
            ],
            "комментарий": [
                "печь",
                "шкаф",
                "шкаф",
                "шкаф",
                "шкаф",
                "шкаф",
                "ШСХSDB1106NM",
                "шкаф",
                "шкаф",
            ],
            "заказ клиента": [f"САУП-00010{i}" for i in range(1, 10)],
            "заказчик": [
                "АЛАБУГА МАШИНЕРИ ООО",
                "Клиент А",
                "Клиент Б",
                "Клиент В",
                "Клиент Г",
                "Клиент Д",
                "Клиент Е",
                "Клиент Ж",
                "Клиент З",
            ],
            "модель/ количество": [
                "SDB1106NM × 15 шт.",
                "SDB1106NM × 4 шт.",
                "SDB1106NM × 5 шт.",
                "SDB1106NM × 3 шт.",
                "SDB1106NM × 2 шт.",
                "SDB1106NM × 1 шт.",
                "SDB1106NM × 1 шт.",
                "SDB702NM × 1 шт.",
                "SDB302NM × 1 шт.",
            ],
        }
    )


class TestProductCatalog:
    def test_cabinets_counted_without_llm(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _qr_cabinets_df()
        text = chat_service.handle_question(df, "Сколько и каких шкафов заказали?")["answer"]
        assert "Не удалось выполнить операцию" not in text
        assert "33 шт" in text
        assert "SDB1106NM" in text and "31 шт" in text
        assert "SDB702NM" in text
        assert "SDB302NM" in text
        assert "JTR-1200" not in text

    def test_sold_and_typo_still_count(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _qr_cabinets_df()
        sold = chat_service.handle_question(
            df, "Сколько и каких шкафов было продано?"
        )["answer"]
        typo = chat_service.handle_question(
            df, "сколько и каих шкафов заказали?"
        )["answer"]
        for text in (sold, typo):
            assert "Не нашёл двух сторон" not in text
            assert "нет данных" not in text.lower()
            assert "18 шт" not in text.split("SDB")[0]
            assert "33 шт" in text
            assert "SDB1106NM" in text

    def test_empty_stat_does_not_dead_end(self, monkeypatch):
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _qr_cabinets_df()
        text = chat_service._execute_actions(
            df,
            "Сколько и каких шкафов заказали?",
            [{"action": "stat"}],
        )["answer"]
        assert "Не удалось выполнить операцию" not in text
        assert "18 шт" not in text.split("SDB")[0]
        assert "33 шт" in text
        assert "SDB1106NM" in text

    def test_does_not_steal_named_customer(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        assert not wants_product_catalog(
            "Сколько у Алабуги неоплаченный остаток?", deficit_df
        )
        text = chat_service.handle_question(
            deficit_df, "Сколько у Алабуги неоплаченный остаток?"
        )["answer"]
        assert "АЛАБУГА" in text.upper()
        assert "остаток" in text.lower()


def _start_deals_df():
    return pd.DataFrame(
        {
            "заказчик": [
                "АЛАБУГА МАШИНЕРИ ООО",
                "РОБЕЛ ООО",
                "КЭАЗ",
                "АЛАБУГА МАШИНЕРИ ООО",
                "ТОРН АО",
            ],
            "дата начала сделки": [
                "15.01.2024",
                "01.03.2024",
                "10.04.2024",
                "02.07.2024",
                "20.11.2025",
            ],
            "сумма по сделке": [100, 200, 300, 400, 500],
        }
    )


class TestQuarterDeals:
    def test_first_quarter_count(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        text = chat_service.handle_question(
            _start_deals_df(), "Сколько сделок в первом квартале?"
        )["answer"]
        assert "2" in text
        assert "01.01–31.03" in text or "01.01-31.03" in text
        assert "апрель" not in text.lower()

    def test_named_quarter_and_year(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _start_deals_df()
        q2 = chat_service.handle_question(df, "сколько сделок во втором квартале 2024")["answer"]
        q4 = chat_service.handle_question(df, "сколько сделок в 4 квартале 2025")["answer"]
        assert "1" in q2
        assert "2 кв" in q2 or "2 квартал" in q2
        assert "1" in q4
        assert "2025" in q4

    def test_breakdown_and_missing_column(self, deficit_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        all_q = chat_service.handle_question(
            _start_deals_df(), "разбивка сделок по кварталам"
        )["answer"]
        assert "1 кв. 2024" in all_q
        assert "2 кв. 2024" in all_q
        assert "3 кв. 2024" in all_q
        assert "4 кв. 2025" in all_q
        missing = chat_service.handle_question(
            deficit_df, "Сколько сделок в первом квартале?"
        )["answer"]
        assert "дата начала сделки" in missing.lower()

    def test_typo_column_nchala(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = pd.DataFrame(
            {
                "Дата нчала сделки": ["05.02.2024", "01.08.2024"],
                "заказчик": ["А", "Б"],
            }
        )
        text = chat_service.handle_question(df, "Сколько сделок в 1 квартале")["answer"]
        assert "1" in text
        assert "1 кв" in text


def _dept_deals_df():
    return pd.DataFrame(
        {
            "заказчик": ["АЛАБУГА", "РОБЕЛ", "КЭАЗ", "ТОРН", "КЭАЗ"],
            "подразделение": ["СООК", "СООК", "СТО", "СООК", "СООК"],
            "дата начала сделки": [
                "15.01.2025",
                "01.03.2025",
                "10.02.2025",
                "20.02.2026",
                "05.05.2025",
            ],
        }
    )


class TestDepartmentDeals:
    def test_cook_latin_and_short_year(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        text = chat_service.handle_question(
            _dept_deals_df(),
            "Сколько сделок было в первом квартале 25 года в подразделении COOK",
        )["answer"]
        assert "СООК" in text.upper() or "COOK" in text.upper()
        assert "2025" in text
        assert "2026" not in text
        assert "**2**" in text
        assert "СТО" not in text

    def test_department_total_and_breakdown(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _dept_deals_df()
        total = chat_service.handle_question(
            df, "Сколько сделок в подразделении СООК"
        )["answer"]
        assert "**4**" in total
        by_dept = chat_service.handle_question(
            df, "сколько сделок по подразделениям"
        )["answer"]
        assert "СООК" in by_dept
        assert "СТО" in by_dept
        assert "**4**" in by_dept
        assert "**1**" in by_dept
        period = chat_service.handle_question(
            df, "сколько сделок было совершено за этот промежуток по отделам?"
        )["answer"]
        assert "Не нашёл" not in period
        assert "совершено" not in period.lower()
        assert "СООК" in period
        assert "СТО" in period
        assert "**4**" in period
        assert "2025" in period or "2024" in period


    def test_service_full_name_and_abbrev(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = pd.DataFrame(
            {
                "подразделение": [
                    "Отдел АПЛиС",
                    "ОВК",
                    "Отдел неразрушающего контроля",
                    "ОФК",
                    "Сервисная служба",
                    "СИО",
                    "Служба микроэлектроники",
                    "Служба оборудования обработки кабеля",
                ],
                "дата начала сделки": ["10.01.2025"] * 8,
                "заказчик": ["А"] * 8,
            }
        )
        cases = (
            ("сколько сделок в АПЛиС", "аплис"),
            ("сколько сделок в отделе внутрисхемного контроля", "овк"),
            ("сколько сделок в ОНК", "неразрушающ"),
            ("сколько сделок в ОФК", "офк"),
            ("сколько сделок в СС", "сервисная"),
            ("сколько сделок в СИО", "сио"),
            ("сколько сделок в СМ", "микроэлектрон"),
            ("сколько сделок в СМЭ", "микроэлектрон"),
            ("сколько сделок в службе оборудования обработки кабеля", "кабел"),
        )
        for question, needle in cases:
            text = chat_service.handle_question(df, question)["answer"].lower()
            assert "**1**" in chat_service.handle_question(df, question)["answer"], question
            assert needle in text, question
        ss = chat_service.handle_question(df, "сколько сделок в СС")["answer"]
        assert "СИО" not in ss
        sm = chat_service.handle_question(df, "сколько сделок в СМ")["answer"]
        assert "кабел" not in sm.lower()


class TestPeriodDeals:
    def test_month_week_and_day(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = pd.DataFrame(
            {
                "подразделение": ["СТО", "СТО", "СООК", "СМЭ", "СТО"],
                "дата начала сделки": [
                    "15.01.2025",
                    "16.01.2025",
                    "20.01.2025",
                    "03.02.2025",
                    "10.03.2025",
                ],
            }
        )
        month = chat_service.handle_question(df, "сколько сделок в январе 2025")["answer"]
        assert "**3**" in month
        assert "январ" in month.lower()
        assert "СТО" in month
        assert "СООК" in month
        assert "СМЭ" not in month
        day = chat_service.handle_question(df, "сколько сделок 15 января 2025")["answer"]
        assert "**1**" in day
        assert "СТО" in day
        assert "СООК" not in day
        week = chat_service.handle_question(
            df, "сколько сделок за неделю с 13 по 19 января 2025"
        )["answer"]
        assert "**2**" in week
        assert "СТО" in week
        assert "СООК" not in week
        around = chat_service.handle_question(
            df, "сколько сделок за неделю 15.01.2025"
        )["answer"]
        assert "**2**" in around
        empty = chat_service.handle_question(df, "сколько сделок 1 апреля 2025")["answer"]
        assert "**0**" in empty or "нет" in empty.lower()

    def test_range_and_named_department(self, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", _fail_llm)
        monkeypatch.setattr(chat_service, "ask_llm", _fail_llm)
        df = _dept_deals_df()
        text = chat_service.handle_question(
            df, "сколько сделок с 1 по 31 января 2025 в СООК"
        )["answer"]
        assert "**1**" in text
        assert "СООК" in text
        assert "СТО" not in text




