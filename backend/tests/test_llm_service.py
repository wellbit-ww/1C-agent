"""Таймауты и потолок токенов на всех ChatOllama-клиентах."""
import httpx
import pytest

from config import LLM_NUM_PREDICT_DEFAULT, OLLAMA_CONNECT_TIMEOUT, OLLAMA_REQUEST_TIMEOUT
from services import dashboard_service, file_context_service, llm_service
from services.exceptions import OllamaUnavailableError


def _timeout_of(client) -> httpx.Timeout:
    timeout = (client.client_kwargs or {}).get("timeout")
    assert isinstance(timeout, httpx.Timeout), "ChatOllama должен получать httpx.Timeout"
    return timeout


class TestFactory:
    def test_timeout_and_num_predict(self):
        client = llm_service.make_chat_ollama(model="qwen3:8b", num_predict=123)
        timeout = _timeout_of(client)
        assert timeout.connect == OLLAMA_CONNECT_TIMEOUT
        assert timeout.read == OLLAMA_REQUEST_TIMEOUT
        assert client.num_predict == 123

    def test_main_llm_has_defaults(self):
        assert llm_service.llm.num_predict == LLM_NUM_PREDICT_DEFAULT
        timeout = _timeout_of(llm_service.llm)
        assert timeout.read == OLLAMA_REQUEST_TIMEOUT
        assert timeout.connect == OLLAMA_CONNECT_TIMEOUT

    def test_router_has_timeout_and_short_predict(self):
        router = llm_service._get_router_llm()
        assert router.num_predict == 300
        assert _timeout_of(router).read == OLLAMA_REQUEST_TIMEOUT

    def test_brief_spec_comments_clients_have_timeout(self):
        for client in (
            file_context_service._get_brief_llm(),
            dashboard_service._get_spec_llm(),
            dashboard_service._get_comments_llm(),
        ):
            assert _timeout_of(client).read == OLLAMA_REQUEST_TIMEOUT


class TestAskLlm:
    def test_default_num_predict_is_bound(self, monkeypatch):
        captured = {}

        class Fake:
            def bind(self, **kwargs):
                captured["bind"] = kwargs
                return self

            def invoke(self, prompt):
                captured["prompt"] = prompt

                class Response:
                    content = "ok"

                return Response()

        monkeypatch.setattr(llm_service, "llm", Fake())
        assert llm_service.ask_llm("вопрос") == "ok"
        assert captured["bind"] == {"options": {"num_predict": LLM_NUM_PREDICT_DEFAULT}}
        assert captured["prompt"] == "вопрос"

    def test_explicit_num_predict_overrides(self, monkeypatch):
        captured = {}

        class Fake:
            def bind(self, **kwargs):
                captured["bind"] = kwargs
                return self

            def invoke(self, prompt):
                class Response:
                    content = "long"

                return Response()

        monkeypatch.setattr(llm_service, "llm", Fake())
        llm_service.ask_llm("отчёт", num_predict=1800)
        assert captured["bind"] == {"options": {"num_predict": 1800}}

    def test_read_timeout_becomes_unavailable(self, monkeypatch):
        class Fake:
            def bind(self, **kwargs):
                return self

            def invoke(self, prompt):
                raise httpx.ReadTimeout("Read timed out")

        monkeypatch.setattr(llm_service, "llm", Fake())
        with pytest.raises(OllamaUnavailableError) as err:
            llm_service.ask_llm("вопрос")
        assert "не ответила" in str(err.value)
        assert f"{OLLAMA_REQUEST_TIMEOUT:.0f}" in str(err.value)

    def test_classify_timeout_becomes_unavailable(self, monkeypatch):
        class Fake:
            def invoke(self, prompt):
                raise httpx.ConnectTimeout("Connect timed out")

        monkeypatch.setattr(llm_service, "_get_router_llm", lambda: Fake())
        with pytest.raises(OllamaUnavailableError) as err:
            llm_service.classify("json")
        assert "не ответила" in str(err.value)
