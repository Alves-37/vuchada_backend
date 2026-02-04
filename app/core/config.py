from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    # URLs do PostgreSQL (Railway fornece DATABASE_URL internamente). Em ambiente local, use DATABASE_PUBLIC_URL.
    # Não manter credenciais hardcoded no repositório.
    DATABASE_URL: str | None = None
    DATABASE_PUBLIC_URL: str | None = None
    JWT_SECRET: str = "a_very_secret_key_that_should_be_changed"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    
    # Railway environment detection
    ENVIRONMENT: str = "development"
    PORT: int = 8000

    model_config = SettingsConfigDict(env_file=".env")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Auto-detect Railway environment
        if os.getenv("RAILWAY_ENVIRONMENT"):
            self.ENVIRONMENT = "production"
            self.PORT = int(os.getenv("PORT", 8000))

        # Escolha da URL do banco:
        # - Em produção (Railway): usar DATABASE_URL.
        # - Local/dev: usar DATABASE_PUBLIC_URL (para conectar externamente).
        db_url = None
        if self.ENVIRONMENT == "production":
            db_url = os.getenv("DATABASE_URL") or self.DATABASE_URL
        else:
            db_url = os.getenv("DATABASE_PUBLIC_URL") or self.DATABASE_PUBLIC_URL or os.getenv("DATABASE_URL") or self.DATABASE_URL

        if not db_url:
            raise ValueError("DATABASE_URL/DATABASE_PUBLIC_URL não configurado. Defina no .env (local) ou nas variáveis do Railway.")

        # Ensure SQLAlchemy async driver
        if db_url.startswith("postgresql+asyncpg://"):
            self.DATABASE_URL = db_url
        elif db_url.startswith("postgresql://"):
            self.DATABASE_URL = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
        elif db_url.startswith("postgres://"):
            # Alguns providers (incluindo Railway) usam este alias
            self.DATABASE_URL = db_url.replace("postgres://", "postgresql+asyncpg://", 1)
        else:
            self.DATABASE_URL = db_url

settings = Settings()
