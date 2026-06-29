from langchain_ollama import ChatOllama

from services.exceptions import OllamaUnavailableError

llm = ChatOllama(
    model="qwen3:8b",
    base_url="http://127.0.0.1:11434",
    temperature=0,
)


def ask_llm(prompt: str):
    try:
        response = llm.invoke(prompt)
    except Exception as exc:
        raise OllamaUnavailableError(
            f"Ollama недоступна или не отвечает: {exc}"
        ) from exc

    return response.content
