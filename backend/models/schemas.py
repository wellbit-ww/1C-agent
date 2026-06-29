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


class TableRequest(BaseModel):
    file_id: str
