"""Таймауты и потолок токенов на всех ChatOllama-клиентах."""
import httpx
import pytest

from config import (
    BRIEF_NUM_PREDICT,
    LLM_NUM_PREDICT_DEFAULT,
    MAIN_MODEL,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_REQUEST_TIMEOUT,
    ROUTER_NUM_PREDICT,
)
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
        assert router.num_predict == ROUTER_NUM_PREDICT == 300
        assert _timeout_of(router).read == OLLAMA_REQUEST_TIMEOUT

    def test_roles_keep_token_caps(self):
        assert ROUTER_NUM_PREDICT == 300
        assert BRIEF_NUM_PREDICT == 1800
        assert LLM_NUM_PREDICT_DEFAULT == 800
        assert ROUTER_NUM_PREDICT < LLM_NUM_PREDICT_DEFAULT < BRIEF_NUM_PREDICT
        brief = file_context_service._get_brief_llm()
        spec = dashboard_service._get_spec_llm()
        fallback = llm_service._get_router_fallback_llm()
        assert brief.num_predict == BRIEF_NUM_PREDICT
        assert brief.model == MAIN_MODEL
        assert spec.num_predict == 2500
        assert spec.model == MAIN_MODEL
        assert fallback.num_predict == ROUTER_NUM_PREDICT
        assert fallback.model == MAIN_MODEL

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

    def test_classify_falls_back_when_router_model_missing(self, monkeypatch):
        class Missing:
            def invoke(self, prompt):
                raise RuntimeError("model 'qwen3:1.7b' not found, try pulling it")

        captured = {}

        class Ok:
            def invoke(self, prompt):
                captured["prompt"] = prompt

                class Response:
                    content = '{"action":"general"}'

                return Response()

        monkeypatch.setattr(llm_service, "ROUTER_MODEL", "qwen3:1.7b")
        monkeypatch.setattr(llm_service, "MAIN_MODEL", "qwen3:8b")
        monkeypatch.setattr(llm_service, "_get_router_llm", lambda: Missing())
        monkeypatch.setattr(llm_service, "_get_router_fallback_llm", lambda: Ok())
        assert llm_service.classify("json") == '{"action":"general"}'
        assert captured["prompt"] == "json"
