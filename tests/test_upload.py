from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import JobStatus


def make_client(tmp_path):
    app = create_app(Settings(WORKDIR=tmp_path))
    return TestClient(app), app


def pdf_bytes() -> bytes:
    return b"%PDF-1.4\n% minimal test pdf\n"


def test_upload_single_pdf_creates_job(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/upload",
        files={"files": ("document.pdf", pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["jobs"]) == 1
    uploaded = body["jobs"][0]
    assert uploaded["status"] == JobStatus.UPLOADED
    assert uploaded["job_id"].startswith("job_")
    assert uploaded["filename"] == "document.pdf"

    job = app.state.jobs.get(uploaded["job_id"])
    assert job is not None
    assert job.source_path.exists()
    assert job.source_path.read_bytes().startswith(b"%PDF-")


def test_upload_multiple_pdfs_creates_independent_jobs(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/upload",
        files=[
            ("files", ("first.pdf", pdf_bytes(), "application/pdf")),
            ("files", ("second.pdf", pdf_bytes(), "application/pdf")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert [job["status"] for job in body["jobs"]] == [
        JobStatus.UPLOADED,
        JobStatus.UPLOADED,
    ]
    job_ids = {job["job_id"] for job in body["jobs"]}
    assert len(job_ids) == 2
    assert len(app.state.jobs.list()) == 2


def test_jobs_endpoint_lists_uploaded_documents_with_status(tmp_path):
    client, _app = make_client(tmp_path)
    upload_response = client.post(
        "/upload",
        files=[
            ("files", ("first.pdf", pdf_bytes(), "application/pdf")),
            ("files", ("second.pdf", pdf_bytes(), "application/pdf")),
        ],
    )
    uploaded_jobs = upload_response.json()["jobs"]

    response = client.get("/jobs")

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == [
        {
            "job_id": uploaded_jobs[0]["job_id"],
            "filename": "first.pdf",
            "status": JobStatus.UPLOADED,
            "confirmed_by_user": False,
            "errors": [],
            "warnings": [],
        },
        {
            "job_id": uploaded_jobs[1]["job_id"],
            "filename": "second.pdf",
            "status": JobStatus.UPLOADED,
            "confirmed_by_user": False,
            "errors": [],
            "warnings": [],
        },
    ]


def test_reset_clears_jobs_and_runtime_files(tmp_path):
    client, app = make_client(tmp_path)
    upload_response = client.post(
        "/upload",
        files={"files": ("document.pdf", pdf_bytes(), "application/pdf")},
    )
    uploaded = upload_response.json()["jobs"][0]
    job = app.state.jobs.get(uploaded["job_id"])
    assert job is not None
    assert job.source_path.exists()

    response = client.post("/reset")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert app.state.jobs.list() == []
    assert client.get("/jobs").json() == {"jobs": []}
    assert not job.source_path.exists()
    assert (tmp_path / "uploads").exists()
    assert (tmp_path / "previews").exists()
    assert (tmp_path / "exports").exists()


def test_upload_rejects_non_pdf_without_breaking_valid_files(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/upload",
        files=[
            ("files", ("valid.pdf", pdf_bytes(), "application/pdf")),
            ("files", ("bad.txt", b"not a pdf", "text/plain")),
        ],
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["jobs"]) == 2
    assert body["jobs"][0]["status"] == JobStatus.UPLOADED
    assert body["jobs"][0]["job_id"] is not None
    assert body["jobs"][1]["status"] == JobStatus.FAILED
    assert body["jobs"][1]["job_id"] is None
    assert "file extension must be .pdf" in body["jobs"][1]["errors"][0]
    assert "file content is not a PDF" in body["jobs"][1]["errors"][0]
    assert len(app.state.jobs.list()) == 1


def test_upload_rejects_pdf_extension_with_wrong_magic_bytes(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/upload",
        files={"files": ("fake.pdf", b"not a pdf", "application/pdf")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"][0]["status"] == JobStatus.FAILED
    assert "file content is not a PDF" in body["jobs"][0]["errors"][0]
    assert app.state.jobs.list() == []


def test_upload_sanitizes_filename(tmp_path):
    client, app = make_client(tmp_path)

    response = client.post(
        "/upload",
        files={"files": ("../unsafe.pdf", pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 200
    uploaded = response.json()["jobs"][0]
    job = app.state.jobs.get(uploaded["job_id"])
    assert job is not None
    assert job.filename == "unsafe.pdf"
    assert job.source_path.parent.parent == tmp_path / "uploads"
