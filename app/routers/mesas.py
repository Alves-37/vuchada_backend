from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_tenant_id
from app.db.database import get_db_session
from app.db.models import Mesa


router = APIRouter(prefix="/api/mesas", tags=["mesas"])


class MesaOut(BaseModel):
    id: int
    numero: int
    capacidade: int
    status: str
    mesa_token: str


async def _ensure_default_mesas(db: AsyncSession, tenant_id: uuid.UUID) -> None:
    res = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id).limit(1))
    if res.scalar_one_or_none():
        return

    for n in (1, 2, 3, 4):
        db.add(
            Mesa(
                tenant_id=tenant_id,
                numero=n,
                capacidade=4,
                status="Livre",
                mesa_token=f"mesa-{n}",
            )
        )
    await db.commit()


@router.get("/", response_model=list[MesaOut])
@router.get("", response_model=list[MesaOut])
async def listar_mesas(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    await _ensure_default_mesas(db, tenant_id)
    res = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id).order_by(Mesa.numero.asc()))
    rows = res.scalars().all()
    return [
        MesaOut(
            id=int(getattr(m, "id")),
            numero=int(getattr(m, "numero")),
            capacidade=int(getattr(m, "capacidade")),
            status=str(getattr(m, "status")),
            mesa_token=str(getattr(m, "mesa_token")),
        )
        for m in rows
    ]
