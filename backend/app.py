import logging
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from config import (
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
from services.cache_service import set_dataframe
from services.storage_service import get_file, get_original_name, save_upload
from services.exceptions import (
    EmptyDataFrameError,
    InvalidFileError,
    OllamaUnavailableError,
)
from models.schemas import ChatRequest, InsightsRequest, ChartRequest, ProfileRequest, DashboardRequest, TableRequest, ReportRequest

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
async def chat(
    request: ChatRequest,
):
    file_path = _get_file_path_or_404(request.file_id)

    result = _handle_service_errors(
        agent.handle_chat_message,
        request.file_id,
        file_path,
        request.question,
    )

    return result


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
    report_type, profile = get_profile_for_df(df)
    
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

    # Return top 100 rows for display
    # Replace NaNs with None to ensure JSON serializability
    data = df.head(100).fillna("").to_dict(orient="records")

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

