import json
import uuid

# In-memory storage for charts for MVP
_charts: dict[str, dict] = {}


def save_chart(chart_json: dict) -> str:
    chart_id = str(uuid.uuid4())
    _charts[chart_id] = chart_json
    return chart_id


def get_chart(chart_id: str) -> dict | None:
    return _charts.get(chart_id)


def delete_chart(chart_id: str) -> None:
    _charts.pop(chart_id, None)


def clear_charts() -> None:
    _charts.clear()
