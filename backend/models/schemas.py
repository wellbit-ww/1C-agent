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
Question = Annotated[str, Field(min_length=1, max_length=MAX_QUESTION_CHARS)]


class ChatRequest(BaseModel):
    file_id: FileId
    question: Question


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


class ReportPdfRequest(BaseModel):
    file_id: FileId
    filename: str | None = Field(default=None, max_length=MAX_FILENAME_CHARS)
    narrative: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)
    insights: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)
    comment: str | None = Field(default=None, max_length=MAX_NARRATIVE_CHARS)


class HistoryRequest(BaseModel):
    file_id: FileId


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
