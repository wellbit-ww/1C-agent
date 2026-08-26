from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException

from agents.excel_agent import ExcelAgent
from services.excel_service import read_excel, validate_excel_filename
from services.cache_service import set_dataframe
from services.storage_service import save_file, get_file
from services.exceptions import (
    EmptyDataFrameError,
    InvalidFileError,
    OllamaUnavailableError,
)
from models.schemas import ChatRequest, InsightsRequest, ChartRequest, ProfileRequest, DashboardRequest, TableRequest, ReportRequest


app = FastAPI(
    title="Excel AI Agent",
)

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

agent = ExcelAgent()


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


@app.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
):
    try:
        validate_excel_filename(file.filename)
        file_path = UPLOAD_DIR / file.filename

        with open(file_path, "wb") as f:
            f.write(await file.read())

        file_id = save_file(str(file_path))
        df = read_excel(str(file_path))
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
    validate_excel_filename(file.filename)

    file_path = UPLOAD_DIR / file.filename

    with open(file_path, "wb") as f:
        f.write(await file.read())

    description = _handle_service_errors(
        agent.analyze_file,
        str(file_path),
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
        request.filename or Path(file_path).name,
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

