from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment and optional .env file."""

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: str = Field(
        default="https://api.openai.com/v1", alias="OPENAI_BASE_URL"
    )
    openai_model: str | None = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    signature_image_path: Path = Field(
        default=Path("/Users/rafailvv/Documents/подпись.png"),
        alias="SIGNATURE_IMAGE_PATH",
    )
    stamp_image_path: Path = Field(
        default=Path("/Users/rafailvv/Documents/Печать.png"),
        alias="STAMP_IMAGE_PATH",
    )
    ocr_languages: str = Field(default="rus+eng", alias="OCR_LANGUAGES")
    openai_timeout_seconds: float = Field(default=45, alias="OPENAI_TIMEOUT_SECONDS")
    workdir: Path = Field(default=Path(".runtime"), alias="WORKDIR")
    app_host: str = Field(default="127.0.0.1", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")
    auth_required: bool = Field(default=False, alias="AUTH_REQUIRED")
    secret_key: str = Field(default="change-this-in-prod", alias="SECRET_KEY")
    access_token_expire_minutes: int = Field(default=60 * 24 * 7, alias="ACCESS_TOKEN_EXPIRE_MINUTES")
    postgres_db: str = Field(default="signing_documents", alias="POSTGRES_DB")
    postgres_user: str = Field(default="signing_documents", alias="POSTGRES_USER")
    postgres_password: str = Field(default="signing_documents", alias="POSTGRES_PASSWORD")
    postgres_host: str = Field(default="localhost", alias="POSTGRES_HOST")
    postgres_port: int = Field(default=5432, alias="POSTGRES_PORT")
    database_url_override: str | None = Field(default=None, alias="DATABASE_URL")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @property
    def ai_enabled_by_config(self) -> bool:
        return bool(self.openai_api_key and self.openai_model)

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        if not self.auth_required:
            return f"sqlite:///{self.workdir / 'app.db'}"
        user = quote_plus(self.postgres_user)
        password = quote_plus(self.postgres_password)
        return f"postgresql+psycopg2://{user}:{password}@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
