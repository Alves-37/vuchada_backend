#!/usr/bin/env python3
"""Run DB migration to create pdv.payment_transactions table.

- Reads and executes scripts/add_payment_transactions_table.sql against DATABASE_URL
- Uses SQLAlchemy async engine (asyncpg)

Usage:
  python scripts/run_migration_payment_transactions.py

Requirements:
  DATABASE_URL env var must be set (Railway provides it). If running locally,
  set DATABASE_URL to your Railway Postgres connection string.
"""

import asyncio
import os
import sys
from pathlib import Path
import argparse

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Ensure project root is on sys.path so 'app' package can be imported when run from scripts/
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from app.core.config import settings  # type: ignore

    SETTINGS_OK = True
except Exception:
    settings = None
    SETTINGS_OK = False

SQL_FILE = Path(__file__).with_name("add_payment_transactions_table.sql")


async def run() -> None:
    if not SQL_FILE.exists():
        raise FileNotFoundError(f"Arquivo SQL não encontrado: {SQL_FILE}")

    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--database-url", "--url", dest="database_url", default="", help="Postgres DATABASE_URL")
    args, _ = parser.parse_known_args()

    db_url = (args.database_url or "").strip()
    if not db_url:
        if SETTINGS_OK and getattr(settings, "DATABASE_URL", None):
            db_url = str(settings.DATABASE_URL)
        else:
            db_url = os.getenv("DATABASE_URL", "")

    if not db_url:
        try:
            db_url = input(
                "Cole o DATABASE_URL do Postgres (Railway) e pressione Enter: "
            ).strip()
        except Exception:
            db_url = ""

    if not db_url:
        raise RuntimeError(
            "DATABASE_URL não definido. Use env DATABASE_URL ou rode: "
            "python scripts/run_migration_payment_transactions.py --url <URL>"
        )

    if not db_url.startswith("postgresql+asyncpg://"):
        if db_url.startswith("postgresql://"):
            db_url = db_url.replace("postgresql://", "postgresql+asyncpg://")

    print("\n=== MIGRATION: create pdv.payment_transactions ===")
    print(f"SQL file: {SQL_FILE}")

    engine = create_async_engine(db_url, echo=False, pool_pre_ping=True)
    try:
        sql = SQL_FILE.read_text(encoding="utf-8")
        statements = [s.strip() for s in sql.split(";") if s.strip()]

        async with engine.begin() as conn:
            for stmt in statements:
                print(f"-> Executando: {stmt[:90]}...")
                await conn.execute(text(stmt))

        print("OK: Migração concluída com sucesso.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
