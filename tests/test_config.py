from app.config import Settings


def test_settings_defaults_do_not_require_openai_key(tmp_path):
    settings = Settings(WORKDIR=tmp_path, OPENAI_API_KEY=None)

    assert settings.openai_base_url == "https://api.openai.com/v1"
    assert settings.openai_api_key is None
    assert settings.ai_enabled_by_config is False
    assert settings.workdir == tmp_path
