from typing import Annotated

from pydantic import BaseModel, Field, model_validator

from config import (
    MAX_FILE_ID_CHARS,
    MAX_FILENAME_CHARS,
    MAX_JSON_PAYLOAD_CHARS,
    MAX_NARRATIVE_CHARS,
    MAX_QUESTION_CHARS,
)

FileId = Annotated[
    str,
    Field(min_length=1, max_length=MAX_FILE_ID_CHARS, pattern=r"^[a-zA-Z0-9_-]+$"),
]
WorkspaceId = Annotated[
    str,
    Field(min_length=1, max_length=MAX_FILE_ID_CHARS, pattern=r"^[a-zA-Z0-9_-]+$"),
]
Question = Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARS)]


class ChatRequest(BaseModel):
    file_id: FileId | None = None
    workspace_id: WorkspaceId | None = None
    question: Question

    @model_validator(mode="after")
    def _need_target(self):
        if not self.file_id and not self.workspace_id:
            raise ValueError("Нужен file_id или workspace_id")
        return self


class ChartRequest(BaseModel):
    file_id: FileId
    question: Question


class InsightsRequest(BaseModel):
    file_id: FileId


class ProfileRequest(BaseModel):
    file_id: FileId


class DashboardRequest(BaseModel):
    file_id: FileId


class FileContextRequest(BaseModel):
    file_id: FileId


class TableRequest(BaseModel):
    file_id: FileId


class ReportRequest(BaseModel):
    file_id: FileId
    filename: str | None = Field(default=None, max_length=MAX_FILENAME_CHARS)


class ReportChartIn(BaseModel):
    title: str = Field(default="", max_length=200)
    plotly_json: str | None = Field(default=None, max_length=150_000)
    table: dict | None = None

    @model_validator(mode="after")
    def _need_chart_or_table(self):
        json_text = (self.plotly_json or "").strip()
        if json_text:
            self.plotly_json = json_text
            return self
        self.plotly_json = None
        if isinstance(self.table, dict) and (self.table.get("columns") or self.table.get("rows")):
            return self
        raise ValueError("Нужен график или таблица")


class ReportPdfRequest(BaseModel):
    file_id: FileId
    filename: str | None = Field(default=None, max_length=MAX_FILENAME_CHARS)
    narrative: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)
    insights: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)
    comment: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)
    report_charts: list[ReportChartIn] | None = Field(default=None, max_length=8)

    @model_validator(mode="after")
    def _cap_json(self):
        if len(self.model_dump_json()) > MAX_JSON_PAYLOAD_CHARS * 4:
            raise ValueError("Слишком большой набор графиков для PDF")
        return self


class HistoryRequest(BaseModel):
    file_id: FileId | None = None
    workspace_id: WorkspaceId | None = None

    @model_validator(mode="after")
    def _need_target(self):
        if not self.file_id and not self.workspace_id:
            raise ValueError("Нужен file_id или workspace_id")
        return self


class WorkspaceAttachRequest(BaseModel):
    file_id: FileId
    replace: bool = False


class WorkspaceDashboardRequest(BaseModel):
    workspace_id: WorkspaceId


class DashboardGenerateRequest(BaseModel):
    file_id: FileId
    request: Question


class DashboardPinRequest(BaseModel):
    file_id: FileId
    tile: dict

    @model_validator(mode="after")
    def _cap_json(self):
        if len(self.model_dump_json()) > MAX_JSON_PAYLOAD_CHARS:
            raise ValueError("Слишком большой JSON тайла")
        return self


class DashboardSpecSaveRequest(BaseModel):
    file_id: FileId
    spec: dict

    @model_validator(mode="after")
    def _cap_json(self):
        if len(self.model_dump_json()) > MAX_JSON_PAYLOAD_CHARS:
            raise ValueError("Слишком большая спека дашборда")
        return self
