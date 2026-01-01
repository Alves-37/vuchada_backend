import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

import psycopg2


def _require_confirmation(phrase: str, expected: str):
    if phrase != expected:
        raise SystemExit("Confirmação inválida. Reset abortado.")


def _connect(database_url: str):
    u = urlparse(database_url)
    if u.scheme not in ("postgres", "postgresql"):
        raise SystemExit(f"DATABASE_URL não parece Postgres: scheme={u.scheme!r}")

    dbname = (u.path or "").lstrip("/")
    if not dbname:
        raise SystemExit("DATABASE_URL inválida: dbname ausente")

    return psycopg2.connect(
        dbname=dbname,
        user=u.username,
        password=u.password,
        host=u.hostname,
        port=u.port or 5432,
        sslmode=os.environ.get("PGSSLMODE", "require"),
    )


def _list_tables(cur, schema: str):
    cur.execute(
        """
        SELECT tablename
        FROM pg_catalog.pg_tables
        WHERE schemaname = %s
        ORDER BY tablename
        """,
        (schema,),
    )
    return [r[0] for r in cur.fetchall()]


def _load_dotenv_if_present():
    try:
        root = Path(__file__).resolve().parents[1]
        env_path = root / ".env"
        if not env_path.exists():
            return

        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and (k not in os.environ):
                os.environ[k] = v
    except Exception:
        return


def main():
    parser = argparse.ArgumentParser(description="RESET TOTAL do banco ONLINE (Postgres).")
    parser.add_argument(
        "--database-url",
        default="",
        help="URL do Postgres (se vazio, usa DATABASE_URL do ambiente)",
    )
    parser.add_argument(
        "--schema",
        default=os.environ.get("PGSCHEMA", "public"),
        help="Schema a resetar (default: public)",
    )
    parser.add_argument(
        "--i-know-what-i-am-doing",
        action="store_true",
        help="Obrigatório para permitir o reset.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help="Digite exatamente: RESETAR_TUDO",
    )
    args = parser.parse_args()

    if not args.i_know_what_i_am_doing:
        raise SystemExit("Passe --i-know-what-i-am-doing para permitir o reset.")

    _require_confirmation(args.confirm, "RESETAR_TUDO")

    _load_dotenv_if_present()

    database_url = (args.database_url or "").strip()
    if not database_url:
        database_url = os.environ.get("DATABASE_URL", "").strip() or os.environ.get("DATABASE_PUBLIC_URL", "").strip()
    if not database_url:
        raise SystemExit("DATABASE_URL não está definido no ambiente.")

    conn = _connect(database_url)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            tables = _list_tables(cur, args.schema)

            # Evitar apagar tabelas de sistema/migração, se existirem
            exclude = {"alembic_version"}
            tables = [t for t in tables if t not in exclude]

            if not tables:
                print("Nenhuma tabela encontrada para reset.")
                return

            fq = ", ".join([f'"{args.schema}"."{t}"' for t in tables])
            print(f"Tabelas a truncar ({len(tables)}):")
            for t in tables:
                print(f"- {args.schema}.{t}")

            cur.execute(f"TRUNCATE {fq} RESTART IDENTITY CASCADE;")

        conn.commit()
        print("\nRESET TOTAL concluído com sucesso.")
    except Exception as e:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERRO: {e}", file=sys.stderr)
        raise
