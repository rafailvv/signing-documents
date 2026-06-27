from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_healthcheck_returns_ok(tmp_path):
    app = create_app(Settings(WORKDIR=tmp_path, OPENAI_API_KEY=None))
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai-pdf-signing"
    assert body["ai_configured"] is False
