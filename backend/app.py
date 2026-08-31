import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import (
    CHAT_HISTORY_LIMIT,
    CORS_ORIGINS,
    MAIN_MODEL,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_MB,
    OLLAMA_BASE_URL,
    OLLAMA_HEALTHCHECK_TIMEOUT,
    ROUTER_MODEL,
)
from agents.excel_agent import ExcelAgent
from services.excel_service import (
    read_excel,
    validate_excel_content,
    validate_excel_filename,
)
from services import db_service
from services.cache_service import set_dataframe
from services.storage_service import get_file, get_original_name, save_upload
from services.exceptions import (
    EmptyDataFrameError,
    InvalidFileError,
    OllamaUnavailableError,
)
from models.schemas import (
    ChatRequest,
    DashboardGenerateRequest,
    DashboardPinRequest,
    DashboardRequest,
    DashboardSpecSaveRequest,
    HistoryRequest,
    InsightsRequest,
    ChartRequest,
    ProfileRequest,
    TableRequest,
    ReportRequest,
)

logger = logging.getLogger("excel_agent")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_ollama_availability()
    yield


app = FastAPI(
    title="Excel AI Agent",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = ExcelAgent()


def _check_ollama_availability() -> None:
    """Не роняет сервис, только честно логирует состояние LLM-бэкенда."""
    try:
        response = httpx.get(
            f"{OLLAMA_BASE_URL}/api/tags",
            timeout=OLLAMA_HEALTHCHECK_TIMEOUT,
        )
        models = {m.get("name", "") for m in response.json().get("models", [])}
    except Exception as exc:
        logger.warning(
            "Ollama недоступна (%s): %s. LLM-функции вернут 503, "
            "статистика и графики будут работать.",
            OLLAMA_BASE_URL,
            exc,
        )
        return

    for model in {MAIN_MODEL, ROUTER_MODEL}:
        if not any(model in name for name in models):
            logger.warning(
                "Модель %s не найдена в Ollama. Установите: ollama pull %s",
                model,
                model,
            )


def _get_file_path_or_404(file_id: str) -> str:
    file_path = get_file(file_id)

    if not file_path:
        raise HTTPException(
            status_code=404,
            detail="Файл не найден",
        )

    return file_path


def _handle_service_errors(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except (InvalidFileError, EmptyDataFrameError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OllamaUnavailableError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/")
def root():
    return {
        "status": "ok",
    }


async def _read_upload_securely(file: UploadFile) -> tuple[str, str]:
    """Валидирует и сохраняет upload, возвращает (file_id, file_path).

    Имя на диске генерируется нами, размер ограничен, содержимое
    проверяется по магическим байтам.
    """
    try:
        validate_excel_filename(file.filename)

        data = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Файл больше допустимого размера {MAX_UPLOAD_MB} МБ",
            )

        validate_excel_content(data, file.filename)
    except InvalidFileError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    file_id = save_upload(data, file.filename)
    return file_id, get_file(file_id)


@app.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
):
    file_id, file_path = await _read_upload_securely(file)

    try:
        df = read_excel(file_path)
        set_dataframe(file_id, df)
    except (InvalidFileError, EmptyDataFrameError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return {
        "file_id": file_id,
    }


@app.post("/analyze")
async def analyze_excel(
    file: UploadFile = File(...),
):
    _, file_path = await _read_upload_securely(file)

    description = _handle_service_errors(
        agent.analyze_file,
        file_path,
    )

    return {
        "filename": file.filename,
        "description": description,
    }


@app.post("/chat")
def chat(
    request: ChatRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    history = db_service.get_chat_history(
        request.file_id, limit=CHAT_HISTORY_LIMIT
    )

    result = _handle_service_errors(
        agent.handle_chat_message,
        request.file_id,
        file_path,
        request.question,
        history,
    )

    if result is not None:
        try:
            db_service.add_chat_message(request.file_id, "user", request.question)
            db_service.add_chat_message(
                request.file_id, "assistant", result["answer"], result.get("charts")
            )
        except Exception as exc:
            logger.warning("Не удалось сохранить сообщения чата: %s", exc)

    return result


@app.post("/history")
async def chat_history(
    request: HistoryRequest,
):
    _get_file_path_or_404(request.file_id)
    return {
        "messages": db_service.get_chat_history(
            request.file_id, limit=CHAT_HISTORY_LIMIT
        )
    }


@app.post("/insights")
async def insights(
    request: InsightsRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    insights_list = _handle_service_errors(
        agent.get_insights,
        request.file_id,
        file_path,
    )

    return {
        "insights": insights_list,
    }


@app.post("/profile")
async def profile_report(
    request: ProfileRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    df = _handle_service_errors(
        agent._load_dataframe,
        request.file_id,
        file_path,
    )
    
    from services.report_service import get_profile_for_df
    from services.storage_service import get_original_name

    report_type, profile = get_profile_for_df(
        df, filename=get_original_name(request.file_id)
    )
    
    return {
        "report_type": report_type,
        "kpis": profile.get_kpis(df),
        "charts": profile.get_charts(df),
        "insights": profile.get_insights(df)
    }

@app.post("/dashboard")
async def dashboard(
    request: DashboardRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    data = _handle_service_errors(
        agent.get_dashboard,
        request.file_id,
        file_path,
    )

    return data


# ---------------------------------------------------------------------------
# Дашборды v2: NL-генерация/редактирование, пин из чата, комментарии
# ---------------------------------------------------------------------------

def _render_and_respond(df, spec) -> dict:
    from services.dashboard_engine import render_spec

    rendered = render_spec(df, spec)
    return {"tabs": rendered["tabs"], "spec": spec.model_dump()}


def _load_df(file_id: str, file_path: str):
    return _handle_service_errors(agent._load_dataframe, file_id, file_path)


@app.post("/dashboard/generate")
def dashboard_generate(request: DashboardGenerateRequest):
    """Собрать дашборд с нуля по текстовому запросу (LLM -> спека, иначе запасной)."""
    from services import dashboard_service

    file_path = _get_file_path_or_404(request.file_id)
    df = _load_df(request.file_id, file_path)
    fallback = dashboard_service.get_current_spec(request.file_id, df)
    spec, warning = _handle_service_errors(
        dashboard_service.assemble_spec,
        df,
        request.request,
        None,
        fallback,
    )
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail="Не удалось собрать дашборд по запросу — попробуйте переформулировать",
        )
    dashboard_service.save_spec(request.file_id, spec)
    payload = _render_and_respond(df, spec)
    if warning:
        payload["warning"] = warning
    return payload


@app.post("/dashboard/edit")
def dashboard_edit(request: DashboardGenerateRequest):
    """Отредактировать текущий дашборд текстовой командой."""
    from services import dashboard_service

    file_path = _get_file_path_or_404(request.file_id)
    df = _load_df(request.file_id, file_path)
    current = dashboard_service.get_current_spec(request.file_id, df)
    if current is None:
        raise HTTPException(status_code=404, detail="Нет дашборда для редактирования")
    spec, warning = _handle_service_errors(
        dashboard_service.assemble_spec,
        df,
        request.request,
        current,
        current,
    )
    if spec is None:
        raise HTTPException(
            status_code=422,
            detail="Не удалось применить правку — попробуйте переформулировать",
        )
    dashboard_service.save_spec(request.file_id, spec)
    payload = _render_and_respond(df, spec)
    if warning:
        payload["warning"] = warning
    return payload


@app.post("/dashboard/pin")
async def dashboard_pin(request: DashboardPinRequest):
    """Закрепить график из чата на первой вкладке дашборда."""
    from services import dashboard_service

    file_path = _get_file_path_or_404(request.file_id)
    df = _load_df(request.file_id, file_path)
    ok, message = _handle_service_errors(
        dashboard_service.pin_tile, request.file_id, df, request.tile
    )
    if not ok:
        raise HTTPException(status_code=422, detail=message)
    return {"ok": True, "message": message}


@app.post("/dashboard/spec")
async def dashboard_spec_save(request: DashboardSpecSaveRequest):
    """Сохранить спеку из простого редактора UI."""
    from models.dashboard_spec import DashboardSpec
    from pydantic import ValidationError
    from services import dashboard_service

    file_path = _get_file_path_or_404(request.file_id)
    df = _load_df(request.file_id, file_path)
    try:
        spec = DashboardSpec.model_validate(request.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Невалидная спека: {exc.errors()[0]['msg']}")
    dashboard_service.save_spec(request.file_id, spec)
    return _render_and_respond(df, spec)


@app.post("/dashboard/comments")
def dashboard_comments(request: DashboardRequest):
    """Авто-комментарии LLM к вкладкам (кэшируются по хэшу данных)."""
    from services import dashboard_service

    file_path = _get_file_path_or_404(request.file_id)
    df = _load_df(request.file_id, file_path)
    spec = dashboard_service.get_current_spec(request.file_id, df)
    if spec is None:
        raise HTTPException(status_code=404, detail="Нет дашборда для комментариев")
    from services.dashboard_engine import render_spec

    rendered = render_spec(df, spec)
    comments = _handle_service_errors(
        dashboard_service.generate_comments, request.file_id, df, rendered["tabs"]
    )
    return {"comments": comments}


@app.post("/report")
async def full_report(
    request: ReportRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    df = _handle_service_errors(
        agent._load_dataframe,
        request.file_id,
        file_path,
    )

    from services.report_service import get_full_report

    report = _handle_service_errors(
        get_full_report,
        df,
        request.filename or get_original_name(request.file_id) or Path(file_path).name,
    )

    return report


@app.post("/table")
async def detailed_table(
    request: TableRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    df = _handle_service_errors(
        agent._load_dataframe,
        request.file_id,
        file_path,
    )

    from services.report_service import _safe_sample_cell

    data = [
        {str(k): _safe_sample_cell(v) for k, v in row.items()}
        for row in df.head(100).to_dict(orient="records")
    ]

    return {"data": data}

@app.post("/chart")
async def generate_chart(
    request: ChartRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    chart_data = _handle_service_errors(
        agent.generate_chart,
        request.file_id,
        file_path,
        request.question,
    )

    if "error" in chart_data:
        raise HTTPException(status_code=422, detail=chart_data["error"])

    return chart_data

