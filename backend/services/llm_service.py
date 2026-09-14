import logging

from langchain_ollama import ChatOllama

from config import (
    LLM_NUM_PREDICT_DEFAULT,
    MAIN_MODEL,
    OLLAMA_BASE_URL,
    OLLAMA_CONNECT_TIMEOUT,
    OLLAMA_REQUEST_TIMEOUT,
    ROUTER_MODEL,
    ROUTER_NUM_PREDICT,
)
from services.exceptions import OllamaUnavailableError

logger = logging.getLogger(__name__)


def ollama_client_kwargs() -> dict:
    """httpx-таймауты для клиента Ollama: ChatOllama не принимает timeout=."""
    import httpx

    return {
        "timeout": httpx.Timeout(
            connect=OLLAMA_CONNECT_TIMEOUT,
            read=OLLAMA_REQUEST_TIMEOUT,
            write=min(30.0, OLLAMA_REQUEST_TIMEOUT),
            pool=OLLAMA_CONNECT_TIMEOUT,
        )
    }


def make_chat_ollama(
    *,
    model: str,
    num_predict: int | None = None,
    temperature: float = 0,
) -> ChatOllama:
    """Единая фабрика: reasoning выключен, HTTP-таймаут всегда задан."""
    kwargs: dict = {
        "model": model,
        "base_url": OLLAMA_BASE_URL,
        "temperature": temperature,
        "reasoning": False,
        "client_kwargs": ollama_client_kwargs(),
    }
    if num_predict is not None:
        kwargs["num_predict"] = num_predict
    return ChatOllama(**kwargs)


llm = make_chat_ollama(model=MAIN_MODEL, num_predict=LLM_NUM_PREDICT_DEFAULT)

_router_llm: ChatOllama | None = None
_router_fallback_llm: ChatOllama | None = None


def _get_router_llm() -> ChatOllama:
    global _router_llm
    if _router_llm is None:
        _router_llm = make_chat_ollama(
            model=ROUTER_MODEL,
            num_predict=ROUTER_NUM_PREDICT,
        )
    return _router_llm


def _get_router_fallback_llm() -> ChatOllama:
    """Тот же короткий JSON-вызов, но на 8B, если малая модель не установлена."""
    global _router_fallback_llm
    if _router_fallback_llm is None:
        _router_fallback_llm = make_chat_ollama(
            model=MAIN_MODEL,
            num_predict=ROUTER_NUM_PREDICT,
        )
    return _router_fallback_llm


def _is_missing_model(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "not found",
            "not exist",
            "does not exist",
            "no such model",
            "pull",
            "404",
        )
    )


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException):
            return True
    except Exception:
        pass
    return "timeout" in type(exc).__name__.lower()


def _ollama_error(exc: BaseException) -> OllamaUnavailableError:
    if _is_timeout(exc):
        return OllamaUnavailableError(
            f"Ollama не ответила за {OLLAMA_REQUEST_TIMEOUT:.0f} с ({exc})"
        )
    return OllamaUnavailableError(f"Ollama недоступна или не отвечает: {exc}")


def ask_llm(prompt: str, *, num_predict: int | None = None):
    tokens = LLM_NUM_PREDICT_DEFAULT if num_predict is None else num_predict
    try:
        # bind(options=...): «голый» bind(num_predict=...) в langchain-ollama
        # проваливается в Client.chat(**kwargs) и падает с TypeError
        response = llm.bind(options={"num_predict": tokens}).invoke(prompt)
    except Exception as exc:
        raise _ollama_error(exc) from exc

    return response.content


def classify(prompt: str) -> str:
    """Короткий JSON-вызов роутера. num_predict не поднимать.

    Сначала ROUTER_MODEL (1–3B). Если её нет в Ollama — тот же потолок
    на MAIN_MODEL, не потолок ответа/брифинга.
    """
    try:
        return _get_router_llm().invoke(prompt).content
    except Exception as exc:
        if _is_missing_model(exc) and ROUTER_MODEL != MAIN_MODEL:
            logger.warning(
                "Роутер %s недоступен (%s). JSON-команда через %s, num_predict=%s",
                ROUTER_MODEL,
                exc,
                MAIN_MODEL,
                ROUTER_NUM_PREDICT,
            )
            try:
                return _get_router_fallback_llm().invoke(prompt).content
            except Exception as fallback_exc:
                raise _ollama_error(fallback_exc) from fallback_exc
        raise _ollama_error(exc) from exc
