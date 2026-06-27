from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from tests.pdf_factory import make_pdf_bytes


def make_client(tmp_path):
    app = create_app(Settings(WORKDIR=tmp_path))
    return TestClient(app), app


def upload_valid_pdf(client: TestClient, filename: str = "preview.pdf") -> str:
    response = client.post(
        "/upload",
        files={
            "files": (
                filename,
                make_pdf_bytes(page_count=2, width=595, height=842),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 200
    uploaded = response.json()["jobs"][0]
    assert uploaded["status"] == JobStatus.UPLOADED
    return uploaded["job_id"]


def test_preview_returns_page_metadata_without_eager_image_render(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)

    response = client.get(f"/preview/{job_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["filename"] == "preview.pdf"
    assert body["page_count"] == 2
    assert len(body["pages"]) == 2

    first = body["pages"][0]
    assert first["page_number"] == 1
    assert first["page_size"] == {"width": 595.0, "height": 842.0}
    assert first["preview_width"] > 0
    assert first["preview_height"] > 0
    assert first["scale"]["x"] > 0
    assert first["scale"]["y"] > 0
    assert first["image_url"].startswith(f"/previews/{job_id}/page-1.png")

    preview_path = Path(tmp_path) / "previews" / job_id / "page-1.png"
    assert not preview_path.exists()


def test_preview_image_url_renders_requested_page_on_demand(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)
    preview_response = client.get(f"/preview/{job_id}")
    image_url = preview_response.json()["pages"][0]["image_url"]
    preview_path = Path(tmp_path) / "previews" / job_id / "page-1.png"

    response = client.get(image_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
    assert preview_path.exists()
    assert preview_path.read_bytes().startswith(b"\x89PNG")


def test_preview_static_image_url_is_served(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)
    preview_response = client.get(f"/preview/{job_id}")
    image_url = preview_response.json()["pages"][0]["image_url"]

    response = client.get(image_url)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_preview_unknown_job_returns_404(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.get("/preview/job_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "job not found"


def test_preview_invalid_uploaded_pdf_marks_job_failed(tmp_path):
    client, app = make_client(tmp_path)
    response = client.post(
        "/upload",
        files={"files": ("broken.pdf", b"%PDF-not-valid", "application/pdf")},
    )
    job_id = response.json()["jobs"][0]["job_id"]

    response = client.get(f"/preview/{job_id}")

    assert response.status_code == 422
    job = app.state.jobs.get(job_id)
    assert job.status == JobStatus.FAILED
    assert job.errors


def test_frontend_index_is_served(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    assert "Подписание документов" in response.text
    assert "<title>Подписание PDF-документов</title>" in response.text
    assert 'href="/favicon.png"' in response.text
    assert "Перетащите PDF" in response.text
    assert "Настройки" in response.text
    assert "Скачать готовые PDF" in response.text
    assert "confidence" not in response.text
    assert "Candidates" not in response.text
    assert "Placements" not in response.text
    assert "job_id" not in response.text
    assert "ручное подтверждение" not in response.text
    assert response.headers["content-type"].startswith("text/html")
