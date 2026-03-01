from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin_user, get_current_user, get_tenant_id
from app.db.database import get_db_session
from app.db.models import Mesa


router = APIRouter(prefix="/api/mesas", tags=["mesas"])


class MesaOut(BaseModel):
    id: uuid.UUID
    numero: int
    capacidade: int
    status: str
    mesa_token: str


class MesaCreate(BaseModel):
    numero: int
    capacidade: int = 4


class MesaUpdate(BaseModel):
    numero: int | None = None
    capacidade: int | None = None
    status: str | None = None


class MesaStatusUpdate(BaseModel):
    status: str


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
            id=getattr(m, "id"),
            numero=int(getattr(m, "numero")),
            capacidade=int(getattr(m, "capacidade")),
            status=str(getattr(m, "status")),
            mesa_token=str(getattr(m, "mesa_token")),
        )
        for m in rows
    ]


@router.post("/", response_model=MesaOut)
async def criar_mesa(
    payload: MesaCreate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    numero = int(payload.numero)
    if numero <= 0:
        raise HTTPException(status_code=400, detail="numero inválido")
    capacidade = int(payload.capacidade or 0)
    if capacidade <= 0:
        raise HTTPException(status_code=400, detail="capacidade inválida")

    res_dup = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.numero == numero))
    if res_dup.scalars().first():
        raise HTTPException(status_code=400, detail="Mesa já existe")

    m = Mesa(
        tenant_id=tenant_id,
        numero=numero,
        capacidade=capacidade,
        status="Livre",
        mesa_token=f"mesa-{numero}",
    )
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return MesaOut(
        id=getattr(m, "id"),
        numero=int(getattr(m, "numero")),
        capacidade=int(getattr(m, "capacidade")),
        status=str(getattr(m, "status")),
        mesa_token=str(getattr(m, "mesa_token")),
    )


@router.put("/{mesa_id}/status", response_model=MesaOut)
async def atualizar_status_mesa(
    mesa_id: uuid.UUID,
    payload: MesaStatusUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_user),
):
    res = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.id == mesa_id))
    m = res.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    st = str(payload.status or "").strip()
    if not st:
        raise HTTPException(status_code=400, detail="status inválido")

    # Permitir apenas alguns status padrão (evita bagunça no PDV)
    st_norm = st.lower()
    allowed = {"livre": "Livre", "ocupado": "Ocupado", "reservado": "Reservado"}
    if st_norm not in allowed:
        raise HTTPException(status_code=400, detail="status inválido")
    m.status = allowed[st_norm]

    await db.commit()
    await db.refresh(m)
    return MesaOut(
        id=getattr(m, "id"),
        numero=int(getattr(m, "numero")),
        capacidade=int(getattr(m, "capacidade")),
        status=str(getattr(m, "status")),
        mesa_token=str(getattr(m, "mesa_token")),
    )


@router.put("/{mesa_id}", response_model=MesaOut)
async def atualizar_mesa(
    mesa_id: uuid.UUID,
    payload: MesaUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.id == mesa_id))
    m = res.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    if payload.numero is not None:
        numero = int(payload.numero)
        if numero <= 0:
            raise HTTPException(status_code=400, detail="numero inválido")
        res_dup = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.numero == numero, Mesa.id != m.id))
        if res_dup.scalars().first():
            raise HTTPException(status_code=400, detail="Mesa já existe")
        m.numero = numero
        m.mesa_token = f"mesa-{numero}"

    if payload.capacidade is not None:
        capacidade = int(payload.capacidade)
        if capacidade <= 0:
            raise HTTPException(status_code=400, detail="capacidade inválida")
        m.capacidade = capacidade

    if payload.status is not None:
        st = str(payload.status or "").strip()
        if not st:
            raise HTTPException(status_code=400, detail="status inválido")
        m.status = st

    await db.commit()
    await db.refresh(m)
    return MesaOut(
        id=getattr(m, "id"),
        numero=int(getattr(m, "numero")),
        capacidade=int(getattr(m, "capacidade")),
        status=str(getattr(m, "status")),
        mesa_token=str(getattr(m, "mesa_token")),
    )


@router.delete("/{mesa_id}")
async def apagar_mesa(
    mesa_id: uuid.UUID,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res = await db.execute(select(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.id == mesa_id))
    m = res.scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    await db.execute(delete(Mesa).where(Mesa.tenant_id == tenant_id, Mesa.id == mesa_id))
    await db.commit()
    return {"ok": True}
