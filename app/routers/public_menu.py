from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from typing import Optional, List
import uuid
import os

from app.db.database import get_db_session
from app.db.models import Produto, Tenant
from app.core.deps import get_tenant_id

router = APIRouter(prefix="/public/menu", tags=["public_menu"])


class PublicProdutoOut(BaseModel):
    id: str
    nome: str
    descricao: Optional[str] = None
    preco_venda: float
    imagem: Optional[str] = None
    estoque: float


def _resolve_public_image_path(produto: Produto, tenant_id: uuid.UUID) -> Optional[str]:
    existing = getattr(produto, "imagem_path", None)
    if isinstance(existing, str) and existing.strip():
        s = existing.strip()
        # Se o backend está apontando para /media, garantir que o arquivo realmente exista
        if s.startswith("/media/"):
            media_dir = os.getenv("MEDIA_DIR", "media")
            rel = s.replace("/media/", "", 1).lstrip("/")
            abs_path = os.path.join(media_dir, rel.replace("/", os.sep))
            if os.path.exists(abs_path):
                return s
            # Se não existe no disco, cair para tentativa de inferência
        else:
            return s

    media_dir = os.getenv("MEDIA_DIR", "media")
    base_rel = os.path.join("produtos", str(tenant_id))
    base_abs = os.path.join(media_dir, base_rel)

    pid = getattr(produto, "id", None)
    if not pid:
        return None

    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        out_name = f"{pid}{ext}"
        abs_path = os.path.join(base_abs, out_name)
        if os.path.exists(abs_path):
            return f"/media/produtos/{tenant_id}/{out_name}"

    return None


@router.get("/produtos", response_model=List[PublicProdutoOut])
async def public_menu_produtos(
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    query = select(Produto).where(
        Produto.ativo == True,
        Produto.tenant_id == tenant_id,
    )
    if q:
        term = f"%{q.strip()}%"
        query = query.where(or_(Produto.nome.ilike(term), Produto.codigo.ilike(term)))

    result = await db.execute(query.order_by(Produto.nome))
    produtos = result.scalars().all()

    return [
        PublicProdutoOut(
            id=str(p.id),
            nome=p.nome,
            descricao=p.descricao,
            preco_venda=float(p.preco_venda or 0.0),
            imagem=_resolve_public_image_path(p, tenant_id),
            estoque=float(p.estoque or 0.0),
        )
        for p in produtos
    ]


@router.get("/{tenant_slug}/produtos", response_model=List[PublicProdutoOut])
async def public_menu_produtos_by_slug(
    tenant_slug: str,
    q: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
):
    slug = (tenant_slug or "").strip().lower()
    if not slug:
        raise HTTPException(status_code=400, detail="tenant_slug inválido")

    res_tenant = await db.execute(select(Tenant).where(Tenant.slug == slug, Tenant.ativo == True))
    tenant = res_tenant.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    tenant_id = tenant.id
    query = select(Produto).where(
        Produto.ativo == True,
        Produto.tenant_id == tenant_id,
    )
    if q:
        term = f"%{q.strip()}%"
        query = query.where(or_(Produto.nome.ilike(term), Produto.codigo.ilike(term)))

    result = await db.execute(query.order_by(Produto.nome))
    produtos = result.scalars().all()
    return [
        PublicProdutoOut(
            id=str(p.id),
            nome=p.nome,
            descricao=p.descricao,
            preco_venda=float(p.preco_venda or 0.0),
            imagem=_resolve_public_image_path(p, tenant_id),
            estoque=float(p.estoque or 0.0),
        )
        for p in produtos
    ]
