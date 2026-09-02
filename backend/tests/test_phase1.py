"""Тесты Фазы 1: персистентность, parquet-кэш, CSV, память чата, составные вопросы."""
import io

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import app
from services import cache_service, chat_service, db_service, storage_service
from services.excel_service import read_excel

from conftest import DEFICIT_FILE


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_service, "DB_PATH", tmp_path / "test.db")
    db_service.init_db()


class TestDbService:
    def test_file_record_roundtrip(self, tmp_db):
        db_service.save_file_record("abc123", "отчёт.xlsx", r"C:\tmp\abc123.xlsx")
        record = db_service.get_file_record("abc123")
        assert record["original_name"] == "отчёт.xlsx"
        assert record["path"].endswith("abc123.xlsx")

    def test_missing_file_record(self, tmp_db):
        assert db_service.get_file_record("nope") is None

    def test_chat_history_order_and_charts(self, tmp_db):
        db_service.add_chat_message("f1", "user", "первый вопрос")
        db_service.add_chat_message(
            "f1", "assistant", "первый ответ", charts=[{"chart_type": "pie"}]
        )
        db_service.add_chat_message("f1", "user", "второй вопрос")

        history = db_service.get_chat_history("f1")
        assert [m["role"] for m in history] == ["user", "assistant", "user"]
        assert history[1]["charts"] == [{"chart_type": "pie"}]
        assert history[2]["charts"] == []

    def test_chat_history_limit(self, tmp_db):
        for i in range(10):
            db_service.add_chat_message("f1", "user", f"вопрос {i}")
        history = db_service.get_chat_history("f1", limit=3)
        assert len(history) == 3
        assert history[-1]["content"] == "вопрос 9"

    def test_history_isolated_per_file(self, tmp_db):
        db_service.add_chat_message("f1", "user", "файл один")
        db_service.add_chat_message("f2", "user", "файл два")
        assert len(db_service.get_chat_history("f1")) == 1
        assert len(db_service.get_chat_history("f2")) == 1


class TestParquetCache:
    @pytest.fixture
    def tmp_cache(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path)
        cache_service.clear_cache()
        yield tmp_path
        cache_service.clear_cache()

    def test_roundtrip_survives_memory_clear(self, tmp_cache):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        cache_service.set_dataframe("fid", df)
        cache_service.clear_cache()  # «рестарт backend»

        restored = cache_service.get_dataframe("fid")
        assert restored is not None
        assert restored.shape == (2, 2)
        assert list(restored["b"]) == ["x", "y"]

    def test_stale_cache_ignored(self, tmp_cache, monkeypatch):
        df = pd.DataFrame({"a": [1]})
        cache_service.set_dataframe("fid", df)
        cache_service.clear_cache()

        # отрицательный TTL надёжнее нуля: на Windows mtime файла может
        # оказаться чуть «в будущем» относительно time.time()
        monkeypatch.setattr(cache_service, "CACHE_TTL_HOURS", -1)
        assert cache_service.get_dataframe("fid") is None

    def test_mixed_object_column_writes_parquet(self, tmp_cache):
        df = pd.DataFrame({"комментарий": ["ок", 1.5, None, "текст"]})
        cache_service.set_dataframe("mix", df)
        cache_service._cache.clear()
        restored = cache_service.get_dataframe("mix")
        assert restored is not None
        assert len(restored) == 4


class TestCsvSupport:
    def test_parse_1c_csv(self, tmp_path):
        # типичный CSV из 1С: cp1251, «;», числа с пробелами и запятой
        content = "Клиент;Сумма;Дата\nООО Ромашка;1 234,56;12.01.2026\nАО Вектор;7 890,00;13.01.2026\n"
        csv_file = tmp_path / "отчёт.csv"
        csv_file.write_bytes(content.encode("cp1251"))

        df = read_excel(str(csv_file))
        assert df.shape == (2, 3)
        assert list(df.columns) == ["клиент", "сумма", "дата"]
        assert pd.api.types.is_numeric_dtype(df["сумма"])
        assert df["сумма"].sum() == pytest.approx(9124.56)

    def test_upload_csv_e2e(self):
        with TestClient(app) as client:
            content = "Колонка1;Колонка2\nа;1\nб;2\n"
            response = client.post(
                "/upload",
                files={"file": ("mini.csv", io.BytesIO(content.encode("utf-8")))},
            )
        assert response.status_code == 200, response.text
        assert response.json()["file_id"]

    def test_csv_with_sum_is_not_deficit(self):
        from services.report_detector import detect_report_type

        df = pd.DataFrame({"клиент": ["А", "Б"], "сумма": [100.0, 200.0]})
        assert detect_report_type(df) == "unknown"
        assert detect_report_type(df, filename="выгрузка.csv") == "unknown"

        with TestClient(app) as client:
            content = "Клиент;Сумма\nА;100\nБ;200\n"
            response = client.post(
                "/upload",
                files={"file": ("t.csv", io.BytesIO(content.encode("utf-8-sig")))},
            )
            assert response.status_code == 200, response.text
            dash = client.post("/dashboard", json={"file_id": response.json()["file_id"]})
            assert dash.status_code == 200, dash.text
            assert dash.json()["report_type"] != "deficit_report"
            assert dash.json().get("tabs") or dash.json().get("charts")


class TestCompoundQuestions:
    def test_compound_goes_to_llm_first(self, sales_df, monkeypatch):
        calls = []

        def fake_classify(question, df, history=None, **kwargs):
            calls.append(question)
            return [
                {"action": "stat", "operation": "row_count"},
                {"action": "stat", "operation": "sum"},
            ]

        monkeypatch.setattr(chat_service, "_llm_classify", fake_classify)
        monkeypatch.setattr(chat_service, "ask_llm", lambda p: "")
        result = chat_service.handle_question(
            sales_df, "Сколько строк и какая общая выручка?"
        )
        assert calls, "составной вопрос должен сначала уйти в LLM-разбор"
        assert "строк" in result["answer"]
        assert "Сумма" in result["answer"]

    def test_compound_falls_back_to_keywords_without_llm(self, sales_df, monkeypatch):
        monkeypatch.setattr(chat_service, "_llm_classify", lambda *a, **kw: None)
        result = chat_service.handle_question(
            sales_df, "Какая общая выручка и кто топ-заказчик?"
        )
        # быстрый путь отвечает хотя бы на часть вопроса
        assert result["answer"]

    def test_simple_question_stays_on_fast_path(self, sales_df, monkeypatch):
        def fail_classify(*a, **kw):
            raise AssertionError("простой вопрос не должен идти в LLM")

        monkeypatch.setattr(chat_service, "_llm_classify", fail_classify)
        result = chat_service.handle_question(sales_df, "Сколько строк?")
        assert "2394" in "".join(ch for ch in result["answer"] if ch.isdigit())


class TestChatMemory:
    def test_history_reaches_classify_prompt(self, sales_df, monkeypatch):
        captured = {}

        def fake_classify(prompt):
            captured["prompt"] = prompt
            return '{"action": "chart", "chart_type": "bar", "group_semantic": "manager"}'

        monkeypatch.setattr(chat_service, "classify", fake_classify)
        monkeypatch.setattr(chat_service, "ask_llm", lambda p: "")
        history = [
            {"role": "user", "content": "покажи выручку по клиентам"},
            {"role": "assistant", "content": "Построил диаграмму по клиентам."},
        ]
        # формулировка без ключевых слов быстрого пути — уходит в LLM
        result = chat_service.handle_question(
            sales_df, "поясни подробнее предыдущий ответ", history=history
        )
        assert "выручку по клиентам" in captured["prompt"]
        assert result["charts"] or result["answer"]


class TestRestartResilience:
    def test_dashboard_and_history_survive_restart(self, tmp_path, monkeypatch):
        # изолированные БД и кэш для теста
        monkeypatch.setattr(db_service, "DB_PATH", tmp_path / "test.db")
        db_service.init_db()
        monkeypatch.setattr(cache_service, "CACHE_DIR", tmp_path / "cache")

        with TestClient(app) as client:
            with open(DEFICIT_FILE, "rb") as f:
                response = client.post(
                    "/upload", files={"file": ("deficit.xlsx", f)}
                )
            assert response.status_code == 200
            file_id = response.json()["file_id"]

            chat_response = client.post(
                "/chat",
                json={"file_id": file_id, "question": "Сколько строк?"},
            )
            assert chat_response.status_code == 200

            # имитация рестарта backend: in-memory состояние обнулено
            storage_service._files.clear()
            cache_service.clear_cache()

            dashboard = client.post("/dashboard", json={"file_id": file_id})
            assert dashboard.status_code == 200, (
                "после рестарта файл должен восстановиться из SQLite + parquet"
            )
            assert dashboard.json()["metadata"]["rows"] == 89

            history = client.post("/history", json={"file_id": file_id})
            assert history.status_code == 200
            messages = history.json()["messages"]
            assert [m["role"] for m in messages] == ["user", "assistant"]
            assert "Сколько строк?" in messages[0]["content"]
