from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_admin_user, get_tenant_id
from app.db.database import get_db_session
from app.db.models import Produto, Venda, ItemVenda


router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])


class PedidoListItem(BaseModel):
    pedido_uuid: str
    pedido_id: str
    tipo_pedido: Optional[str] = None
    status: str
    total: float
    mesa_id: Optional[int] = None
    lugar_numero: Optional[int] = None
    distancia_tipo: Optional[str] = None
    cliente_nome: Optional[str] = None
    cliente_telefone: Optional[str] = None
    endereco_entrega: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PedidoDetail(BaseModel):
    pedido_uuid: str
    pedido_id: str
    tipo_pedido: Optional[str] = None
    status: str
    total: float
    taxa_entrega: float = 0.0
    mesa_id: Optional[int] = None
    lugar_numero: Optional[int] = None
    distancia_tipo: Optional[str] = None
    cliente_nome: Optional[str] = None
    cliente_telefone: Optional[str] = None
    endereco_entrega: Optional[str] = None
    observacoes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    itens: list[dict] = []


class PedidoStatusUpdate(BaseModel):
    status: str


def _resolve_status(v: Venda) -> str:
    status_pedido = (getattr(v, "status_pedido", None) or "").strip()
    if status_pedido:
        return status_pedido
    if bool(getattr(v, "cancelada", False)):
        return "cancelado"
    fp = (getattr(v, "forma_pagamento", None) or "").strip().upper()
    if fp == "PENDENTE_PAGAMENTO":
        return "criado"
    return "aguardando_pagamento"


@router.get("/", response_model=list[PedidoListItem])
async def listar_pedidos(
    status_filter: Optional[str] = None,
    incluir_cancelados: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    limit = max(1, min(500, int(limit)))

    stmt = (
        select(Venda)
        .where(Venda.tenant_id == tenant_id)
        .order_by(Venda.created_at.desc())
        .limit(limit)
    )

    if not incluir_cancelados:
        stmt = stmt.where(Venda.cancelada == False)

    # Restaurante: pedidos são vendas com tipo_pedido preenchido ou mesa_id/lugar_numero.
    # Não aplicamos filtro rígido aqui para manter compatibilidade com mercearia, mas o PDV web pode filtrar.

    res = await db.execute(stmt)
    rows = res.scalars().all()

    out: list[PedidoListItem] = []
    for v in rows:
        s = _resolve_status(v)
        if status_filter and s.lower() != str(status_filter).strip().lower():
            continue
        out.append(
            PedidoListItem(
                pedido_uuid=str(v.id),
                pedido_id=str(v.id)[:8],
                tipo_pedido=getattr(v, "tipo_pedido", None),
                status=s,
                total=float(getattr(v, "total", 0.0) or 0.0),
                mesa_id=getattr(v, "mesa_id", None),
                lugar_numero=getattr(v, "lugar_numero", None),
                distancia_tipo=getattr(v, "distancia_tipo", None),
                cliente_nome=getattr(v, "cliente_nome", None),
                cliente_telefone=getattr(v, "cliente_telefone", None),
                endereco_entrega=getattr(v, "endereco_entrega", None),
                created_at=getattr(v, "created_at", None),
                updated_at=getattr(v, "updated_at", None) or getattr(v, "created_at", None),
            )
        )
    return out


@router.get("/uuid/{pedido_uuid}", response_model=PedidoDetail)
async def obter_pedido(
    pedido_uuid: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        vid = uuid.UUID(str(pedido_uuid))
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res = await db.execute(select(Venda).where(Venda.id == vid, Venda.tenant_id == tenant_id))
    v = res.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    res_itens = await db.execute(
        select(ItemVenda, Produto)
        .join(Produto, Produto.id == ItemVenda.produto_id)
        .where(ItemVenda.venda_id == v.id, Produto.tenant_id == tenant_id)
    )
    itens_rows = res_itens.all()
    itens_out = []
    for item, produto in itens_rows:
        itens_out.append(
            {
                "produto_id": str(getattr(produto, "id", "")),
                "produto_nome": str(getattr(produto, "nome", None) or getattr(produto, "descricao", None) or "Produto"),
                "quantidade": int(getattr(item, "quantidade", 0) or 0),
                "preco_unitario": float(getattr(item, "preco_unitario", 0.0) or 0.0),
                "subtotal": float(getattr(item, "subtotal", 0.0) or 0.0),
            }
        )

    taxa_entrega = float(getattr(v, "taxa_entrega", 0.0) or 0.0)
    total_base = float(getattr(v, "total", 0.0) or 0.0)

    updated_at = getattr(v, "updated_at", None) or getattr(v, "created_at", None)

    return PedidoDetail(
        pedido_uuid=str(v.id),
        pedido_id=str(v.id)[:8],
        tipo_pedido=getattr(v, "tipo_pedido", None),
        status=_resolve_status(v),
        total=total_base,
        taxa_entrega=taxa_entrega,
        mesa_id=getattr(v, "mesa_id", None),
        lugar_numero=getattr(v, "lugar_numero", None),
        distancia_tipo=getattr(v, "distancia_tipo", None),
        cliente_nome=getattr(v, "cliente_nome", None),
        cliente_telefone=getattr(v, "cliente_telefone", None),
        endereco_entrega=getattr(v, "endereco_entrega", None),
        observacoes=getattr(v, "observacoes", None),
        created_at=getattr(v, "created_at", None),
        updated_at=updated_at,
        itens=itens_out,
    )


@router.put("/uuid/{pedido_uuid}/status")
async def atualizar_status_pedido(
    pedido_uuid: str,
    payload: PedidoStatusUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    novo = (payload.status or "").strip().lower()
    if not novo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status é obrigatório")

    try:
        vid = uuid.UUID(str(pedido_uuid))
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res = await db.execute(select(Venda).where(Venda.id == vid, Venda.tenant_id == tenant_id))
    v = res.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    v.status_pedido = novo
    if novo in ("cancelado", "cancelada"):
        v.cancelada = True
    await db.commit()

    return {"ok": True, "pedido_uuid": str(v.id), "status": novo}
