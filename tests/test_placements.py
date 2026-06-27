from base64 import b64decode

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from tests.pdf_factory import make_pdf_bytes


PNG_BYTES = b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)


def make_client(tmp_path):
    signature_path = tmp_path / "signature.png"
    stamp_path = tmp_path / "stamp.png"
    signature_path.write_bytes(PNG_BYTES)
    stamp_path.write_bytes(PNG_BYTES)
    app = create_app(
        Settings(
            WORKDIR=tmp_path / "runtime",
            SIGNATURE_IMAGE_PATH=signature_path,
            STAMP_IMAGE_PATH=stamp_path,
        )
    )
    return TestClient(app), app


def upload_valid_pdf(client: TestClient) -> str:
    response = client.post(
        "/upload",
        files={
            "files": (
                "manual.pdf",
                make_pdf_bytes(page_count=1, width=595, height=842),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 200
    return response.json()["jobs"][0]["job_id"]


def placement_payload():
    return {
        "placements": [
            {
                "placement_id": "manual_1",
                "page_number": 1,
                "signature": {
                    "enabled": True,
                    "bbox": {"x0": 120, "y0": 610, "x1": 330, "y1": 690},
                    "rotation": 0,
                },
                "stamp": {
                    "enabled": True,
                    "bbox": {"x0": 310, "y0": 585, "x1": 430, "y1": 705},
                    "rotation": 0,
                },
                "name": None,
                "confidence": 1,
                "needs_manual_review": False,
                "source": "manual",
            }
        ],
        "confirmed_by_user": True,
    }


def test_save_placements_updates_job_and_status(tmp_path):
    client, app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)

    response = client.post(f"/placement/{job_id}", json=placement_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job_id
    assert body["status"] == JobStatus.READY
    assert body["confirmed_by_user"] is True
    assert len(body["placements"]) == 1

    job = app.state.jobs.get(job_id)
    assert job.status == JobStatus.READY
    assert job.confirmed_by_user is True
    assert job.placements[0].placement_id == "manual_1"


def test_preview_returns_saved_placements(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)
    client.post(f"/placement/{job_id}", json=placement_payload())

    response = client.get(f"/preview/{job_id}")

    assert response.status_code == 200
    placements = response.json()["placements"]
    assert len(placements) == 1
    assert placements[0]["placement_id"] == "manual_1"
    assert placements[0]["signature"]["bbox"] == {
        "x0": 120.0,
        "y0": 610.0,
        "x1": 330.0,
        "y1": 690.0,
    }


def test_jobs_endpoint_shows_ready_after_manual_save(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_valid_pdf(client)
    client.post(f"/placement/{job_id}", json=placement_payload())

    response = client.get("/jobs")

    assert response.status_code == 200
    assert response.json()["jobs"][0]["status"] == JobStatus.READY
    assert response.json()["jobs"][0]["confirmed_by_user"] is True


def test_save_placements_unknown_job_returns_404(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post("/placement/job_missing", json=placement_payload())

    assert response.status_code == 404
    assert response.json()["detail"] == "job not found"


def test_asset_endpoints_serve_signature_and_stamp(tmp_path):
    client, _app = make_client(tmp_path)

    signature = client.get("/assets/signature")
    stamp = client.get("/assets/stamp")

    assert signature.status_code == 200
    assert signature.content.startswith(b"\x89PNG")
    assert stamp.status_code == 200
    assert stamp.content.startswith(b"\x89PNG")
