"""Тесты ИИ-слоя дашбордов: персистентность спек, пин, NL-генерация (LLM замокана)."""
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app import app
from conftest import SALES_FILE
from models.dashboard_spec import DashboardSpec, Tab, Tile, TileSource
from services import dashboard_service, db_service


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def sales_file_id(client):
    with open(SALES_FILE, "rb") as f:
        response = client.post("/upload", files={"file": ("sales.xlsx", f)})
    assert response.status_code == 200, response.text
    return response.json()["file_id"]


def _spec(**overrides) -> DashboardSpec:
    tile = {
        "title": "Тестовый тайл",
        "chart_type": "bar",
        "source": {"kind": "group", "group_column": "компания", "value_column": "сумма по сделке"},
        "agg": "sum",
        "top_n": 5,
        "unit": "auto",
        "sort": "desc",
    }
    tile.update(overrides)
    return DashboardSpec(tabs=[Tab(title="Вкладка", tiles=[Tile(**tile)])])


class _FakeMessage:
    def __init__(self, content: str):
        self.content = content


class _FakeLLM:
    def __init__(self, content: str):
        self._content = content

    def invoke(self, prompt: str):
        return _FakeMessage(self._content)


class TestSpecPersistence:
    def test_roundtrip(self):
        fid = f"test-{uuid.uuid4()}"
        spec = _spec()
        db_service.save_dashboard_spec(fid, spec.model_dump_json())
        loaded = DashboardSpec.model_validate_json(db_service.get_dashboard_spec(fid))
        assert loaded.tabs[0].tiles[0].title == "Тестовый тайл"
        db_service.delete_dashboard_spec(fid)
        assert db_service.get_dashboard_spec(fid) is None

    def test_get_current_spec_falls_back_to_profile(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        spec = dashboard_service.get_current_spec(fid, sales_df)
        assert spec is not None  # дефолт sales_pipeline v2
        assert [t.title for t in spec.tabs] == ["Воронка", "Менеджеры", "Клиенты"]

    def test_get_current_spec_prefers_saved(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        custom = _spec()
        dashboard_service.save_spec(fid, custom)
        spec = dashboard_service.get_current_spec(fid, sales_df)
        assert spec.tabs[0].title == "Вкладка"
        db_service.delete_dashboard_spec(fid)

    def test_corrupt_saved_spec_falls_back(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        db_service.save_dashboard_spec(fid, "{not valid json")
        spec = dashboard_service.get_current_spec(fid, sales_df)
        assert [t.title for t in spec.tabs] == ["Воронка", "Менеджеры", "Клиенты"]
        assert db_service.get_dashboard_spec(fid) is None  # мусор вычищен


class TestPin:
    def test_pin_adds_tile_to_first_tab(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        tile = _spec().tabs[0].tiles[0].model_dump()
        ok, message = dashboard_service.pin_tile(fid, sales_df, tile)
        assert ok, message
        spec = dashboard_service.get_current_spec(fid, sales_df)
        assert any(t.title == "Тестовый тайл" for t in spec.tabs[0].tiles)
        db_service.delete_dashboard_spec(fid)

    def test_pin_duplicate_rejected(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        tile = _spec().tabs[0].tiles[0].model_dump()
        dashboard_service.pin_tile(fid, sales_df, tile)
        ok, message = dashboard_service.pin_tile(fid, sales_df, tile)
        assert not ok and "уже есть" in message
        db_service.delete_dashboard_spec(fid)

    def test_pin_invalid_tile_rejected(self, sales_df):
        fid = f"test-{uuid.uuid4()}"
        ok, _ = dashboard_service.pin_tile(fid, sales_df, {"title": "x", "chart_type": "3d"})
        assert not ok


class TestNlGeneration:
    def test_valid_llm_json_becomes_spec(self, sales_df, monkeypatch):
        spec = _spec(title="Сгенерированный")
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm",
            lambda: _FakeLLM(f"Вот спека:\n{spec.model_dump_json()}"),
        )
        result = dashboard_service.generate_spec_nl(sales_df, "собери дашборд по клиентам")
        assert result is not None
        assert result.tabs[0].tiles[0].title == "Сгенерированный"

    def test_garbage_llm_output_returns_none(self, sales_df, monkeypatch):
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm", lambda: _FakeLLM("я не понял вопрос")
        )
        assert dashboard_service.generate_spec_nl(sales_df, "что-то") is None

    def test_invalid_schema_returns_none(self, sales_df, monkeypatch):
        bad = json.dumps({"tabs": [{"title": "T", "tiles": [{"title": "x", "chart_type": "pie3d", "source": {"kind": "group"}}]}]})
        monkeypatch.setattr(dashboard_service, "_get_spec_llm", lambda: _FakeLLM(bad))
        assert dashboard_service.generate_spec_nl(sales_df, "что-то") is None

    def test_edit_mode_includes_current_spec(self, sales_df, monkeypatch):
        captured = {}

        class CapturingLLM:
            def invoke(self, prompt):
                captured["prompt"] = prompt
                return _FakeMessage(_spec().model_dump_json())

        monkeypatch.setattr(dashboard_service, "_get_spec_llm", CapturingLLM)
        dashboard_service.generate_spec_nl(sales_df, "убери второй график", _spec())
        assert "Текущая спека" in captured["prompt"]

    def test_funnel_chart_type_is_coerced(self, sales_df, monkeypatch):
        payload = {
            "tabs": [{
                "title": "Воронка",
                "tiles": [{
                    "title": "Этапы",
                    "chart_type": "funnel",
                    "source": {"kind": "funnel", "columns_pattern": "(сумма)"},
                }],
            }]
        }
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm", lambda: _FakeLLM(json.dumps(payload))
        )
        result = dashboard_service.generate_spec_nl(sales_df, "воронка")
        assert result is not None
        tile = result.tabs[0].tiles[0]
        assert tile.chart_type == "hbar"
        assert tile.source.kind == "current_stage"

    def test_json_extracted_from_think_and_fences(self, sales_df, monkeypatch):
        spec = _spec(title="Из markdown")
        wrapped = f"<think>думаю</think>\n```json\n{spec.model_dump_json()}\n```"
        monkeypatch.setattr(dashboard_service, "_get_spec_llm", lambda: _FakeLLM(wrapped))
        result = dashboard_service.generate_spec_nl(sales_df, "клиенты")
        assert result is not None
        assert result.tabs[0].tiles[0].title == "Из markdown"

    def test_request_with_braces_does_not_crash(self, sales_df, monkeypatch):
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm", lambda: _FakeLLM(_spec().model_dump_json())
        )
        result = dashboard_service.generate_spec_nl(sales_df, "фильтр {статус}")
        assert result is not None

    def test_keyword_funnel_spec_without_llm(self, sales_df):
        spec = dashboard_service.build_spec_from_request(
            sales_df, "собери дашборд по инвестициям с воронкой"
        )
        assert spec is not None
        assert spec.tabs[0].title == "Воронка"
        assert spec.tabs[0].tiles[0].source.kind == "current_stage"
        assert spec.tabs[0].tiles[0].source.columns_pattern == "(сумма)"


class TestComments:
    def test_comments_cached_by_data_hash(self, sales_df, monkeypatch):
        calls = {"n": 0}

        class CountingLLM:
            def invoke(self, prompt):
                calls["n"] += 1
                return _FakeMessage('{"Вкладка": "текст выводов"}')

        monkeypatch.setattr(dashboard_service, "_get_comments_llm", CountingLLM)
        tabs = [{"title": "Вкладка", "tiles": [{"title": "T", "stats": {"total": 1, "top": [("a", 1.0)], "count": 1}}]}]
        fid = f"test-{uuid.uuid4()}"
        first = dashboard_service.generate_comments(fid, sales_df, tabs)
        second = dashboard_service.generate_comments(fid, sales_df, tabs)
        assert first == second == {"Вкладка": "текст выводов"}
        assert calls["n"] == 1  # второй вызов — из кэша


class TestChatPinSpec:
    def test_chart_carries_pin_spec(self, sales_df):
        from services.chat_service import handle_question

        result = handle_question(sales_df, "построй график по менеджерам")
        assert result["charts"], "ожидался график"
        pin = result["charts"][0].get("pin_spec")
        assert pin is not None
        Tile(**pin)  # валидируется как Tile


class TestEndpoints:
    def test_spec_save_endpoint(self, client, sales_file_id):
        response = client.post(
            "/dashboard/spec",
            json={"file_id": sales_file_id, "spec": _spec().model_dump()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tabs"][0]["tiles"][0]["title"] == "Тестовый тайл"
        db_service.delete_dashboard_spec(sales_file_id)

    def test_spec_save_rejects_invalid(self, client, sales_file_id):
        response = client.post(
            "/dashboard/spec",
            json={"file_id": sales_file_id, "spec": {"tabs": [{"title": "T", "tiles": [{"title": "x", "chart_type": "pie3d", "source": {"kind": "group"}}]}]}},
        )
        assert response.status_code == 422

    def test_pin_endpoint(self, client, sales_file_id):
        tile = _spec(title="Пин через API").tabs[0].tiles[0].model_dump()
        response = client.post("/dashboard/pin", json={"file_id": sales_file_id, "tile": tile})
        assert response.status_code == 200, response.text
        assert "закреплён" in response.json()["message"]
        db_service.delete_dashboard_spec(sales_file_id)

    def test_dashboard_returns_saved_spec(self, client, sales_file_id):
        dashboard_service.save_spec(sales_file_id, _spec(title="Сохранённый"))
        response = client.post("/dashboard", json={"file_id": sales_file_id})
        assert response.status_code == 200
        assert response.json()["tabs"][0]["title"] == "Вкладка"
        assert "spec" in response.json()
        db_service.delete_dashboard_spec(sales_file_id)

    def test_generate_endpoint_with_mocked_llm(self, client, sales_file_id, monkeypatch):
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm",
            lambda: _FakeLLM(_spec(title="NL-дашборд").model_dump_json()),
        )
        response = client.post(
            "/dashboard/generate",
            json={"file_id": sales_file_id, "request": "собери дашборд по менеджерам"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tabs"][0]["tiles"][0]["title"] == "NL-дашборд"
        db_service.delete_dashboard_spec(sales_file_id)

    def test_generate_falls_back_when_llm_fails(self, client, sales_file_id, monkeypatch):
        monkeypatch.setattr(
            dashboard_service, "_get_spec_llm", lambda: _FakeLLM("бред без json")
        )
        response = client.post(
            "/dashboard/generate",
            json={"file_id": sales_file_id, "request": "непонятный запрос"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["tabs"]
        assert response.json().get("warning")
        db_service.delete_dashboard_spec(sales_file_id)

    def test_generate_funnel_request_skips_llm(self, client, sales_file_id, monkeypatch):
        def _fail():
            raise RuntimeError("LLM не должен вызываться для запроса с воронкой")

        monkeypatch.setattr(dashboard_service, "_get_spec_llm", _fail)
        response = client.post(
            "/dashboard/generate",
            json={
                "file_id": sales_file_id,
                "request": "собери дашборд по инвестициям с воронкой",
            },
        )
        assert response.status_code == 200, response.text
        kinds = [
            tile["source"]["kind"]
            for tab in response.json()["spec"]["tabs"]
            for tile in tab["tiles"]
        ]
        assert "current_stage" in kinds
        db_service.delete_dashboard_spec(sales_file_id)
