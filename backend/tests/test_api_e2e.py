"""E2E через FastAPI TestClient: загрузка -> дашборд -> таблица -> отчёт -> чат."""
import io

import pytest
from fastapi.testclient import TestClient

from app import app

from conftest import DEFICIT_FILE, SALES_FILE


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def deficit_file_id(client):
    with open(DEFICIT_FILE, "rb") as f:
        response = client.post(
            "/upload",
            files={"file": ("deficit.xlsx", f)},
        )
    assert response.status_code == 200, response.text
    return response.json()["file_id"]


class TestUpload:
    def test_upload_returns_file_id(self, deficit_file_id):
        assert deficit_file_id

    def test_rejects_wrong_extension(self, client):
        response = client.post(
            "/upload",
            files={"file": ("data.txt", io.BytesIO(b"hello"))},
        )
        assert response.status_code == 422

    def test_rejects_fake_xlsx_content(self, client):
        response = client.post(
            "/upload",
            files={"file": ("fake.xlsx", io.BytesIO(b"not an excel file at all"))},
        )
        assert response.status_code == 422
        assert "не похоже" in response.json()["detail"]

    def test_rejects_oversized_file(self, client, monkeypatch):
        import app as backend_app

        monkeypatch.setattr(backend_app, "MAX_UPLOAD_BYTES", 1024)
        payload = b"PK\x03\x04" + b"0" * 4096
        response = client.post(
            "/upload",
            files={"file": ("big.xlsx", io.BytesIO(payload))},
        )
        assert response.status_code == 413

    def test_path_traversal_neutralized(self, client):
        with open(DEFICIT_FILE, "rb") as f:
            response = client.post(
                "/upload",
                files={"file": ("../../evil.xlsx", f)},
            )
        assert response.status_code == 200
        from config import UPLOAD_DIR

        assert not (UPLOAD_DIR.parent / "evil.xlsx").exists()
        assert not (UPLOAD_DIR / "evil.xlsx").exists()


class TestDashboardAndTable:
    def test_dashboard(self, client, deficit_file_id):
        response = client.post("/dashboard", json={"file_id": deficit_file_id})
        assert response.status_code == 200
        payload = response.json()
        assert payload["report_type"]
        assert payload["metadata"]["rows"] > 0
        assert payload.get("file_context", {}).get("summary")

    def test_table(self, client, deficit_file_id):
        response = client.post("/table", json={"file_id": deficit_file_id})
        assert response.status_code == 200
        assert 0 < len(response.json()["data"]) <= 100

    def test_full_report_structure(self, client, deficit_file_id):
        response = client.post("/report", json={"file_id": deficit_file_id})
        assert response.status_code == 200
        payload = response.json()
        for key in ("report_type", "narrative", "kpis", "charts",
                    "insights", "data_quality", "columns_overview"):
            assert key in payload, f"в отчёте нет секции {key}"
        assert len(payload["narrative"]) > 200

    def test_report_pdf_download(self, client, deficit_file_id):
        response = client.post(
            "/report/pdf",
            json={
                "file_id": deficit_file_id,
                "narrative": "Правка резюме для PDF",
                "comment": "Комментарий в PDF",
            },
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("application/pdf")
        assert response.content.startswith(b"%PDF")
        assert b"%%EOF" in response.content[-64:]
        assert "attachment" in response.headers.get("content-disposition", "")
        assert b"/XObject" in response.content or b"/Image" in response.content

    def test_unknown_file_id_404(self, client):
        response = client.post("/dashboard", json={"file_id": "nope"})
        assert response.status_code == 404


class TestChatE2E:
    """Сквозные прогоны 26.08 как регрессионный набор (быстрый путь, без LLM)."""

    @pytest.mark.parametrize(
        "question, marker",
        [
            ("Сколько строк?", None),
            ("Какой общий дефицит?", None),
            ("Топ-5 заказчиков", None),
            ("Круговая диаграмма дефицита по подразделениям", "pie"),
            ("Динамика по месяцам", "line"),
        ],
    )
    def test_chat(self, client, deficit_file_id, question, marker):
        response = client.post(
            "/chat",
            json={"file_id": deficit_file_id, "question": question},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["answer"]
        if marker:
            assert [c["chart_type"] for c in payload["charts"]] == [marker]


@pytest.fixture(scope="module")
def sales_file_id(client):
    with open(SALES_FILE, "rb") as f:
        response = client.post(
            "/upload",
            files={"file": ("sales.xlsx", f)},
        )
    assert response.status_code == 200, response.text
    return response.json()["file_id"]


class TestSalesE2E:

    def test_report_type_detected(self, client, sales_file_id):
        response = client.post("/dashboard", json={"file_id": sales_file_id})
        assert response.json()["report_type"] == "sales_pipeline"

    def test_user_regression_questions(self, client, sales_file_id):
        # вопросы пользователя из бага 26.08
        response = client.post(
            "/chat",
            json={
                "file_id": sales_file_id,
                "question": "Разбивка долга по службам в виде круговой",
            },
        )
        assert [c["chart_type"] for c in response.json()["charts"]] == ["pie"]

        response = client.post(
            "/chat",
            json={
                "file_id": sales_file_id,
                "question": "Выручка по месяцам в виде круговой диаграммы",
            },
        )
        assert [c["chart_type"] for c in response.json()["charts"]] == ["pie"]
