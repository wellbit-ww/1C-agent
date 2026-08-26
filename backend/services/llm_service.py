import os

from langchain_ollama import ChatOllama

from services.exceptions import OllamaUnavailableError

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MAIN_MODEL = os.getenv("EXCEL_AGENT_MODEL", "qwen3:8b")
# для классификации запросов можно назначить более лёгкую модель,
# например EXCEL_AGENT_ROUTER_MODEL=qwen3:4b
ROUTER_MODEL = os.getenv("EXCEL_AGENT_ROUTER_MODEL", MAIN_MODEL)

llm = ChatOllama(
    model=MAIN_MODEL,
    base_url=OLLAMA_BASE_URL,
    temperature=0,
    # qwen3 без «размышлений» отвечает в разы быстрее,
    # а для наших задач (переформулировка результата) thinking не нужен
    reasoning=False,
)

_router_llm: ChatOllama | None = None


def _get_router_llm() -> ChatOllama:
    global _router_llm
    if _router_llm is None:
        _router_llm = ChatOllama(
            model=ROUTER_MODEL,
            base_url=OLLAMA_BASE_URL,
            temperature=0,
            reasoning=False,
            # классификация должна вернуть короткий JSON — обрезаем,
            # чтобы зависшая генерация не блокировала чат
            num_predict=512,
        )
    return _router_llm


def ask_llm(prompt: str):
    try:
        response = llm.invoke(prompt)
    except Exception as exc:
        raise OllamaUnavailableError(
            f"Ollama недоступна или не отвечает: {exc}"
        ) from exc

    return response.content


def classify(prompt: str) -> str:
    """Короткий вызов LLM для классификации запросов (роутер чата).

    Использует ROUTER_MODEL — ей может быть более быстрая малая модель.
    """
    try:
        response = _get_router_llm().invoke(prompt)
    except Exception as exc:
        raise OllamaUnavailableError(
            f"Ollama недоступна или не отвечает: {exc}"
        ) from exc

    return response.content
