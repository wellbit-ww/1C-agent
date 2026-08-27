# Excel AI Agent — аналитик отчётов 1С

Загружаете Excel-выгрузку из 1С — агент показывает дашборд, подробный
редактируемый отчёт и отвечает на вопросы в чате (статистика и диаграммы
по запросу). Расчёты детерминированные (pandas), LLM используется только
для понимания свободных формулировок и текстовых ответов.

## Архитектура

- `backend/` — FastAPI: загрузка, парсер 1C-выгрузок (openpyxl, outline-уровни,
  многострочные шапки), роутер чата (keyword fast-path → LLM JSON-классификация →
  fallback с подсказками), отчёты по профилям (YAML).
- `ui/` — Streamlit: загрузка, дашборд, страница подробного отчёта, чат с
  инлайн-графиками (plotly) и чипами-подсказками.
- LLM — локальная Ollama (по умолчанию `qwen3:8b`).

## Требования

- Python 3.11+
- [Ollama](https://ollama.com) с моделью: `ollama pull qwen3:8b`

## Установка

```powershell
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt -r ui\requirements.txt
```

Для разработки (тесты):

```powershell
venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

## Запуск

Три процесса:

```powershell
# 1. Ollama (если не запущена как служба)
ollama serve

# 2. Backend
cd backend
..\venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000

# 3. UI (в другом терминале, из корня проекта)
venv\Scripts\python.exe -m streamlit run ui\app.py
```

UI откроется на http://localhost:8501. Без Ollama приложение работает,
но LLM-функции вернут 503 (статистика и графики — полностью локальные).

## Конфигурация

Скопируйте `.env.example` в `.env`. Основные переменные: адрес и модели
Ollama (`OLLAMA_BASE_URL`, `EXCEL_AGENT_MODEL`, `EXCEL_AGENT_ROUTER_MODEL`),
лимит загрузки (`EXCEL_AGENT_MAX_UPLOAD_MB`), CORS, адрес backend для UI
(`EXCEL_AGENT_API_URL`).

## Тесты

```powershell
cd backend
..\venv\Scripts\python.exe -m pytest tests -v
```

Набор: юниты парсера/резолвера/роутера чата + e2e через API на реальных
выгрузках 1С (`tests/data/`). LLM-тесты автоматически пропускаются, если
Ollama недоступна.
