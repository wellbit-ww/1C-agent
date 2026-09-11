"""Поиск заказа по заказчику, номеру и дате — комментарий и карточка."""
from services import chat_service
from services.chat_lookup import (
    exec_order_lookup,
    extract_order_clues,
    find_order_rows,
    match_entity_slice,
    wants_order_lookup,
)


class TestOrderLookupDetect:
    def test_examples_from_user(self):
        assert wants_order_lookup("Что с заказом Алабуги")
        assert wants_order_lookup("Что с заказом САУП-000450")
        assert wants_order_lookup("Что с заказом от 29.12")

    def test_code_without_word_order(self):
        assert wants_order_lookup("САУП-000450")

    def test_does_not_steal_aggregates(self):
        assert not wants_order_lookup("Какой общий дефицит?")
        assert not wants_order_lookup("Топ-5 заказчиков")
        assert not wants_order_lookup("Сколько строк в таблице?")
        assert not wants_order_lookup("Сколько у Алабуги неоплаченный остаток?")


class TestEntitySlice:
    def test_alabuga_not_whole_file(self, deficit_df):
        found = match_entity_slice(deficit_df, "Сколько у Алабуги неоплаченный остаток?")
        assert found is not None
        assert found["frame"] is not None
        assert any("АЛАБУГ" in name.upper() for name in found["names"])
        assert len(found["frame"]) < len(deficit_df)

    def test_total_question_has_no_entity(self, deficit_df):
        assert match_entity_slice(deficit_df, "Какой общий дефицит?") is None


class TestOrderLookupClues:
    def test_splits_code_date_word(self):
        clues = extract_order_clues("Что с заказом САУП-000450")
        assert any("сауп-000450" in c for c in clues["codes"])
        assert clues["words"] == []

        clues = extract_order_clues("Что с заказом Алабуги")
        assert any("алабуг" in w.lower() for w in clues["words"])
        assert clues["codes"] == []

        clues = extract_order_clues("Что с заказом от 29.12")
        assert clues["dates"] == ["29.12"]


class TestOrderLookupOnDeficit:
    def test_by_customer(self, deficit_df):
        result = exec_order_lookup(deficit_df, "Что с заказом Алабуги")
        text = result["answer"]
        assert "Комментарий" in text
        assert "ПНР" in text or "акт" in text.lower()
        assert "АЛАБУГА" in text.upper()
        assert "САУП-000450" in text

    def test_by_order_code(self, deficit_df):
        result = exec_order_lookup(deficit_df, "Что с заказом САУП-000450")
        assert "САУП-000450" in result["answer"]
        assert "АЛАБУГА" in result["answer"].upper()

    def test_by_date_includes_alabuga(self, deficit_df):
        rows = find_order_rows(deficit_df, "Что с заказом от 29.12")
        assert not rows.empty
        blob = " ".join(rows.astype(str).fillna("").agg(" ".join, axis=1))
        assert "САУП-000450" in blob or "АЛАБУГА" in blob.upper()

    def test_missing_order(self, deficit_df):
        result = exec_order_lookup(deficit_df, "Что с заказом XYZ-999999")
        assert "Не нашёл" in result["answer"]

    def test_chat_fast_path_skips_llm(self, deficit_df, monkeypatch):
        def fail_classify(*_a, **_kw):
            raise AssertionError("поиск заказа не должен идти в LLM")

        monkeypatch.setattr(chat_service, "_llm_classify", fail_classify)
        result = chat_service.handle_question(deficit_df, "Что с заказом Алабуги")
        assert result["charts"] == []
        assert "Комментарий" in result["answer"]
        assert "АЛАБУГА" in result["answer"].upper()
