import os
from datetime import datetime, time

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.db import Base, engine
from app.routers import mesas, pedidos, produtos, public, sync

app = FastAPI(title="NEOPDV-VUCHADA Backend", version="0.1.0")


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
_UPLOADS_DIR = os.path.join(_STATIC_DIR, "uploads")
os.makedirs(_UPLOADS_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)

    # Ensure QR tokens exist for existing mesas without risking data loss.
    # We do it via SQL because create_all() does not apply schema migrations.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE mesas ADD COLUMN IF NOT EXISTS mesa_token VARCHAR(64)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_mesas_mesa_token ON mesas (mesa_token)"))
        conn.execute(
            text(
                """
                UPDATE mesas
                SET mesa_token = md5(random()::text || clock_timestamp()::text)
                WHERE mesa_token IS NULL OR mesa_token = ''
                """
            )
        )

        conn.execute(text("ALTER TABLE pedidos ADD COLUMN IF NOT EXISTS uuid VARCHAR(64)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_pedidos_uuid ON pedidos (uuid)"))
        conn.execute(
            text(
                """
                UPDATE pedidos
                SET uuid = md5(random()::text || clock_timestamp()::text)
                WHERE uuid IS NULL OR uuid = ''
                """
            )
        )

        conn.execute(text("ALTER TABLE itens_pedido ADD COLUMN IF NOT EXISTS uuid VARCHAR(64)"))
        conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_itens_pedido_uuid ON itens_pedido (uuid)"))
        conn.execute(
            text(
                """
                UPDATE itens_pedido
                SET uuid = md5(random()::text || clock_timestamp()::text)
                WHERE uuid IS NULL OR uuid = ''
                """
            )
        )


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/api/restaurant-status")
def restaurant_status():
    # Horário local: aberto 07:00 e fecha 24:00 (meia-noite).
    # (Ou seja, aberto a partir de 07:00 inclusive até 23:59.)
    now = datetime.now()
    t = now.time()
    open_at = time(7, 0)
    close_at = time(0, 0)

    # Range que atravessa meia-noite: (07:00 -> 00:00)
    is_open = (t >= open_at) or (t < close_at)

    return {
        "is_open": bool(is_open),
        "server_time": now.strftime("%H:%M:%S"),
        "open_at": "07:00",
        "close_at": "00:00",
    }


app.include_router(produtos.router)
app.include_router(mesas.router)
app.include_router(pedidos.router)
app.include_router(public.router)
app.include_router(sync.router)
