from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_frontend_contains_mobile_ux_structure(tmp_path):
    client = TestClient(create_app(Settings(WORKDIR=tmp_path)))

    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "boot-screen" in html
    assert "Открываем сервис" in html
    assert 'id="auth-screen" class="auth-screen hidden"' in html
    assert "auth-screen" in html
    assert "login-tab" in html
    assert "register-tab" in html
    assert "auth-email" in html
    assert "auth-password-repeat" in html
    assert "auth-legal-consents" in html
    assert "accept-offer" in html
    assert "accept-data-processing" in html
    assert 'id="accept-privacy"' not in html
    assert 'id="accept-personal-data"' not in html
    assert 'id="accept-ai-analysis"' not in html
    assert 'id="accept-usage-rules"' not in html
    assert 'id="accept-marketing"' not in html
    assert "/legal/public-offer.pdf" in html
    assert "/legal/privacy-policy.pdf" in html
    assert "/legal/personal-data-consent.pdf" in html
    assert "/legal/ai-analysis-consent.pdf" in html
    assert "Правовые документы" in html
    assert "Без кода и лишних шагов" not in html
    assert "upload-signature" in html
    assert "upload-stamp" in html
    assert "logout" in html
    assert "mobile-file-actions" in html
    assert "mobile-download-all" in html
    assert "mobile-choose-files" in html
    assert "mobile-documents-toggle" in html
    assert "documents-panel" in html
    assert "documents-collapsed" in html
    assert "Скачать все PDF" in html
    assert "@media (max-width: 760px)" in html
    assert ".finish-actions" in html
    assert "fullscreen-editor" in html
    assert "fullscreen-mode" in html
    assert "editor-fullscreen-active" in html
    assert ".app-shell.editor-fullscreen-active .workspace" in html
    assert "Открыть PDF на весь экран" in html
    assert ".editor-tools" in html
    assert "position: fixed" in html
    assert "overflow-x: auto" in html
    assert "scroll-snap-type: x proximity" in html
    assert "Выберите на странице" in html
    assert "startLazyPreviewLoading" in html
    assert "IntersectionObserver" in html
    assert "preview-pending" in html
    assert "max-height: min(620px, calc(100dvh - 138px))" in html
    assert "overscroll-behavior: contain" in html
    assert "scrollbar-width: thin" in html
    assert "uploadBatchMaxBytes" in html
    assert "makeUploadBatches" in html
    assert "uploadFileBatch" in html
    assert "JSON.stringify({ [key.ids]: [id], options: options() })" in html


def test_legal_documents_are_served(tmp_path):
    client = TestClient(create_app(Settings(WORKDIR=tmp_path)))

    listing = client.get("/legal")
    assert listing.status_code == 200
    assert any(item["filename"] == "public-offer.pdf" for item in listing.json())

    response = client.get("/legal/public-offer.pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")
