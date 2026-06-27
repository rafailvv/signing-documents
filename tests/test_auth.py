from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.pdf_factory import make_pdf_bytes
from tests.test_placements import PNG_BYTES, placement_payload


def make_client(tmp_path):
    signature_path = tmp_path / "default_signature.png"
    stamp_path = tmp_path / "default_stamp.png"
    signature_path.write_bytes(PNG_BYTES)
    stamp_path.write_bytes(PNG_BYTES)
    app = create_app(
        Settings(
            WORKDIR=tmp_path / "runtime",
            AUTH_REQUIRED=True,
            DATABASE_URL=f"sqlite:///{tmp_path / 'auth.db'}",
            SECRET_KEY="test-secret",
            SIGNATURE_IMAGE_PATH=signature_path,
            STAMP_IMAGE_PATH=stamp_path,
        )
    )
    return TestClient(app), app


def legal_consents() -> dict[str, bool]:
    return {
        "accept_offer": True,
        "accept_data_processing": True,
    }


def register(client: TestClient, login: str) -> dict:
    response = client.post(
        "/auth/register",
        json={
            "email": f"{login}@example.com",
            "login": login,
            "password": "secret123",
            "password_repeat": "secret123",
            **legal_consents(),
        },
    )
    assert response.status_code == 200
    return response.json()


def headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def upload_pdf(client: TestClient, token: str) -> str:
    response = client.post(
        "/upload",
        files={"files": ("document.pdf", make_pdf_bytes(), "application/pdf")},
        headers=headers(token),
    )
    assert response.status_code == 200
    return response.json()["jobs"][0]["job_id"]


def test_register_login_and_me(tmp_path):
    client, _app = make_client(tmp_path)

    registered = register(client, "rafail")
    assert registered["access_token"]
    assert registered["user"]["login"] == "rafail"
    assert registered["user"]["email"] == "rafail@example.com"

    login = client.post("/auth/login", json={"login": "rafail", "password": "secret123"})
    assert login.status_code == 200
    me = client.get("/auth/me", headers=headers(login.json()["access_token"]))
    assert me.status_code == 200
    assert me.json()["login"] == "rafail"

    email_login = client.post("/auth/login", json={"login": "rafail@example.com", "password": "secret123"})
    assert email_login.status_code == 200
    email_me = client.get("/auth/me", headers=headers(email_login.json()["access_token"]))
    assert email_me.status_code == 200
    assert email_me.json()["email"] == "rafail@example.com"


def test_duplicate_register_and_bad_password(tmp_path):
    client, _app = make_client(tmp_path)
    register(client, "user")

    duplicate = client.post(
        "/auth/register",
        json={
            "email": "another@example.com",
            "login": "user",
            "password": "secret123",
            "password_repeat": "secret123",
            **legal_consents(),
        },
    )
    assert duplicate.status_code == 409

    duplicate_email = client.post(
        "/auth/register",
        json={
            "email": "user@example.com",
            "login": "user2",
            "password": "secret123",
            "password_repeat": "secret123",
            **legal_consents(),
        },
    )
    assert duplicate_email.status_code == 409

    password_mismatch = client.post(
        "/auth/register",
        json={
            "email": "user3@example.com",
            "login": "user3",
            "password": "secret123",
            "password_repeat": "secret124",
            **legal_consents(),
        },
    )
    assert password_mismatch.status_code == 422

    bad_password = client.post("/auth/login", json={"login": "user", "password": "wrong123"})
    assert bad_password.status_code == 401


def test_register_requires_legal_consents(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post(
        "/auth/register",
        json={
            "email": "consent@example.com",
            "login": "consent",
            "password": "secret123",
            "password_repeat": "secret123",
        },
    )

    assert response.status_code == 422


def test_protected_endpoints_require_token(tmp_path):
    client, _app = make_client(tmp_path)

    assert client.get("/jobs").status_code == 401
    assert client.post("/reset").status_code == 401
    assert client.post("/upload", files={"files": ("a.pdf", make_pdf_bytes(), "application/pdf")}).status_code == 401
    assert client.get("/assets/signature").status_code == 401
    assert client.get("/assets/stamp").status_code == 401


def test_security_headers_are_set(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"


def test_auth_rate_limit_blocks_repeated_login_attempts(tmp_path):
    client, _app = make_client(tmp_path)
    register(client, "limited")

    statuses = [
        client.post("/auth/login", json={"login": "limited", "password": "wrong123"}).status_code
        for _ in range(9)
    ]

    assert statuses[:8] == [401] * 8
    assert statuses[8] == 429


def test_preview_images_require_token_and_job_owner(tmp_path):
    client, _app = make_client(tmp_path)
    first = register(client, "preview-owner")["access_token"]
    second = register(client, "preview-other")["access_token"]
    job_id = upload_pdf(client, first)
    preview = client.get(f"/preview/{job_id}", headers=headers(first))
    image_url = preview.json()["pages"][0]["image_url"]

    assert client.get(image_url).status_code == 401
    assert client.get(image_url, headers=headers(second)).status_code == 404
    image = client.get(image_url, headers=headers(first))
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content.startswith(b"\x89PNG")


def test_users_do_not_see_each_other_jobs(tmp_path):
    client, _app = make_client(tmp_path)
    first = register(client, "first")["access_token"]
    second = register(client, "second")["access_token"]
    upload_pdf(client, first)

    first_jobs = client.get("/jobs", headers=headers(first))
    second_jobs = client.get("/jobs", headers=headers(second))

    assert len(first_jobs.json()["jobs"]) == 1
    assert second_jobs.json() == {"jobs": []}


def test_png_assets_are_uploaded_and_reported_in_me(tmp_path):
    client, _app = make_client(tmp_path)
    token = register(client, "assets")["access_token"]

    signature = client.post(
        "/assets/me/signature",
        files={"file": ("signature.png", PNG_BYTES, "image/png")},
        headers=headers(token),
    )
    stamp = client.post(
        "/assets/me/stamp",
        files={"file": ("stamp.png", PNG_BYTES, "image/png")},
        headers=headers(token),
    )
    bad = client.post(
        "/assets/me/stamp",
        files={"file": ("stamp.txt", b"not png", "text/plain")},
        headers=headers(token),
    )

    assert signature.status_code == 200
    assert stamp.status_code == 200
    assert stamp.json()["has_signature"] is True
    assert stamp.json()["has_stamp"] is True
    assert bad.status_code == 422
    assert client.get("/assets/me/signature", headers=headers(token)).content == PNG_BYTES


def test_png_asset_size_is_limited(tmp_path):
    client, _app = make_client(tmp_path)
    token = register(client, "big-asset")["access_token"]

    response = client.post(
        "/assets/me/signature",
        files={"file": ("signature.png", b"\x89PNG\r\n\x1a\n" + b"0" * (5 * 1024 * 1024 + 1), "image/png")},
        headers=headers(token),
    )

    assert response.status_code == 413


def test_export_receives_user_assets(tmp_path, monkeypatch):
    client, _app = make_client(tmp_path)
    token = register(client, "exporter")["access_token"]
    client.post(
        "/assets/me/signature",
        files={"file": ("signature.png", PNG_BYTES, "image/png")},
        headers=headers(token),
    )
    client.post(
        "/assets/me/stamp",
        files={"file": ("stamp.png", PNG_BYTES, "image/png")},
        headers=headers(token),
    )
    job_id = upload_pdf(client, token)
    placement = client.post(
        f"/placement/{job_id}",
        json=placement_payload(),
        headers=headers(token),
    )
    assert placement.status_code == 200

    captured = {}

    def fake_export_jobs(**kwargs):
        captured.update(kwargs)
        from app.models import ExportResult, ExportType, ExportedFile

        output = tmp_path / "fake.pdf"
        output.write_bytes(b"%PDF-1.4\n")
        return ExportResult(
            export_id="export_test",
            user_id=kwargs["user_id"],
            type=ExportType.PDF,
            path=output,
            files=[ExportedFile(job_id=job_id, output_filename="document_signed.pdf")],
        )

    monkeypatch.setattr("app.main.export_jobs", fake_export_jobs)

    response = client.post("/export", json={"job_ids": [job_id]}, headers=headers(token))

    assert response.status_code == 200
    assert captured["signature_png"] == PNG_BYTES
    assert captured["stamp_png"] == PNG_BYTES
