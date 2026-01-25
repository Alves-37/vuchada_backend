from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from app.core.deps import get_current_admin_user
from app.db.database import get_db_session
from app.db.models import Tenant


router = APIRouter(prefix="/api/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    nome: str
    ativo: bool = True
    id: Optional[str] = None
    tipo_negocio: str = "mercearia"
    slug: Optional[str] = None


class TenantResponse(BaseModel):
    id: str
    nome: str
    slug: Optional[str] = None
    ativo: bool
    tipo_negocio: str
    is_system: bool = False

    class Config:
        from_attributes = True


class TenantUpdate(BaseModel):
    nome: Optional[str] = None
    ativo: Optional[bool] = None
    tipo_negocio: Optional[str] = None
    slug: Optional[str] = None


@router.get("/", response_model=List[TenantResponse])
async def list_tenants(
    incluir_system: bool = False,
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(Tenant).order_by(Tenant.created_at)
    if not incluir_system:
        stmt = stmt.where(Tenant.is_system == False)
    result = await db.execute(stmt)
    tenants = result.scalars().all()
    return [
        TenantResponse(
            id=str(t.id),
            nome=t.nome,
            slug=getattr(t, "slug", None),
            ativo=bool(t.ativo),
            tipo_negocio=getattr(t, "tipo_negocio", "mercearia") or "mercearia",
            is_system=bool(getattr(t, "is_system", False)),
        )
        for t in tenants
    ]


@router.post("/", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: TenantCreate,
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    try:
        tenant_id = uuid.UUID(payload.id) if payload.id else uuid.uuid4()
    except Exception:
        raise HTTPException(status_code=400, detail="id inválido (UUID esperado)")

    existing = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tenant com este id já existe")

    tenant = Tenant(
        id=tenant_id,
        nome=payload.nome,
        ativo=payload.ativo,
        tipo_negocio=payload.tipo_negocio,
        slug=(payload.slug.strip().lower() if isinstance(payload.slug, str) and payload.slug.strip() else None),
    )
    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return TenantResponse(
        id=str(tenant.id),
        nome=tenant.nome,
        slug=getattr(tenant, "slug", None),
        ativo=bool(tenant.ativo),
        tipo_negocio=getattr(tenant, "tipo_negocio", "mercearia") or "mercearia",
        is_system=bool(getattr(tenant, "is_system", False)),
    )


@router.put("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(
    tenant_id: str,
    payload: TenantUpdate,
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    try:
        tid = uuid.UUID(tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="tenant_id inválido (UUID esperado)")

    result = await db.execute(select(Tenant).where(Tenant.id == tid))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    if payload.nome is not None:
        tenant.nome = payload.nome

    if payload.ativo is not None:
        if payload.ativo is False and bool(tenant.ativo) is True:
            active_count = await db.scalar(select(func.count()).select_from(Tenant).where(Tenant.ativo == True))
            if (active_count or 0) <= 1:
                raise HTTPException(status_code=400, detail="Não é possível desativar o último tenant ativo")
        tenant.ativo = payload.ativo

    if payload.tipo_negocio is not None:
        tenant.tipo_negocio = payload.tipo_negocio

    if payload.slug is not None:
        tenant.slug = payload.slug.strip().lower() if isinstance(payload.slug, str) and payload.slug.strip() else None

    db.add(tenant)
    await db.commit()
    await db.refresh(tenant)
    return TenantResponse(
        id=str(tenant.id),
        nome=tenant.nome,
        slug=getattr(tenant, "slug", None),
        ativo=bool(tenant.ativo),
        tipo_negocio=getattr(tenant, "tipo_negocio", "mercearia") or "mercearia",
        is_system=bool(getattr(tenant, "is_system", False)),
    )


@router.delete("/{tenant_id}", response_model=TenantResponse)
async def delete_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    """Exclusão do tenant.

    Por padrão tentamos hard delete. Se houver vínculos (ex.: produtos/vendas/etc.), o banco pode bloquear.
    Nesse caso retornamos 409 orientando desativar via switch.
    """
    try:
        tid = uuid.UUID(tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="tenant_id inválido (UUID esperado)")

    result = await db.execute(select(Tenant).where(Tenant.id == tid))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    if bool(getattr(tenant, "is_system", False)) is True:
        raise HTTPException(status_code=400, detail="Não é possível remover um tenant do sistema")

    if bool(tenant.ativo) is True:
        active_count = await db.scalar(select(func.count()).select_from(Tenant).where(Tenant.ativo == True))
        if (active_count or 0) <= 1:
            raise HTTPException(status_code=400, detail="Não é possível remover o último tenant ativo")

    resp = TenantResponse(
        id=str(tenant.id),
        nome=tenant.nome,
        slug=getattr(tenant, "slug", None),
        ativo=bool(tenant.ativo),
        tipo_negocio=getattr(tenant, "tipo_negocio", "mercearia") or "mercearia",
        is_system=bool(getattr(tenant, "is_system", False)),
    )
    try:
        await db.execute(delete(Tenant).where(Tenant.id == tid))
        await db.commit()
        return resp
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Não foi possível excluir este negócio porque já possui dados associados. Desative o negócio usando o switch.",
        )
