from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
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


@router.get("/{payment_id}/pay", response_class=HTMLResponse)
async def payment_pay_page(
    payment_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        pid = uuid.UUID(payment_id)
    except Exception:
        return HTMLResponse("<h2>Pagamento inválido</h2>", status_code=400)

    res = await db.execute(
        select(PaymentTransaction).where(PaymentTransaction.id == pid, PaymentTransaction.tenant_id == tenant_id)
    )
    p = res.scalar_one_or_none()
    if not p:
        return HTMLResponse("<h2>Pagamento não encontrado</h2>", status_code=404)

    status_val = str(getattr(p, "status", "pending") or "pending")
    amount = float(getattr(p, "amount", 0.0) or 0.0)
    currency = str(getattr(p, "currency", "MZN") or "MZN")
    provider = str(getattr(p, "provider", "") or "").upper()
    phone = str(getattr(p, "phone", "") or "")

    if status_val == "paid":
        return HTMLResponse(
            f"""
            <html><head><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>
            <body style='font-family:Segoe UI,Arial,sans-serif;padding:18px;'>
              <h2>Pagamento confirmado</h2>
              <div>Operadora: <b>{provider}</b></div>
              <div>Número: <b>{phone}</b></div>
              <div style='margin-top:10px;'>Valor: <b>{amount:.2f} {currency}</b></div>
              <p>Pode fechar esta página.</p>
            </body></html>
            """,
            status_code=200,
        )

    return HTMLResponse(
        f"""
        <html>
          <head>
            <meta name='viewport' content='width=device-width, initial-scale=1.0'>
            <title>Confirmar pagamento</title>
          </head>
          <body style='font-family:Segoe UI,Arial,sans-serif;padding:18px;'>
            <h2>Confirmar pagamento (simulação)</h2>
            <div style='margin-top:6px;'>Operadora: <b>{provider}</b></div>
            <div style='margin-top:6px;'>Número: <b>{phone}</b></div>
            <div style='margin-top:12px;'>Valor: <b>{amount:.2f} {currency}</b></div>
            <div style='margin-top:16px;'>
              <button id='payBtn' style='padding:12px 16px;border:0;border-radius:12px;background:#16a34a;color:#fff;font-weight:800;cursor:pointer;'>Confirmar no telemóvel</button>
            </div>
            <div id='msg' style='margin-top:12px;color:#374151;'></div>
            <script>
              const msg = document.getElementById('msg');
              document.getElementById('payBtn').addEventListener('click', async () => {{
                try {{
                  msg.textContent = 'Processando...';
                  const res = await fetch('/api/payments/{payment_id}/mark-paid', {{ method: 'POST' }});
                  const data = await res.json();
                  msg.textContent = (data && data.status === 'paid') ? 'Pago com sucesso.' : 'Falha.';
                  setTimeout(() => location.reload(), 900);
                }} catch (e) {{
                  msg.textContent = 'Erro ao pagar.';
                }}
              }});
            </script>
          </body>
        </html>
        """,
        status_code=200,
    )
