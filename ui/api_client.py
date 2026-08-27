import os

import requests


API_BASE_URL = os.getenv("EXCEL_AGENT_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = int(os.getenv("EXCEL_AGENT_REQUEST_TIMEOUT", "300"))


class ApiClientError(Exception):
    pass


def _build_url(path: str, base_url: str = API_BASE_URL) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _raise_for_response(response: requests.Response) -> None:
    if response.ok:
        return

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    message = (
        payload.get("detail")
        or payload.get("error")
        or response.text
        or f"HTTP {response.status_code}"
    )
    raise ApiClientError(str(message))


def check_backend(base_url: str = API_BASE_URL) -> tuple[bool, str]:
    try:
        response = requests.get(
            _build_url("/", base_url),
            timeout=5,
        )
    except requests.RequestException:
        return False, "Backend недоступен"

    if not response.ok:
        return False, f"Backend вернул HTTP {response.status_code}"

    return True, "Backend доступен"


def upload_file(uploaded_file, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/upload", base_url),
            files={
                "file": (
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    uploaded_file.type,
                )
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def analyze_file(uploaded_file, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/analyze", base_url),
            files={
                "file": (
                    uploaded_file.name,
                    uploaded_file.getvalue(),
                    uploaded_file.type,
                )
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_full_report(file_id: str, filename: str | None = None, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/report", base_url),
            json={
                "file_id": file_id,
                "filename": filename,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_dashboard(file_id: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/dashboard", base_url),
            json={
                "file_id": file_id,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_insights(file_id: str, base_url: str = API_BASE_URL) -> list[str]:
    try:
        response = requests.post(
            _build_url("/insights", base_url),
            json={
                "file_id": file_id,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        payload = response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc

    return list(payload.get("insights", []))


def generate_chart(file_id: str, question: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/chart", base_url),
            json={
                "file_id": file_id,
                "question": question,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_detailed_table(file_id: str, base_url: str = API_BASE_URL) -> list[dict]:
    try:
        response = requests.post(
            _build_url("/table", base_url),
            json={
                "file_id": file_id,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        payload = response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc

    return payload.get("data", [])

def chat(file_id: str, question: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/chat", base_url),
            json={
                "file_id": file_id,
                "question": question,
            },
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        payload = response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc

    if "error" in payload:
        raise ApiClientError(str(payload["error"]))

    return {
        "answer": str(payload.get("answer", "Ответ не получен")),
        "charts": list(payload.get("charts", [])),
    }
