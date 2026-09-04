"""Юниты UI api_client без Streamlit и живого backend."""
import importlib.util
from pathlib import Path

import pytest

_API_CLIENT_PATH = Path(__file__).resolve().parents[2] / "ui" / "api_client.py"
_spec = importlib.util.spec_from_file_location("ui_api_client", _API_CLIENT_PATH)
api_client = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api_client)


class TestBuildUrl:
    def test_strips_slashes(self):
        assert api_client._build_url("/chat", "http://127.0.0.1:8000/") == (
            "http://127.0.0.1:8000/chat"
        )


class TestAuthHeaders:
    def test_empty_without_token(self, monkeypatch):
        monkeypatch.delenv("EXCEL_AGENT_API_TOKEN", raising=False)
        assert api_client._auth_headers() == {}

    def test_sets_header(self, monkeypatch):
        monkeypatch.setenv("EXCEL_AGENT_API_TOKEN", "secret")
        assert api_client._auth_headers() == {"X-API-Token": "secret"}


class TestRaiseForResponse:
    def test_ok_is_noop(self):
        class Resp:
            ok = True

        api_client._raise_for_response(Resp())

    def test_error_uses_detail(self):
        class Resp:
            ok = False
            status_code = 409
            text = "conflict"

            def json(self):
                return {"detail": "Дашборд изменился"}

        with pytest.raises(api_client.ApiClientError, match="изменился"):
            api_client._raise_for_response(Resp())
