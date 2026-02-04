from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.core.config import settings

# Create engine with error handling
try:
    db_url = str(settings.DATABASE_URL)
    if db_url.startswith("postgresql+asyncpg://"):
        pass
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        db_url,
        pool_pre_ping=True,           # Detect stale connections
        pool_recycle=900,             # Recycle connections every 15 minutes
        pool_timeout=30,              # Wait up to 30s for a connection
        echo=False,                   # Set to True for SQL debugging
        pool_size=5,                  # Tune according to Railway plan
        max_overflow=5                # Allow short bursts
    )
    AsyncSessionLocal = async_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        expire_on_commit=False,
    )
except Exception as e:
    print(f"Database connection error: {e}")
    print(f"DATABASE_URL: {settings.DATABASE_URL}")
    raise

# Alias para compatibilidade
async_session = AsyncSessionLocal
