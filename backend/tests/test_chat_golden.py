"""Золотой набор «как живой пользователь»: быстрый путь без LLM-роутера.

Цифры — pandas по тому же df. Запрещено: «не удалось сгруппировать»,
итог файла вместо среза по имени.
"""
import pandas as pd

from models.file_context import FileContext, SheetBrief
from services import chat_service
from services.exceptions import OllamaUnavailableError
from services.file_context_service import deterministic_context
from services.insights_service import _format_number
from services.report_detector import detect_report_type
from services.report_profiles.deficit_profile import _col_sum, detect_deficit_money_layout

_FORBIDDEN = ("Не удалось сгруппировать", "я пока не понял")


def _digits(text: str) -> str:
    return "".join(ch for ch in str(text) if ch.isdigit())


def _ban_router(monkeypatch):
    def fail(*_a, **_k):
        raise AssertionError("быстрый путь не должен звать роутер")

    monkeypatch.setattr(chat_service, "_llm_classify", fail)
    monkeypatch.setattr(chat_service, "classify", fail)


def _ban_answer_llm(monkeypatch):
    def boom(*_a, **_k):
        raise OllamaUnavailableError("banned")

    monkeypatch.setattr(chat_service, "ask_llm", boom)


def _ask(df, question, monkeypatch, *, history=None, ctx=None, allow_answer_llm=False):
    _ban_router(monkeypatch)
    if not allow_answer_llm:
        _ban_answer_llm(monkeypatch)
    return chat_service.handle_question(
        df, question, history=history, file_context=ctx
    )["answer"]


def _fmt(value) -> str:
    return _digits(_format_number(float(value)))


def _contains_fmt(answer: str, value) -> bool:
    token = _fmt(value)
    return bool(token) and token in _digits(answer)


class TestDeficitLiveQuestions:
    def test_entity_one_metric_not_file_total(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        mask = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        slice_unpaid = _col_sum(deficit_df.loc[mask], layout.unpaid)
        total = _col_sum(deficit_df, layout.unpaid)
        text = _ask(deficit_df, "Сколько у Алабуги неоплаченный остаток?", monkeypatch)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "АЛАБУГА" in text.upper()
        assert _contains_fmt(text, slice_unpaid)
        assert not _contains_fmt(text, total)
        assert abs(slice_unpaid - total) > 1

    def test_entity_two_metrics(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        mask = deficit_df["заказчик"].astype(str).str.contains("КЭАЗ", case=False, na=False)
        paid = _col_sum(deficit_df.loc[mask], layout.paid)
        unpaid = _col_sum(deficit_df.loc[mask], layout.unpaid)
        text = _ask(deficit_df, "Сколько оплатил и сколько осталось КЭАЗ", monkeypatch)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "КЭАЗ" in text.upper()
        assert _contains_fmt(text, paid)
        assert _contains_fmt(text, unpaid)

    def test_compare_two_names_not_combined(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        ala = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        rob = deficit_df["заказчик"].astype(str).str.contains("РОБЕЛ", case=False, na=False)
        ala_u = _col_sum(deficit_df.loc[ala], layout.unpaid)
        rob_u = _col_sum(deficit_df.loc[rob], layout.unpaid)
        text = _ask(deficit_df, "Сравни Алабугу и Робел", monkeypatch)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "АЛАБУГА" in text.upper() and "РОБЕЛ" in text.upper()
        assert _contains_fmt(text, ala_u)
        assert _contains_fmt(text, rob_u)
        assert not _contains_fmt(text, ala_u + rob_u)

    def test_manager_vs_customer(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        mgr = deficit_df["ответственный"].astype(str).str.contains("Кусков", case=False, na=False)
        cust = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        mgr_u = _col_sum(deficit_df.loc[mgr], layout.unpaid)
        cust_u = _col_sum(deficit_df.loc[cust], layout.unpaid)
        assert abs(mgr_u - cust_u) > 1
        mgr_text = _ask(deficit_df, "Какой остаток у Кускова?", monkeypatch)
        cust_text = _ask(deficit_df, "Какой остаток у Алабуги?", monkeypatch)
        assert "Кусков" in mgr_text
        assert "АЛАБУГА" in cust_text.upper()
        assert _contains_fmt(mgr_text, mgr_u)
        assert _contains_fmt(cust_text, cust_u)
        assert not _contains_fmt(mgr_text, cust_u)
        assert "Не удалось сгруппировать" not in mgr_text + cust_text

    def test_file_total_vs_named_slice(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        total = _col_sum(deficit_df, layout.unpaid)
        mask = deficit_df["заказчик"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        slice_u = _col_sum(deficit_df.loc[mask], layout.unpaid)
        total_text = _ask(deficit_df, "Какой общий дефицит?", monkeypatch)
        named_text = _ask(deficit_df, "Сколько у Алабуги неоплаченный остаток?", monkeypatch)
        assert _contains_fmt(total_text, total)
        assert _contains_fmt(named_text, slice_u)
        assert not _contains_fmt(named_text, total)

    def test_compound_deficit_and_top_not_lookup(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        total = _col_sum(deficit_df, layout.unpaid)
        text = _ask(deficit_df, "Какой общий дефицит и кто топ-заказчик?", monkeypatch)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "Не нашёл заказ" not in text
        assert _contains_fmt(text, total)
        assert "1." in text or "АЛАБУГА" in text.upper() or "РОБЕЛ" in text.upper()

    def test_what_is_in_the_file(self, deficit_df, monkeypatch):
        ctx = deterministic_context(deficit_df, filename="deficit.xlsx")
        layout = detect_deficit_money_layout(deficit_df)
        total = _col_sum(deficit_df, layout.unpaid)
        text = _ask(deficit_df, "Что в файле?", monkeypatch, ctx=ctx)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert str(len(deficit_df)) in text
        assert _contains_fmt(text, total)

    def test_which_sheets_one_sheet(self, deficit_df, monkeypatch):
        ctx = deterministic_context(deficit_df, filename="deficit.xlsx")
        text = _ask(deficit_df, "Какие листы в файле?", monkeypatch, ctx=ctx)
        assert "Не удалось сгруппировать" not in text
        assert ctx.sheets
        assert ctx.sheets[0].name in text

    def test_followup_remainder_after_named_paid(self, deficit_df, monkeypatch):
        layout = detect_deficit_money_layout(deficit_df)
        mask = deficit_df["заказчик"].astype(str).str.contains("КЭАЗ", case=False, na=False)
        unpaid = _col_sum(deficit_df.loc[mask], layout.unpaid)
        paid = _col_sum(deficit_df.loc[mask], layout.paid)
        total = _col_sum(deficit_df, layout.unpaid)
        history = [
            {"role": "user", "content": "Сколько оплатил КЭАЗ"},
            {"role": "assistant", "content": f"Оплачено {paid}"},
        ]
        text = _ask(deficit_df, "а по остатку?", monkeypatch, history=history)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "КЭАЗ" in text.upper()
        assert _contains_fmt(text, unpaid)
        assert not _contains_fmt(text, total)


class TestSalesLiveQuestions:
    def test_entity_deal_sum_not_file_total(self, sales_df, monkeypatch):
        col = "сумма по сделке"
        mask = sales_df["компания"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        slice_sum = float(pd.to_numeric(sales_df.loc[mask, col], errors="coerce").sum())
        total = float(pd.to_numeric(sales_df[col], errors="coerce").sum())
        text = _ask(sales_df, "Какая выручка у Алабуги?", monkeypatch)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "АЛАБУГА" in text.upper()
        assert _contains_fmt(text, slice_sum)
        assert not _contains_fmt(text, total)

    def test_compare_keeps_legal_entities_apart(self, sales_df, monkeypatch):
        col = "сумма по сделке"
        ala = sales_df["компания"].astype(str).str.contains("АЛАБУГА МАШИНЕРИ", case=False, na=False)
        rob = sales_df["компания"].astype(str).str.contains("РОБЕЛ", case=False, na=False)
        ala_s = float(pd.to_numeric(sales_df.loc[ala, col], errors="coerce").sum())
        rob_s = float(pd.to_numeric(sales_df.loc[rob, col], errors="coerce").sum())
        text = _ask(sales_df, "Сравни Алабугу и Робел", monkeypatch)
        assert "АЛАБУГА" in text.upper() and "РОБЕЛ" in text.upper()
        assert _contains_fmt(text, ala_s)
        assert _contains_fmt(text, rob_s)
        assert not _contains_fmt(text, ala_s + rob_s)

    def test_sheets_and_whats_in_file(self, sales_df, sales_workbook, monkeypatch):
        ctx = deterministic_context(
            sales_df, filename="Этапы продаж.xlsx", workbook=sales_workbook
        )
        sheets = _ask(sales_df, "Какие листы в файле?", monkeypatch, ctx=ctx)
        overview = _ask(sales_df, "Что в файле?", monkeypatch, ctx=ctx)
        for name in ("Данные", "Дашборд (сделки)"):
            assert name in sheets
        assert "витрина" in sheets.lower() or "1С" in sheets
        total = float(pd.to_numeric(sales_df["сумма по сделке"], errors="coerce").sum())
        assert _contains_fmt(overview, total)
        assert "2394" in overview
        assert "Не удалось сгруппировать" not in sheets + overview


class TestPdoAndWarranty:
    def test_pdo_type_and_department_slice(self, pdo_df, monkeypatch):
        assert detect_report_type(pdo_df) == "pdo_report"
        ctx = deterministic_context(pdo_df, filename="Отчет ПДО.xlsx")
        ssmu = pdo_df["ответственное подразделение"] == "ССМУ"
        qty = float(pdo_df.loc[ssmu, "количество изделий"].sum())
        file_qty = float(pdo_df["количество изделий"].sum())
        text = _ask(pdo_df, "Сколько изделий у ССМУ?", monkeypatch, ctx=ctx)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "ССМУ" in text.upper()
        assert _contains_fmt(text, qty)
        assert not _contains_fmt(text, file_qty)
        assert str(len(pdo_df.loc[ssmu])) in text or "2" in text

    def test_pdo_compare_and_overview(self, pdo_df, monkeypatch):
        ctx = deterministic_context(pdo_df, filename="Отчет ПДО.xlsx")
        compare = _ask(pdo_df, "Сравни ССМУ и УПМ", monkeypatch, ctx=ctx)
        overview = _ask(pdo_df, "Что в файле?", monkeypatch, ctx=ctx)
        assert "ССМУ" in compare.upper() and "УПМ" in compare.upper()
        assert "Не удалось сгруппировать" not in compare
        assert str(len(pdo_df)) in overview
        assert "ПДО" in overview or "строк" in overview.lower()

    def test_warranty_type_slice_and_lookup(self, warranty_df, monkeypatch):
        assert detect_report_type(warranty_df, filename="Гарантия 2026.xlsx") == "warranty"
        ctx = FileContext(
            title="Гарантия 2026.xlsx",
            report_kind="Гарантия",
            grain="одна строка = позиция гарантии",
            facts=[f"Строк: {len(warranty_df)}"],
            sheets=[
                SheetBrief(name="Гарантия", rows=len(warranty_df), active=True, role="data"),
                SheetBrief(name="Справочник", rows=2, active=False, role="reference"),
            ],
        )
        mask = warranty_df["контрагент"].astype(str).str.contains("АЛАБУГА", case=False, na=False)
        text = _ask(warranty_df, "Сколько у Алабуги?", monkeypatch, ctx=ctx)
        for phrase in _FORBIDDEN:
            assert phrase not in text
        assert "АЛАБУГА" in text.upper()
        assert str(int(mask.sum())) in text
        file_index_sum = float(warranty_df["№ п/п"].sum())
        slice_index_sum = float(warranty_df.loc[mask, "№ п/п"].sum())
        if abs(file_index_sum - slice_index_sum) > 1:
            assert not _contains_fmt(text, file_index_sum)
        sheets = _ask(warranty_df, "Какие листы в файле?", monkeypatch, ctx=ctx)
        assert "Гарантия" in sheets and "Справочник" in sheets
        lookup = _ask(warranty_df, "Что с заказом САУП-000111", monkeypatch, ctx=ctx)
        assert "САУП-000111" in lookup
        assert "АЛАБУГА" in lookup.upper()
        overview = _ask(warranty_df, "Что в файле?", monkeypatch, ctx=ctx)
        assert str(len(warranty_df)) in overview
