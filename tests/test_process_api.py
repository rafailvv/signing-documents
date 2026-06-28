from base64 import urlsafe_b64decode
from json import loads

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
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


def pdf_file(name: str = "api.pdf"):
    return (
        name,
        make_signature_pdf_bytes(
            text="Генеральный директор",
            line=(230, 720, 430, 720),
        ),
        "application/pdf",
    )


def test_process_api_returns_download_url_and_report(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post(
        "/api/process",
        files={"files": pdf_file("one.pdf")},
        data={"use_ai": "false"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["type"] == "pdf"
    assert body["download_url"].startswith("/download/")
    assert body["jobs"][0]["filename"] == "one.pdf"
    assert body["jobs"][0]["signed"] is True
    assert body["jobs"][0]["stamped"] is True
    assert body["jobs"][0]["name_added"] is True
    assert body["jobs"][0]["placements_count"] >= 1

    download = client.get(body["download_url"])
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/pdf")
    assert download.content.startswith(b"%PDF")


def test_process_file_api_returns_pdf_and_encoded_report(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post(
        "/api/process-file",
        files={"files": pdf_file("direct.pdf")},
        data={"use_ai": "false"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content.startswith(b"%PDF")
    report = loads(urlsafe_b64decode(response.headers["x-signing-report"]).decode("utf-8"))
    assert report["type"] == "pdf"
    assert report["jobs"][0]["filename"] == "direct.pdf"
    assert report["jobs"][0]["signed"] is True


def test_process_api_returns_zip_for_multiple_pdfs(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.post(
        "/api/process",
        files=[
            ("files", pdf_file("first.pdf")),
            ("files", pdf_file("second.pdf")),
        ],
        data={"use_ai": "false"},
    )

    assert response.status_code == 200
    assert response.json()["type"] == "zip"
    assert len(response.json()["files"]) == 2


def test_openapi_documents_integration_api(tmp_path):
    client, _app = make_client(tmp_path)

    openapi = client.get("/openapi.json").json()

    assert "Integration API" in {tag["name"] for tag in openapi["tags"]}
    assert "/api/process" in openapi["paths"]
    assert "/api/process-file" in openapi["paths"]
    assert "/analyze" in openapi["paths"]
    assert openapi["paths"]["/api/process"]["post"]["summary"] == "Загрузить PDF, обработать и получить ссылку на результат"
    for path in ["/api/process", "/api/process-file"]:
        process_schema_ref = openapi["paths"][path]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]["$ref"]
        process_schema_name = process_schema_ref.rsplit("/", 1)[-1]
        process_schema = openapi["components"]["schemas"][process_schema_name]
        assert "use_ai" in process_schema["properties"]
    analyze_schema_ref = openapi["paths"]["/analyze"]["post"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    analyze_schema_name = analyze_schema_ref.rsplit("/", 1)[-1]
    analyze_schema = openapi["components"]["schemas"][analyze_schema_name]
    options_schema_ref = analyze_schema["properties"]["options"]["$ref"]
    options_schema_name = options_schema_ref.rsplit("/", 1)[-1]
    options_schema = openapi["components"]["schemas"][options_schema_name]
    assert "use_ai" in options_schema["properties"]


def test_swagger_docs_load_assets_with_compatible_csp(tmp_path):
    client, _app = make_client(tmp_path)

    response = client.get("/docs")

    assert response.status_code == 200
    assert "SwaggerUIBundle" in response.text
    assert "/favicon.png" in response.text
    assert "https://cdn.jsdelivr.net" in response.headers["content-security-policy"]
