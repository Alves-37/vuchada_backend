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
from app.db.models import Venda, ItemVenda, Produto, Cliente, Divida, ItemDivida, PagamentoDivida

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

    def _parse_uuid(v: Any) -> uuid.UUID | None:
        if v is None:
            return None
        try:
            return uuid.UUID(str(v))
        except Exception:
            return None

    for ev in events:
        try:
            entity = str(ev.entity or "").lower()
            payload = ev.payload or {}

            if entity == "pedido":
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
                continue

            if entity == "cliente":
                cli_id = _parse_uuid(payload.get("id") or payload.get("uuid"))
                if cli_id is None:
                    cli_id = uuid.uuid4()

                res_cli = await db.execute(
                    select(Cliente).where(Cliente.id == cli_id, Cliente.tenant_id == tenant_id)
                )
                cli_db = res_cli.scalar_one_or_none()

                if cli_db is None:
                    cli_db = Cliente(
                        id=cli_id,
                        tenant_id=tenant_id,
                        nome=str(payload.get("nome") or "").strip() or "Cliente",
                        documento=payload.get("documento"),
                        telefone=payload.get("telefone"),
                        endereco=payload.get("endereco"),
                        ativo=bool(payload.get("ativo", True)),
                    )
                    db.add(cli_db)
                else:
                    # Upsert simples (LWW fica por conta do client por enquanto)
                    if payload.get("nome") is not None:
                        cli_db.nome = str(payload.get("nome") or cli_db.nome)
                    if payload.get("documento") is not None:
                        cli_db.documento = payload.get("documento")
                    if payload.get("telefone") is not None:
                        cli_db.telefone = payload.get("telefone")
                    if payload.get("endereco") is not None:
                        cli_db.endereco = payload.get("endereco")
                    if payload.get("ativo") is not None:
                        cli_db.ativo = bool(payload.get("ativo"))

                await db.commit()
                results.append({"outbox_id": int(ev.outbox_id), "ok": True, "error": None})
                continue

            if entity == "divida":
                div_id = _parse_uuid(payload.get("id"))
                if div_id is None:
                    div_id = uuid.uuid4()

                res_div = await db.execute(
                    select(Divida)
                    .options(selectinload(Divida.itens), selectinload(Divida.pagamentos))
                    .where(Divida.id == div_id, Divida.tenant_id == tenant_id)
                )
                div_db = res_div.scalar_one_or_none()

                cliente_id = _parse_uuid(payload.get("cliente_id"))
                usuario_id = _parse_uuid(payload.get("usuario_id"))

                if div_db is None:
                    div_db = Divida(
                        id=div_id,
                        tenant_id=tenant_id,
                        id_local=payload.get("id_local"),
                        cliente_id=cliente_id,
                        usuario_id=usuario_id,
                        valor_total=float(payload.get("valor_total") or 0),
                        valor_original=float(payload.get("valor_original") or 0),
                        desconto_aplicado=float(payload.get("desconto_aplicado") or 0),
                        percentual_desconto=float(payload.get("percentual_desconto") or 0),
                        valor_pago=float(payload.get("valor_pago") or 0),
                        status=str(payload.get("status") or "Pendente"),
                        observacao=payload.get("observacao"),
                    )
                    db.add(div_db)
                    await db.flush()
                else:
                    if payload.get("id_local") is not None:
                        div_db.id_local = payload.get("id_local")
                    if cliente_id is not None:
                        div_db.cliente_id = cliente_id
                    if usuario_id is not None:
                        div_db.usuario_id = usuario_id
                    if payload.get("valor_total") is not None:
                        div_db.valor_total = float(payload.get("valor_total") or 0)
                    if payload.get("valor_original") is not None:
                        div_db.valor_original = float(payload.get("valor_original") or 0)
                    if payload.get("desconto_aplicado") is not None:
                        div_db.desconto_aplicado = float(payload.get("desconto_aplicado") or 0)
                    if payload.get("percentual_desconto") is not None:
                        div_db.percentual_desconto = float(payload.get("percentual_desconto") or 0)
                    if payload.get("valor_pago") is not None:
                        div_db.valor_pago = float(payload.get("valor_pago") or 0)
                    if payload.get("status") is not None:
                        div_db.status = str(payload.get("status") or div_db.status)
                    if payload.get("observacao") is not None:
                        div_db.observacao = payload.get("observacao")

                # Itens e pagamentos (best-effort; idempotência por (divida_id, produto_id, subtotal))
                itens = payload.get("itens") or []
                if isinstance(itens, list):
                    for it in itens:
                        if not isinstance(it, dict):
                            continue
                        prod_uuid = _parse_uuid(it.get("produto_id"))
                        if prod_uuid is None:
                            continue
                        # validar produto no tenant
                        rp = await db.execute(
                            select(Produto).where(Produto.id == prod_uuid, Produto.tenant_id == tenant_id)
                        )
                        if rp.scalar_one_or_none() is None:
                            continue
                        db.add(
                            ItemDivida(
                                divida_id=div_db.id,
                                produto_id=prod_uuid,
                                quantidade=float(it.get("quantidade") or 0),
                                preco_unitario=float(it.get("preco_unitario") or 0),
                                subtotal=float(it.get("subtotal") or 0),
                                peso_kg=float(it.get("peso_kg") or 0.0),
                            )
                        )

                pagamentos = payload.get("pagamentos") or []
                if isinstance(pagamentos, list):
                    for pg in pagamentos:
                        if not isinstance(pg, dict):
                            continue
                        db.add(
                            PagamentoDivida(
                                divida_id=div_db.id,
                                valor=float(pg.get("valor") or 0),
                                forma_pagamento=str(pg.get("forma_pagamento") or ""),
                                usuario_id=_parse_uuid(pg.get("usuario_id")),
                            )
                        )

                await db.commit()
                results.append({"outbox_id": int(ev.outbox_id), "ok": True, "error": None})
                continue

            # Entidade desconhecida: não bloquear o outbox.
            results.append({"outbox_id": int(ev.outbox_id), "ok": True, "error": None})
            continue

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
    # Clientes (incremental)
    clientes_out: list[dict] = []
    stmt_cli = select(Cliente).where(Cliente.tenant_id == tenant_id)
    if since_dt is not None:
        try:
            stmt_cli = stmt_cli.where(Cliente.updated_at > since_dt)
        except Exception:
            pass
    try:
        stmt_cli = stmt_cli.order_by(Cliente.updated_at.asc()).limit(limit_i)
    except Exception:
        stmt_cli = stmt_cli.limit(limit_i)

    res_cli = await db.execute(stmt_cli)
    clientes = res_cli.scalars().all() or []
    for c in clientes:
        cu = getattr(c, "updated_at", None) or getattr(c, "created_at", None)
        try:
            cu_iso = cu.isoformat() if cu else None
        except Exception:
            cu_iso = None
        if cu_iso:
            max_updated = cu_iso
        clientes_out.append(
            {
                "id": str(getattr(c, "id", "")),
                "nome": getattr(c, "nome", None),
                "documento": getattr(c, "documento", None),
                "telefone": getattr(c, "telefone", None),
                "endereco": getattr(c, "endereco", None),
                "ativo": bool(getattr(c, "ativo", True)),
                "created_at": getattr(c, "created_at", None).isoformat() if getattr(c, "created_at", None) else None,
                "updated_at": cu_iso,
            }
        )

    # Dívidas (incremental; inclui itens/pagamentos)
    dividas_out: list[dict] = []
    stmt_div = (
        select(Divida)
        .options(selectinload(Divida.itens), selectinload(Divida.pagamentos))
        .where(Divida.tenant_id == tenant_id)
    )
    if since_dt is not None:
        try:
            stmt_div = stmt_div.where(Divida.updated_at > since_dt)
        except Exception:
            pass
    try:
        stmt_div = stmt_div.order_by(Divida.updated_at.asc()).limit(limit_i)
    except Exception:
        stmt_div = stmt_div.limit(limit_i)

    res_div = await db.execute(stmt_div)
    dividas = res_div.scalars().all() or []

    # map produto_id -> codigo/nome/preco_venda para itens de dívida
    div_prod_ids = set()
    for d in dividas:
        for it in (getattr(d, "itens", None) or []):
            try:
                div_prod_ids.add(it.produto_id)
            except Exception:
                continue
    div_prod_map: dict[str, dict] = {}
    if div_prod_ids:
        rp2 = await db.execute(
            select(Produto).where(Produto.id.in_(list(div_prod_ids)), Produto.tenant_id == tenant_id)
        )
        for p in rp2.scalars().all() or []:
            div_prod_map[str(p.id)] = {
                "codigo": getattr(p, "codigo", None),
                "nome": getattr(p, "nome", None),
                "preco_venda": float(getattr(p, "preco_venda", 0) or 0),
            }

    for d in dividas:
        du = getattr(d, "updated_at", None) or getattr(d, "created_at", None)
        try:
            du_iso = du.isoformat() if du else None
        except Exception:
            du_iso = None
        if du_iso:
            max_updated = du_iso

        itens = []
        for it in (getattr(d, "itens", None) or []):
            pid = str(getattr(it, "produto_id", ""))
            pm = div_prod_map.get(pid) or {}
            itens.append(
                {
                    "produto_id": pid,
                    "produto_codigo": pm.get("codigo"),
                    "produto_nome": pm.get("nome"),
                    "produto_preco_venda": pm.get("preco_venda"),
                    "quantidade": float(getattr(it, "quantidade", 0) or 0),
                    "preco_unitario": float(getattr(it, "preco_unitario", 0) or 0),
                    "subtotal": float(getattr(it, "subtotal", 0) or 0),
                    "peso_kg": float(getattr(it, "peso_kg", 0) or 0),
                }
            )

        pagamentos = []
        for pg in (getattr(d, "pagamentos", None) or []):
            pagamentos.append(
                {
                    "data_pagamento": getattr(pg, "data_pagamento", None).isoformat() if getattr(pg, "data_pagamento", None) else None,
                    "valor": float(getattr(pg, "valor", 0) or 0),
                    "forma_pagamento": getattr(pg, "forma_pagamento", None),
                    "usuario_id": str(getattr(pg, "usuario_id", None)) if getattr(pg, "usuario_id", None) else None,
                }
            )

        dividas_out.append(
            {
                "id": str(getattr(d, "id", "")),
                "id_local": getattr(d, "id_local", None),
                "cliente_id": str(getattr(d, "cliente_id", None)) if getattr(d, "cliente_id", None) else None,
                "usuario_id": str(getattr(d, "usuario_id", None)) if getattr(d, "usuario_id", None) else None,
                "data_divida": getattr(d, "data_divida", None).isoformat() if getattr(d, "data_divida", None) else None,
                "valor_total": float(getattr(d, "valor_total", 0) or 0),
                "valor_original": float(getattr(d, "valor_original", 0) or 0),
                "desconto_aplicado": float(getattr(d, "desconto_aplicado", 0) or 0),
                "percentual_desconto": float(getattr(d, "percentual_desconto", 0) or 0),
                "valor_pago": float(getattr(d, "valor_pago", 0) or 0),
                "status": getattr(d, "status", None),
                "observacao": getattr(d, "observacao", None),
                "created_at": getattr(d, "created_at", None).isoformat() if getattr(d, "created_at", None) else None,
                "updated_at": du_iso,
                "itens": itens,
                "pagamentos": pagamentos,
            }
        )

    next_since = max_updated
    return {
        "server_now": server_now,
        "pedidos": pedidos,
        "clientes": clientes_out,
        "dividas": dividas_out,
        "next_since": next_since,
        "since": since,
        "limit": int(limit_i),
    }
