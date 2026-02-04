from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_tenant_id
from app.db.database import get_db_session
from app.db.models import Produto, Venda, ItemVenda


router = APIRouter(prefix="/public", tags=["public_pedidos"])


class PublicPedidoItemIn(BaseModel):
    produto_id: str
    quantidade: int = Field(..., ge=1)
    observacao: Optional[str] = None


class PublicPedidoCreateIn(BaseModel):
    mesa_id: int
    lugar_numero: int = 1
    observacao_cozinha: Optional[str] = None
    payment_mode: Optional[str] = None
    itens: list[PublicPedidoItemIn] = Field(default_factory=list)


class PublicPedidoCreateOut(BaseModel):
    pedido_id: str
    pedido_uuid: str
    status: str


class PublicMesaOut(BaseModel):
    id: int
    nome: str
    numero: int
    mesa_token: str


def _default_mesas() -> list[PublicMesaOut]:
    return [
        PublicMesaOut(id=1, nome="Mesa 1", numero=1, mesa_token="mesa-1"),
        PublicMesaOut(id=2, nome="Mesa 2", numero=2, mesa_token="mesa-2"),
        PublicMesaOut(id=3, nome="Mesa 3", numero=3, mesa_token="mesa-3"),
        PublicMesaOut(id=4, nome="Mesa 4", numero=4, mesa_token="mesa-4"),
    ]


@router.get("/mesas", response_model=list[PublicMesaOut])
async def public_list_mesas():
    return _default_mesas()


def _mesa_from_token(token: str) -> Optional[PublicMesaOut]:
    t = (token or "").strip().lower()
    for m in _default_mesas():
        if m.mesa_token == t:
            return m
    return None


async def _create_venda_from_public_pedido(
    payload: PublicPedidoCreateIn,
    db: AsyncSession,
    tenant_id: uuid.UUID,
) -> Venda:
    if not payload.itens:
        raise HTTPException(status_code=400, detail="Pedido sem itens")

    venda_uuid = uuid.uuid4()

    payment_mode = (payload.payment_mode or "").strip().lower()
    forma_pagamento = "BALCAO" if payment_mode in ("balcao", "cash", "dinheiro") else "PENDENTE_PAGAMENTO"

    status_pedido = "aguardando_pagamento" if forma_pagamento == "BALCAO" else "criado"

    nova_venda = Venda(
        id=venda_uuid,
        tenant_id=tenant_id,
        usuario_id=None,
        cliente_id=None,
        total=0.0,
        desconto=0.0,
        forma_pagamento=forma_pagamento,
        tipo_pedido="local",
        status_pedido=status_pedido,
        mesa_id=int(payload.mesa_id) if getattr(payload, "mesa_id", None) is not None else None,
        lugar_numero=int(payload.lugar_numero) if getattr(payload, "lugar_numero", None) is not None else None,
        observacoes=payload.observacao_cozinha,
        cancelada=False,
        created_at=datetime.utcnow(),
    )
    db.add(nova_venda)
    await db.flush()

    total = 0.0
    for it in payload.itens:
        try:
            produto_uuid = uuid.UUID(str(it.produto_id))
        except Exception:
            raise HTTPException(status_code=400, detail=f"produto_id inválido: {it.produto_id}")

        res_prod = await db.execute(
            select(Produto).where(Produto.id == produto_uuid, Produto.tenant_id == tenant_id)
        )
        produto = res_prod.scalar_one_or_none()
        if not produto:
            raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {it.produto_id}")

        qtd = max(1, int(it.quantidade or 0))
        preco_unit = float(getattr(produto, "preco_venda", 0.0) or 0.0)
        subtotal = float(preco_unit * qtd)

        taxa_iva = float(getattr(produto, "taxa_iva", 0.0) or 0.0)
        if taxa_iva > 0:
            fator = 1 + (taxa_iva / 100.0)
            base_iva = subtotal / fator
            valor_iva = subtotal - base_iva
        else:
            base_iva = subtotal
            valor_iva = 0.0

        item = ItemVenda(
            venda_id=nova_venda.id,
            produto_id=produto_uuid,
            quantidade=qtd,
            peso_kg=0.0,
            preco_unitario=preco_unit,
            subtotal=subtotal,
            taxa_iva=taxa_iva,
            base_iva=base_iva,
            valor_iva=valor_iva,
        )
        db.add(item)
        total += subtotal

    nova_venda.total = float(total)
    await db.commit()
    await db.refresh(nova_venda)
    return nova_venda


@router.post("/pedidos", response_model=PublicPedidoCreateOut)
async def public_create_pedido(
    payload: PublicPedidoCreateIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    venda = await _create_venda_from_public_pedido(payload, db, tenant_id)
    return PublicPedidoCreateOut(
        pedido_id=str(venda.id)[:8],
        pedido_uuid=str(venda.id),
        status=str(getattr(venda, "status_pedido", None) or "criado"),
    )


@router.post("/mesa/{mesa_token}/pedidos", response_model=PublicPedidoCreateOut)
async def public_create_pedido_by_token(
    mesa_token: str,
    payload: PublicPedidoCreateIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    mesa = _mesa_from_token(mesa_token)
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    payload.mesa_id = int(mesa.id)
    venda = await _create_venda_from_public_pedido(payload, db, tenant_id)
    return PublicPedidoCreateOut(
        pedido_id=str(venda.id)[:8],
        pedido_uuid=str(venda.id),
        status=str(getattr(venda, "status_pedido", None) or "criado"),
    )


@router.get("/pedidos/uuid/{pedido_uuid}")
async def public_get_pedido_by_uuid(
    pedido_uuid: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        vid = uuid.UUID(pedido_uuid)
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res = await db.execute(select(Venda).where(Venda.id == vid, Venda.tenant_id == tenant_id))
    v = res.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    status = getattr(v, "status_pedido", None)
    if not status:
        status = "aguardando_pagamento" if (getattr(v, "forma_pagamento", "") == "PENDENTE_PAGAMENTO") else "criado"
    if bool(getattr(v, "cancelada", False)):
        status = "CANCELADO"

    res_itens = await db.execute(
        select(ItemVenda, Produto)
        .join(Produto, Produto.id == ItemVenda.produto_id)
        .where(
            ItemVenda.venda_id == v.id,
            Produto.tenant_id == tenant_id,
        )
    )
    itens_rows = res_itens.all()
    itens_out = []
    for item, produto in itens_rows:
        itens_out.append(
            {
                "produto_id": str(getattr(produto, "id", "")),
                "produto_nome": str(getattr(produto, "nome", None) or getattr(produto, "descricao", None) or "Produto"),
                "quantidade": int(getattr(item, "quantidade", 0) or 0),
                "subtotal": float(getattr(item, "subtotal", 0.0) or 0.0),
            }
        )

    taxa_entrega = float(getattr(v, "taxa_entrega", 0.0) or 0.0)
    total_base = float(getattr(v, "total", 0.0) or 0.0)
    valor_total = float(total_base + (taxa_entrega if taxa_entrega > 0 else 0.0))

    updated_at = getattr(v, "updated_at", None)
    if not updated_at:
        updated_at = getattr(v, "created_at", None)

    return {
        "pedido_uuid": str(v.id),
        "pedido_id": str(v.id)[:8],
        "status": status,
        "valor_total": valor_total,
        "total": total_base,
        "taxa_entrega": taxa_entrega,
        "tipo_pedido": getattr(v, "tipo_pedido", None),
        "mesa_id": getattr(v, "mesa_id", None),
        "distancia_tipo": getattr(v, "distancia_tipo", None),
        "cliente_nome": getattr(v, "cliente_nome", None),
        "cliente_telefone": getattr(v, "cliente_telefone", None),
        "endereco_entrega": getattr(v, "endereco_entrega", None),
        "created_at": getattr(v, "created_at", None).isoformat() if getattr(v, "created_at", None) else None,
        "updated_at": updated_at.isoformat() if updated_at else None,
        "itens": itens_out,
    }
