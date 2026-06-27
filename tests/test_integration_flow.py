from hashlib import sha256

import fitz
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.models import JobStatus
from tests.pdf_factory import make_signature_pdf_bytes
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


def test_full_upload_analyze_preview_placement_export_flow(tmp_path):
    client, app = make_client(tmp_path)
    source_bytes = make_signature_pdf_bytes(
        text="Генеральный директор",
        line=(230, 720, 430, 720),
    )
    source_hash = sha256(source_bytes).hexdigest()

    upload = client.post(
        "/upload",
        files={"files": ("flow.pdf", source_bytes, "application/pdf")},
    )
    assert upload.status_code == 200
    job_id = upload.json()["jobs"][0]["job_id"]

    analyze = client.post(
        "/analyze",
        json={
            "job_ids": [job_id],
            "options": {
                "place_signature": True,
                "place_stamp": True,
                "add_name_if_missing": True,
                "use_ai": False,
                "require_manual_confirmation": True,
            },
        },
    )
    assert analyze.status_code == 200
    assert analyze.json()["jobs"][0]["status"] == JobStatus.READY

    preview = client.get(f"/preview/{job_id}")
    assert preview.status_code == 200
    placement = preview.json()["placements"][0]
    assert placement["source"] == "auto"
    assert placement["signature"] is not None
    assert placement["stamp"] is not None
    assert placement["name"]["text"] == "Венедиктов Р.В."

    placement["signature"]["bbox"]["x0"] += 5
    placement["signature"]["bbox"]["x1"] += 5
    save = client.post(
        f"/placement/{job_id}",
        json={"placements": [placement], "confirmed_by_user": True},
    )
    assert save.status_code == 200
    assert save.json()["confirmed_by_user"] is True

    export = client.post("/export", json={"job_ids": [job_id]})
    assert export.status_code == 200
    assert export.json()["download_url"].startswith("/download/")
    download = client.get(export.json()["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF")

    with fitz.open(stream=download.content, filetype="pdf") as document:
        assert document.page_count == 1
        page = document[0]
        assert "Венедиктов" in page.get_text()
        assert len(page.get_images(full=True)) >= 2

    job = app.state.jobs.get(job_id)
    assert sha256(job.source_path.read_bytes()).hexdigest() == source_hash
