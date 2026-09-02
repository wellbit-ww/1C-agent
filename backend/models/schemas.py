from pydantic import BaseModel


class ChatRequest(BaseModel):
    file_id: str
    question: str


class ChartRequest(BaseModel):
    file_id: str
    question: str


class InsightsRequest(BaseModel):
    file_id: str


class ProfileRequest(BaseModel):
    file_id: str


class DashboardRequest(BaseModel):
    file_id: str


class FileContextRequest(BaseModel):
    file_id: str


class TableRequest(BaseModel):
    file_id: str


class ReportRequest(BaseModel):
    file_id: str
    filename: str | None = None


class HistoryRequest(BaseModel):
    file_id: str


class DashboardGenerateRequest(BaseModel):
    file_id: str
    request: str


class DashboardPinRequest(BaseModel):
    file_id: str
    tile: dict


class DashboardSpecSaveRequest(BaseModel):
    file_id: str
    spec: dict
