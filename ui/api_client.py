import os
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:  # dotenv не обязателен: переменные могут быть в окружении
    pass


API_BASE_URL = os.getenv("EXCEL_AGENT_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT = int(os.getenv("EXCEL_AGENT_REQUEST_TIMEOUT", "300"))


def _auth_headers() -> dict:
    token = os.getenv("EXCEL_AGENT_API_TOKEN", "").strip()
    if token:
        return {"X-API-Token": token}
    return {}


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
            headers=_auth_headers(),
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
            headers=_auth_headers(),
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
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_history(file_id: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/history", base_url),
            json={"file_id": file_id},
            headers=_auth_headers(),
            timeout=30,
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
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def download_report_pdf(
    file_id: str,
    filename: str | None = None,
    narrative: str | None = None,
    insights: str | None = None,
    comment: str | None = None,
    base_url: str = API_BASE_URL,
) -> bytes:
    payload = {"file_id": file_id, "filename": filename}
    if narrative is not None:
        payload["narrative"] = narrative
    if insights is not None:
        payload["insights"] = insights
    if comment is not None:
        payload["comment"] = comment
    try:
        response = requests.post(
            _build_url("/report/pdf", base_url),
            json=payload,
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.content
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def get_detailed_table(file_id: str, base_url: str = API_BASE_URL) -> list[dict]:
    try:
        response = requests.post(
            _build_url("/table", base_url),
            json={
                "file_id": file_id,
            },
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        payload = response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc

    return payload.get("data", [])

def _post_json(path: str, payload: dict, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url(path, base_url),
            json=payload,
            headers=_auth_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc


def dashboard_generate(file_id: str, request: str, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/dashboard/generate", {"file_id": file_id, "request": request}, base_url)


def dashboard_edit(file_id: str, request: str, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/dashboard/edit", {"file_id": file_id, "request": request}, base_url)


def dashboard_pin(file_id: str, tile: dict, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/dashboard/pin", {"file_id": file_id, "tile": tile}, base_url)


def dashboard_save_spec(file_id: str, spec: dict, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/dashboard/spec", {"file_id": file_id, "spec": spec}, base_url)


def dashboard_comments(file_id: str, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/dashboard/comments", {"file_id": file_id}, base_url)


def enrich_file_context(file_id: str, base_url: str = API_BASE_URL) -> dict:
    return _post_json("/file-context", {"file_id": file_id}, base_url)


def chat(file_id: str, question: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.post(
            _build_url("/chat", base_url),
            json={
                "file_id": file_id,
                "question": question,
            },
            headers=_auth_headers(),
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


def delete_file(file_id: str, base_url: str = API_BASE_URL) -> dict:
    try:
        response = requests.delete(
            _build_url(f"/file/{file_id}", base_url),
            headers=_auth_headers(),
            timeout=30,
        )
        _raise_for_response(response)
        return response.json()
    except requests.RequestException as exc:
        raise ApiClientError("Backend недоступен") from exc
