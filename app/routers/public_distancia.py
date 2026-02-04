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
from app.db.models import Produto, Venda, ItemVenda, PaymentTransaction


router = APIRouter(prefix="/public/distancia", tags=["public_distancia"])


class DistanciaItemIn(BaseModel):
    produto_id: str
    quantidade: int = Field(..., ge=1)
    observacao: Optional[str] = None


class DistanciaCheckoutIn(BaseModel):
    tipo: str = Field(..., min_length=3, max_length=20)  # entrega | retirada
    cliente_nome: str = Field(..., min_length=2, max_length=100)
    cliente_telefone: str = Field(..., min_length=6, max_length=30)
    endereco_entrega: Optional[str] = None
    taxa_entrega: float = 0.0
    provider: str = Field(..., min_length=2, max_length=20)  # mpesa | emola
    phone: str = Field(..., min_length=6, max_length=30)  # número para push
    itens: list[DistanciaItemIn] = Field(default_factory=list)


class DistanciaCheckoutOut(BaseModel):
    pedido_uuid: str
    pedido_id: str
    payment_id: str
    status: str


@router.post("/checkout", response_model=DistanciaCheckoutOut)
async def distancia_checkout(
    payload: DistanciaCheckoutIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        if not payload.itens:
            raise HTTPException(status_code=400, detail="Pedido sem itens")

        tipo = (payload.tipo or "").strip().lower()
        if tipo not in ("entrega", "retirada"):
            raise HTTPException(status_code=400, detail="tipo inválido. Use entrega ou retirada")

        provider = (payload.provider or "").strip().lower()
        if provider not in ("mpesa", "emola"):
            raise HTTPException(status_code=400, detail="provider inválido. Use mpesa ou emola")

        if tipo == "entrega" and not (payload.endereco_entrega and payload.endereco_entrega.strip()):
            raise HTTPException(status_code=400, detail="endereco_entrega é obrigatório para entrega")

        venda_uuid = uuid.uuid4()

        nova_venda = Venda(
            id=venda_uuid,
            tenant_id=tenant_id,
            usuario_id=None,
            cliente_id=None,
            total=0.0,
            desconto=0.0,
            forma_pagamento="PENDENTE_PAGAMENTO",
            tipo_pedido="distancia",
            status_pedido="aguardando_pagamento",
            distancia_tipo=tipo,
            cliente_nome=payload.cliente_nome,
            cliente_telefone=payload.cliente_telefone,
            endereco_entrega=payload.endereco_entrega,
            taxa_entrega=float(payload.taxa_entrega or 0.0),
            observacoes=None,
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

        nova_venda.total = float(total + float(payload.taxa_entrega or 0.0))

        payment = PaymentTransaction(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            venda_id=nova_venda.id,
            provider=provider,
            phone=payload.phone,
            amount=float(getattr(nova_venda, "total", 0.0) or 0.0),
            currency="MZN",
            status="pending",
            provider_reference=None,
        )

        db.add(payment)
        await db.commit()

        return DistanciaCheckoutOut(
            pedido_uuid=str(nova_venda.id),
            pedido_id=str(nova_venda.id)[:8],
            payment_id=str(payment.id),
            status="aguardando_pagamento",
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro interno em checkout distância: {str(e)}")
