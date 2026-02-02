from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pydantic import BaseModel


router = APIRouter(prefix="/api/payments/mock", tags=["payments-mock"])

_PAYMENTS: dict[str, dict[str, Any]] = {}


class CreatePaymentRequest(BaseModel):
    amount: float
    currency: str | None = "MZN"
    description: str | None = None
    order_uuid: str | None = None
    customer_phone: str | None = None
    auto_pay_seconds: int | None = None


@router.post("/create")
async def create_payment(req: CreatePaymentRequest):
    payment_id = str(uuid.uuid4())
    now = time.time()

    auto_sec = int(req.auto_pay_seconds) if req.auto_pay_seconds is not None else 20
    auto_sec = max(0, min(auto_sec, 3600))

    _PAYMENTS[payment_id] = {
        "id": payment_id,
        "status": "pending",
        "amount": float(req.amount or 0),
        "currency": (req.currency or "MZN"),
        "description": req.description,
        "order_uuid": req.order_uuid,
        "customer_phone": req.customer_phone,
        "created_at": now,
        "updated_at": now,
        "auto_pay_seconds": auto_sec,
    }

    pay_url = f"/api/payments/mock/{payment_id}/pay"

    return {
        "payment_id": payment_id,
        "status": "pending",
        "payment_url": pay_url,
    }


def _maybe_auto_pay(p: dict[str, Any]) -> None:
    try:
        if p.get("status") != "pending":
            return
        created = float(p.get("created_at") or 0)
        auto_sec = int(p.get("auto_pay_seconds") or 0)
        if auto_sec <= 0:
            return
        if time.time() - created >= auto_sec:
            p["status"] = "paid"
            p["updated_at"] = time.time()
    except Exception:
        return


@router.get("/{payment_id}")
async def get_payment(payment_id: str):
    p = _PAYMENTS.get(str(payment_id))
    if not p:
        return {"payment_id": payment_id, "status": "not_found"}

    _maybe_auto_pay(p)

    return {
        "payment_id": p.get("id"),
        "status": p.get("status"),
        "amount": p.get("amount"),
        "currency": p.get("currency"),
        "order_uuid": p.get("order_uuid"),
        "updated_at": p.get("updated_at"),
    }


@router.post("/{payment_id}/mark-paid")
async def mark_paid(payment_id: str):
    p = _PAYMENTS.get(str(payment_id))
    if not p:
        return {"payment_id": payment_id, "status": "not_found"}

    p["status"] = "paid"
    p["updated_at"] = time.time()
    return {"payment_id": p.get("id"), "status": p.get("status")}


@router.get("/{payment_id}/pay", response_class=HTMLResponse)
async def pay_page(payment_id: str):
    p = _PAYMENTS.get(str(payment_id))
    if not p:
        return HTMLResponse("<h2>Pagamento não encontrado</h2>", status_code=404)

    _maybe_auto_pay(p)

    status = str(p.get("status") or "pending")
    amount = p.get("amount")
    currency = p.get("currency")

    if status == "paid":
        return HTMLResponse(
            f"""
            <html><head><meta name='viewport' content='width=device-width, initial-scale=1.0'></head>
            <body style='font-family:Segoe UI,Arial,sans-serif;padding:18px;'>
              <h2>Pagamento confirmado</h2>
              <div>Valor: <b>{amount:.2f} {currency}</b></div>
              <p>Pode fechar esta página.</p>
            </body></html>
            """
        )

    return HTMLResponse(
        f"""
        <html>
          <head>
            <meta name='viewport' content='width=device-width, initial-scale=1.0'>
            <title>Pagamento</title>
          </head>
          <body style='font-family:Segoe UI,Arial,sans-serif;padding:18px;'>
            <h2>Pagamento (simulação)</h2>
            <div style='margin-top:8px;'>Valor: <b>{amount:.2f} {currency}</b></div>
            <div style='margin-top:16px;'>
              <button id='payBtn' style='padding:12px 16px;border:0;border-radius:12px;background:#16a34a;color:#fff;font-weight:800;cursor:pointer;'>Pagar agora</button>
            </div>
            <div id='msg' style='margin-top:12px;color:#374151;'></div>
            <script>
              const msg = document.getElementById('msg');
              document.getElementById('payBtn').addEventListener('click', async () => {{
                try {{
                  msg.textContent = 'Processando...';
                  const res = await fetch('/api/payments/mock/{payment_id}/mark-paid', {{ method: 'POST' }});
                  const data = await res.json();
                  msg.textContent = (data && data.status === 'paid') ? 'Pago com sucesso.' : 'Falha.';
                  setTimeout(() => location.reload(), 800);
                }} catch (e) {{
                  msg.textContent = 'Erro ao pagar.';
                }}
              }});
            </script>
          </body>
        </html>
        """
    )
