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
from app.db.models import PaymentTransaction, Venda


router = APIRouter(prefix="/api/payments", tags=["payments"])


class CheckoutRequest(BaseModel):
    pedido_uuid: str
    provider: str = Field(..., min_length=2, max_length=20)
    phone: str = Field(..., min_length=6, max_length=30)


class CheckoutResponse(BaseModel):
    payment_id: str
    pedido_uuid: str
    status: str
    provider: str


@router.post("/checkout", response_model=CheckoutResponse)
async def checkout_payment(
    req: CheckoutRequest,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    provider = (req.provider or "").strip().lower()
    if provider not in ("mpesa", "emola"):
        raise HTTPException(status_code=400, detail="provider inválido. Use mpesa ou emola")

    try:
        pedido_id = uuid.UUID(req.pedido_uuid)
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res_v = await db.execute(select(Venda).where(Venda.id == pedido_id, Venda.tenant_id == tenant_id))
    venda = res_v.scalar_one_or_none()
    if not venda:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    payment = PaymentTransaction(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        venda_id=venda.id,
        provider=provider,
        phone=req.phone,
        amount=float(getattr(venda, "total", 0.0) or 0.0),
        currency="MZN",
        status="pending",
        provider_reference=None,
    )

    db.add(payment)
    await db.commit()

    return CheckoutResponse(
        payment_id=str(payment.id),
        pedido_uuid=str(venda.id),
        status="pending",
        provider=provider,
    )


@router.get("/{payment_id}")
async def get_payment_status(
    payment_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        pid = uuid.UUID(payment_id)
    except Exception:
        raise HTTPException(status_code=400, detail="payment_id inválido")

    res = await db.execute(select(PaymentTransaction).where(PaymentTransaction.id == pid, PaymentTransaction.tenant_id == tenant_id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")

    return {
        "payment_id": str(p.id),
        "pedido_uuid": str(p.venda_id) if getattr(p, "venda_id", None) else None,
        "provider": getattr(p, "provider", None),
        "phone": getattr(p, "phone", None),
        "amount": float(getattr(p, "amount", 0.0) or 0.0),
        "currency": getattr(p, "currency", "MZN"),
        "status": getattr(p, "status", "pending"),
    }


@router.post("/{payment_id}/mark-paid")
async def mark_payment_paid(
    payment_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        pid = uuid.UUID(payment_id)
    except Exception:
        raise HTTPException(status_code=400, detail="payment_id inválido")

    res = await db.execute(select(PaymentTransaction).where(PaymentTransaction.id == pid, PaymentTransaction.tenant_id == tenant_id))
    p = res.scalar_one_or_none()
    if not p:
        raise HTTPException(status_code=404, detail="Pagamento não encontrado")

    p.status = "paid"
    p.updated_at = datetime.utcnow()

    if getattr(p, "venda_id", None):
        res_v = await db.execute(select(Venda).where(Venda.id == p.venda_id, Venda.tenant_id == tenant_id))
        venda = res_v.scalar_one_or_none()
        if venda:
            venda.forma_pagamento = (getattr(p, "provider", None) or "online").upper()
            try:
                setattr(venda, "status_pedido", "pago")
            except Exception:
                pass

    await db.commit()
    return {"status": "paid"}
