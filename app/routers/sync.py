from datetime import datetime, timezone
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Dict, Any, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_tenant_id
from app.db.database import get_db_session
from app.db.models import Venda, ItemVenda, Produto

router = APIRouter(prefix="/api", tags=["Sync"])

# Placeholder for a dependency that would get the current user from a JWT token
async def get_current_user():
    # In a real app, this would decode the token and return the user model
    return {"username": "testuser", "id": "123"}


@router.get("/sync/ping")
async def sync_ping():
    return {"ok": True}

class SyncPushEvent(BaseModel):
    outbox_id: int
    entity: str
    operation: str
    payload: Dict[str, Any] = {}


class SyncPushRequest(BaseModel):
    events: List[SyncPushEvent] = []


@router.post("/sync/push")
async def push_changes(
    req: SyncPushRequest,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Receives a batch of offline changes from a client."""
    # TODO: Implement the logic to process changes:
    # 1. Iterate through changes in a transaction.
    # 2. Use a repository/service layer to apply upserts.
    # 3. Use LWW (Last-Write-Wins) for conflict resolution based on updated_at.
    # 4. Collect temp_id -> id mappings.
    # 5. Return the mappings and status for each change.
    events = req.events or []
    try:
        print(f"Received {len(events)} events from user {current_user['username']}")
    except Exception:
        pass

    results: list[dict] = []

    for ev in events:
        try:
            if str(ev.entity).lower() != "pedido":
                results.append({"outbox_id": int(ev.outbox_id), "ok": True, "error": None})
                continue

            payload = ev.payload or {}
            pedido_uuid = payload.get("uuid")
            if not pedido_uuid:
                results.append({"outbox_id": int(ev.outbox_id), "ok": False, "error": "missing uuid"})
                continue

            try:
                venda_uuid = uuid.UUID(str(pedido_uuid))
            except Exception:
                venda_uuid = uuid.uuid4()

            existing = await db.execute(
                select(Venda).where(Venda.id == venda_uuid, Venda.tenant_id == tenant_id)
            )
            venda_db = existing.scalar_one_or_none()

            total = float(payload.get("valor_total") or payload.get("total") or 0)
            created_at = payload.get("created_at") or payload.get("data_inicio")

            if venda_db is None:
                venda_db = Venda(
                    id=venda_uuid,
                    tenant_id=tenant_id,
                    usuario_id=None,
                    cliente_id=None,
                    total=total,
                    desconto=0.0,
                    forma_pagamento=str(payload.get("forma_pagamento") or "dinheiro"),
                    observacoes=payload.get("observacao_cozinha") or payload.get("observacoes"),
                    cancelada=False,
                    created_at=created_at,
                )
                db.add(venda_db)
                await db.flush()

            itens = payload.get("itens") or []

            try:
                await db.execute(select(ItemVenda).where(ItemVenda.venda_id == venda_db.id))
            except Exception:
                pass

            for it in itens:
                if not isinstance(it, dict):
                    continue
                produto_codigo = it.get("produto_codigo")
                produto_id = it.get("produto_id")

                produto_uuid = None
                if produto_id:
                    try:
                        produto_uuid = uuid.UUID(str(produto_id))
                    except Exception:
                        produto_uuid = None

                if produto_uuid is None and produto_codigo:
                    try:
                        res_p = await db.execute(
                            select(Produto).where(
                                Produto.codigo == str(produto_codigo),
                                Produto.tenant_id == tenant_id,
                            )
                        )
                        prod = res_p.scalar_one_or_none()
                        if prod:
                            produto_uuid = prod.id
                    except Exception:
                        produto_uuid = None

                if produto_uuid is None:
                    continue

                quantidade = int(it.get("quantidade") or 1)
                preco_unitario = float(it.get("preco_unitario") or it.get("produto_preco_venda") or 0)
                subtotal = float(it.get("subtotal") or (quantidade * preco_unitario))

                db.add(
                    ItemVenda(
                        venda_id=venda_db.id,
                        produto_id=produto_uuid,
                        quantidade=max(1, quantidade),
                        peso_kg=float(it.get("peso_kg") or 0.0),
                        preco_unitario=preco_unitario,
                        subtotal=subtotal,
                        taxa_iva=0.0,
                        base_iva=subtotal,
                        valor_iva=0.0,
                    )
                )

            await db.commit()
            results.append({"outbox_id": int(ev.outbox_id), "ok": True, "error": None})
        except Exception as e:
            try:
                await db.rollback()
            except Exception:
                pass
            results.append({"outbox_id": int(ev.outbox_id), "ok": False, "error": str(e)})

    return {"results": results}

@router.get("/sync/pull")
async def pull_changes(
    since: Optional[str] = None,
    limit: int = 500,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    server_now = datetime.now(timezone.utc).isoformat()

    def _parse_since(v: Optional[str]):
        if not v:
            return None
        try:
            s = str(v)
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            return datetime.fromisoformat(s)
        except Exception:
            return None

    since_dt = _parse_since(since)
    limit_i = max(1, min(int(limit or 500), 1000))

    stmt = (
        select(Venda)
        .options(selectinload(Venda.itens))
        .where(Venda.tenant_id == tenant_id)
        .where(Venda.cancelada == False)
    )
    if since_dt is not None:
        try:
            stmt = stmt.where(Venda.updated_at > since_dt)
        except Exception:
            pass
    try:
        stmt = stmt.order_by(Venda.updated_at.asc()).limit(limit_i)
    except Exception:
        stmt = stmt.limit(limit_i)

    result = await db.execute(stmt)
    vendas = result.scalars().all() or []

    # Resolver dados de produto para preencher produto_codigo/nome/preco_venda
    produto_ids = set()
    for v in vendas:
        for it in (getattr(v, "itens", None) or []):
            try:
                produto_ids.add(it.produto_id)
            except Exception:
                continue

    produtos_map: dict[str, dict] = {}
    if produto_ids:
        res_p = await db.execute(
            select(Produto).where(Produto.id.in_(list(produto_ids)), Produto.tenant_id == tenant_id)
        )
        for p in res_p.scalars().all() or []:
            produtos_map[str(p.id)] = {
                "codigo": getattr(p, "codigo", None),
                "nome": getattr(p, "nome", None),
                "preco_venda": float(getattr(p, "preco_venda", 0) or 0),
            }

    pedidos: list[dict] = []
    max_updated: Optional[str] = None

    for v in vendas:
        v_id = str(getattr(v, "id", ""))
        v_updated = getattr(v, "updated_at", None) or getattr(v, "created_at", None)
        try:
            v_updated_iso = v_updated.isoformat() if v_updated else None
        except Exception:
            v_updated_iso = None
        if v_updated_iso:
            max_updated = v_updated_iso

        itens_out: list[dict] = []
        for it in (getattr(v, "itens", None) or []):
            pid = str(getattr(it, "produto_id", ""))
            pm = produtos_map.get(pid) or {}

            item_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{v_id}:{pid}"))

            itens_out.append(
                {
                    "uuid": item_uuid,
                    "produto_id": pid,
                    "produto_codigo": pm.get("codigo"),
                    "produto_nome": pm.get("nome"),
                    "produto_preco_venda": pm.get("preco_venda"),
                    "quantidade": int(getattr(it, "quantidade", 1) or 1),
                    "preco_unitario": float(getattr(it, "preco_unitario", 0) or 0),
                    "observacao": None,
                    "created_at": v_updated_iso,
                    "updated_at": v_updated_iso,
                }
            )

        pedidos.append(
            {
                "uuid": v_id,
                "mesa_id": None,
                "mesa_numero": None,
                "lugar_numero": 1,
                "usuario_id": str(getattr(v, "usuario_id", None)) if getattr(v, "usuario_id", None) else None,
                "status": "pago",
                "forma_pagamento_id": 1,
                "valor_total": float(getattr(v, "total", 0) or 0),
                "valor_recebido": float(getattr(v, "total", 0) or 0),
                "troco": 0.0,
                "observacao_cozinha": getattr(v, "observacoes", None),
                "data_inicio": getattr(v, "created_at", None).isoformat() if getattr(v, "created_at", None) else None,
                "data_fechamento": getattr(v, "created_at", None).isoformat() if getattr(v, "created_at", None) else None,
                "created_at": getattr(v, "created_at", None).isoformat() if getattr(v, "created_at", None) else None,
                "updated_at": v_updated_iso,
                "itens": itens_out,
            }
        )

    next_since = max_updated
    return {"server_now": server_now, "pedidos": pedidos, "next_since": next_since, "since": since, "limit": int(limit_i)}
