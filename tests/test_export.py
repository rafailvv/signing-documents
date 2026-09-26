from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

import fitz
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from tests.pdf_factory import make_pdf_bytes, make_signature_pdf_bytes
from tests.test_placements import PNG_BYTES


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


def upload_pdf(client: TestClient, filename: str = "document.pdf") -> str:
    response = client.post(
        "/upload",
        files={
            "files": (
                filename,
                make_pdf_bytes(page_count=1, width=595, height=842),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 200
    return response.json()["jobs"][0]["job_id"]


def save_placement(client: TestClient, job_id: str) -> None:
    payload = {
        "placements": [
            {
                "placement_id": "manual_export_1",
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
                "name": {
                    "enabled": True,
                    "text": "Венедиктов Р.В.",
                    "bbox": {"x0": 345, "y0": 660, "x1": 510, "y1": 685},
                },
                "confidence": 1,
                "needs_manual_review": False,
                "source": "manual",
            }
        ],
        "confirmed_by_user": True,
    }
    response = client.post(f"/placement/{job_id}", json=payload)
    assert response.status_code == 200


def test_export_single_pdf_and_download_contains_overlays_and_name(tmp_path):
    client, app = make_client(tmp_path)
    job_id = upload_pdf(client, "single.pdf")
    save_placement(client, job_id)

    response = client.post("/export", json={"job_ids": [job_id]})

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "pdf"
    assert body["download_url"] == f"/download/{body['export_id']}"
    assert body["files"][0]["output_filename"] == "single.pdf"
    assert app.state.jobs.get(job_id).status == JobStatus.EXPORTED

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")

    with fitz.open(stream=download.content, filetype="pdf") as document:
        page = document[0]
        assert "Венедиктов" in page.get_text()
        assert len(page.get_images(full=True)) >= 2


def test_large_transparent_signature_is_downsampled_and_reused(tmp_path):
    client, _app = make_client(tmp_path)
    signature = Image.new("RGBA", (2400, 1800), (255, 255, 255, 0))
    draw = ImageDraw.Draw(signature)
    for offset in range(0, 1800, 30):
        draw.line((0, offset, 2399, min(1799, offset + 400)), fill=(20, 40, 180, 180), width=5)
    signature.save(tmp_path / "signature.png")

    upload = client.post(
        "/upload",
        files={"files": ("large-signature.pdf", make_pdf_bytes(page_count=2), "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["jobs"][0]["job_id"]
    placements = [
        {
            "placement_id": f"page_{page_number}",
            "page_number": page_number,
            "signature": {
                "enabled": True,
                "bbox": {"x0": 120, "y0": 610, "x1": 330, "y1": 690},
            },
            "confidence": 1,
            "needs_manual_review": False,
            "source": "manual",
        }
        for page_number in (1, 2)
    ]
    saved = client.post(f"/placement/{job_id}", json={"placements": placements, "confirmed_by_user": True})
    assert saved.status_code == 200

    response = client.post("/export", json={"job_ids": [job_id]})
    assert response.status_code == 200
    download = client.get(response.json()["download_url"])
    assert download.status_code == 200
    assert len(download.content) < 300_000

    with fitz.open(stream=download.content, filetype="pdf") as document:
        first_image = document[0].get_images(full=True)[0]
        second_image = document[1].get_images(full=True)[0]
        assert first_image[0] == second_image[0]
        assert first_image[1] > 0  # Transparency survives the resize.
        assert first_image[2] <= 875  # 210 pt at 300 DPI.
        assert first_image[3] <= 334  # 80 pt at 300 DPI.


def test_export_single_pdf_can_force_zip(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_pdf(client, "single_archive.pdf")
    save_placement(client, job_id)

    response = client.post("/export", json={"job_ids": [job_id], "force_zip": True})

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "zip"
    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")

    with ZipFile(BytesIO(download.content)) as archive:
        archive_dir = f"signed_documents_{body['export_id'].rsplit('_', 1)[-1][:8]}"
        assert archive.namelist() == [
            f"{archive_dir}/single_archive.pdf"
        ]


def test_reset_removes_exported_files_from_runtime(tmp_path):
    client, app = make_client(tmp_path)
    job_id = upload_pdf(client, "temporary.pdf")
    save_placement(client, job_id)
    export_response = client.post("/export", json={"job_ids": [job_id]})
    assert export_response.status_code == 200
    export_id = export_response.json()["export_id"]
    export_path = app.state.jobs.get_export(export_id).path
    assert export_path.exists()

    reset_response = client.post("/reset")

    assert reset_response.status_code == 200
    assert not export_path.exists()
    assert app.state.jobs.get_export(export_id) is None


def test_export_does_not_modify_source_pdf(tmp_path):
    client, app = make_client(tmp_path)
    source_bytes = make_pdf_bytes(page_count=1, width=595, height=842)
    response = client.post(
        "/upload",
        files={"files": ("source.pdf", source_bytes, "application/pdf")},
    )
    assert response.status_code == 200
    job_id = response.json()["jobs"][0]["job_id"]
    save_placement(client, job_id)
    source_path = app.state.jobs.get(job_id).source_path
    before_hash = sha256(source_path.read_bytes()).hexdigest()

    response = client.post("/export", json={"job_ids": [job_id]})

    assert response.status_code == 200
    after_hash = sha256(source_path.read_bytes()).hexdigest()
    assert after_hash == before_hash


def test_export_multiple_pdfs_returns_zip(tmp_path):
    client, _app = make_client(tmp_path)
    first_job_id = upload_pdf(client, "first.pdf")
    second_job_id = upload_pdf(client, "second.pdf")
    save_placement(client, first_job_id)
    save_placement(client, second_job_id)

    response = client.post(
        "/export", json={"job_ids": [first_job_id, second_job_id]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "zip"
    assert len(body["files"]) == 2

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/zip")

    with ZipFile(BytesIO(download.content)) as archive:
        archive_dir = f"signed_documents_{body['export_id'].rsplit('_', 1)[-1][:8]}"
        assert sorted(archive.namelist()) == [
            f"{archive_dir}/first.pdf",
            f"{archive_dir}/second.pdf",
        ]
        for filename in archive.namelist():
            with fitz.open(stream=archive.read(filename), filetype="pdf") as document:
                assert document.page_count == 1


def test_export_preserves_pdfs_with_duplicate_filenames(tmp_path):
    client, _app = make_client(tmp_path)
    first_job_id = upload_pdf(client, "document.pdf")
    second_job_id = upload_pdf(client, "document.pdf")
    save_placement(client, first_job_id)
    save_placement(client, second_job_id)

    response = client.post(
        "/export",
        json={"job_ids": [first_job_id, second_job_id], "force_zip": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert [item["output_filename"] for item in body["files"]] == [
        "document.pdf",
        "document_2.pdf",
    ]

    download = client.get(body["download_url"])
    with ZipFile(BytesIO(download.content)) as archive:
        archive_dir = f"signed_documents_{body['export_id'].rsplit('_', 1)[-1][:8]}"
        assert archive.namelist() == [
            f"{archive_dir}/document.pdf",
            f"{archive_dir}/document_2.pdf",
        ]


def test_export_skips_missing_job_with_warning(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_pdf(client, "valid.pdf")
    save_placement(client, job_id)

    response = client.post("/export", json={"job_ids": [job_id, "job_missing"]})

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "pdf"
    assert body["warnings"] == ["job_missing: job not found"]
    assert len(body["files"]) == 1


def test_export_without_exportable_jobs_returns_422(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post("/export", json={"job_ids": ["job_missing"]})

    assert response.status_code == 422
    assert response.json()["detail"] == "job_missing: job not found"


def test_export_includes_unconfirmed_document_instead_of_silently_skipping_it(tmp_path):
    client, _app = make_client(tmp_path)
    job_id = upload_pdf(client, "unconfirmed.pdf")

    response = client.post("/export", json={"job_ids": [job_id]})

    assert response.status_code == 200
    assert response.json()["files"] == [
        {
            "job_id": job_id,
            "output_filename": "unconfirmed.pdf",
            "warnings": [],
        }
    ]


def test_export_all_includes_confirmed_and_unconfirmed_documents(tmp_path):
    client, _app = make_client(tmp_path)
    confirmed_job_id = upload_pdf(client, "confirmed.pdf")
    unconfirmed_job_id = upload_pdf(client, "unconfirmed.pdf")
    save_placement(client, confirmed_job_id)

    response = client.post(
        "/export",
        json={
            "job_ids": [confirmed_job_id, unconfirmed_job_id],
            "force_zip": True,
        },
    )

    assert response.status_code == 200
    body = response.json()
    download = client.get(body["download_url"])
    with ZipFile(BytesIO(download.content)) as archive:
        archive_dir = f"signed_documents_{body['export_id'].rsplit('_', 1)[-1][:8]}"
        assert archive.namelist() == [
            f"{archive_dir}/confirmed.pdf",
            f"{archive_dir}/unconfirmed.pdf",
        ]


def test_export_allows_unconfirmed_when_manual_confirmation_disabled(tmp_path):
    client, _app = make_client(tmp_path)
    response = client.post(
        "/upload",
        files={
            "files": (
                "unconfirmed_allowed.pdf",
                make_signature_pdf_bytes(
                    text="Венедиктов Р.В.",
                    line=(210, 720, 430, 720),
                ),
                "application/pdf",
            )
        },
    )
    assert response.status_code == 200
    job_id = response.json()["jobs"][0]["job_id"]
    analyze_response = client.post(
        "/analyze",
        json={
            "job_ids": [job_id],
            "options": {
                "place_signature": True,
                "place_stamp": True,
                "add_name_if_missing": True,
                "use_ai": False,
                "require_manual_confirmation": False,
            },
        },
    )
    assert analyze_response.status_code == 200

    response = client.post("/export", json={"job_ids": [job_id]})

    assert response.status_code == 200
    assert response.json()["type"] == "pdf"


def test_download_unknown_export_returns_404(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.get("/download/export_missing")

    assert response.status_code == 404
    assert response.json()["detail"] == "export not found"
