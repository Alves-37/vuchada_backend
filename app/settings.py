from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Raw URLs as provided by Railway
    database_public_url: str | None = Field(default=None, validation_alias="DATABASE_PUBLIC_URL")
    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    # Local fallback
    database_url_default: str = "postgresql+psycopg2://postgres:postgres@localhost:5432/neopdv"

    kiosk_pin: str | None = Field(default=None, validation_alias="KIOSK_PIN")

    @staticmethod
    def normalize_sqlalchemy_url(url: str) -> str:
        # Railway often provides `postgresql://...`.
        # SQLAlchemy wants an explicit driver when using psycopg2.
        if url.startswith("postgresql://"):
            return "postgresql+psycopg2://" + url.removeprefix("postgresql://")
        return url

    @property
    def sqlalchemy_database_url(self) -> str:
        raw = self.database_public_url or self.database_url or self.database_url_default
        return self.normalize_sqlalchemy_url(raw)


settings = Settings()
