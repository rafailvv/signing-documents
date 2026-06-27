from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_frontend_contains_mobile_ux_structure(tmp_path):
    client = TestClient(create_app(Settings(WORKDIR=tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "auth-screen" in html
    assert "login-tab" in html
    assert "register-tab" in html
    assert "auth-email" in html
    assert "auth-password-repeat" in html
    assert "auth-legal-consents" in html
    assert "accept-offer" in html
    assert "accept-ai-analysis" in html
    assert "/legal/public-offer.pdf" in html
    assert "Правовые документы" in html
    assert "Без кода и лишних шагов" not in html
    assert "upload-signature" in html
    assert "upload-stamp" in html
    assert "logout" in html
    assert "@media (max-width: 760px)" in html
    assert ".finish-actions" in html
    assert "fullscreen-editor" in html
    assert "fullscreen-mode" in html
    assert "Открыть PDF на весь экран" in html
    assert ".editor-tools" in html
    assert "position: fixed" in html
    assert "overflow-x: auto" in html
    assert "scroll-snap-type: x proximity" in html
    assert "Выберите на странице" in html


def test_legal_documents_are_served(tmp_path):
    client = TestClient(create_app(Settings(WORKDIR=tmp_path)))

    listing = client.get("/legal")
    assert listing.status_code == 200
    assert any(item["filename"] == "public-offer.pdf" for item in listing.json())

    response = client.get("/legal/public-offer.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
