from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app import models

router = APIRouter(prefix="/sync", tags=["sync"])


@router.get("/ping")
def ping():
    return {"ok": True}


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


@router.post("/push")
def push(payload: dict, db: Session = Depends(get_db)):
    events = (payload or {}).get("events") or []
    results: list[dict] = []

    def _resolve_mesa_id(body: dict) -> int:
        mesa_numero = body.get("mesa_numero")
        if mesa_numero is not None:
            mesa = db.query(models.Mesa).filter(models.Mesa.numero == int(mesa_numero)).first()
            if mesa is None:
                mesa = models.Mesa(numero=int(mesa_numero), capacidade=1, status="livre")
                db.add(mesa)
                db.flush()
            return int(mesa.id)
        return int(body.get("mesa_id") or 0)

    def _resolve_produto_id(item: dict) -> int:
        codigo = item.get("produto_codigo")
        if codigo:
            produto = db.query(models.Produto).filter(models.Produto.codigo == str(codigo)).first()
            if produto is None:
                nome = item.get("produto_nome") or str(codigo)
                preco_venda = float(item.get("produto_preco_venda") or item.get("preco_unitario") or 0)
                produto = models.Produto(
                    codigo=str(codigo),
                    nome=str(nome),
                    descricao=None,
                    imagem=None,
                    preco_custo=0,
                    preco_venda=preco_venda,
                    estoque=0,
                    estoque_minimo=0,
                    ativo=True,
                )
                db.add(produto)
                db.flush()
            return int(produto.id)
        return int(item.get("produto_id") or 0)

    for ev in events:
        outbox_id = ev.get("outbox_id")
        entity = ev.get("entity")
        operation = ev.get("operation")
        body = ev.get("payload") or {}

        if entity != "pedido" or operation not in ("upsert",):
            results.append({"outbox_id": outbox_id, "ok": False, "error": "unsupported"})
            continue

        try:
            pedido_uuid = body.get("uuid")
            if not pedido_uuid:
                raise ValueError("pedido.uuid ausente")

            remote_updated = _parse_dt(body.get("updated_at")) or datetime.now(timezone.utc)

            mesa_id = _resolve_mesa_id(body)
            if not mesa_id:
                raise ValueError("mesa inválida (mesa_id/mesa_numero)")

            pedido = db.query(models.Pedido).filter(models.Pedido.uuid == pedido_uuid).first()
            if pedido is not None:
                local_updated = pedido.updated_at
                if local_updated and remote_updated and remote_updated <= local_updated:
                    results.append({"outbox_id": outbox_id, "ok": True})
                    continue
            else:
                pedido = models.Pedido(uuid=pedido_uuid, mesa_id=mesa_id)
                db.add(pedido)

            pedido.mesa_id = int(mesa_id or pedido.mesa_id or 0)
            pedido.lugar_numero = int(body.get("lugar_numero") or 1)
            pedido.usuario_id = body.get("usuario_id")
            pedido.status = (body.get("status") or "aberto")
            pedido.forma_pagamento_id = body.get("forma_pagamento_id")
            pedido.valor_total = body.get("valor_total") or 0
            pedido.valor_recebido = body.get("valor_recebido") or 0
            pedido.troco = body.get("troco") or 0
            pedido.observacao_cozinha = body.get("observacao_cozinha")

            if body.get("data_inicio"):
                pedido.data_inicio = _parse_dt(body.get("data_inicio"))
            if body.get("data_fechamento"):
                pedido.data_fechamento = _parse_dt(body.get("data_fechamento"))

            if body.get("created_at"):
                pedido.created_at = _parse_dt(body.get("created_at"))
            pedido.updated_at = remote_updated

            # Replace itens (idempotent): ensure any previous rows are removed
            # and also protect against UUID collisions on re-push.
            incoming_item_uuids = [str(it.get("uuid")) for it in (body.get("itens") or []) if it.get("uuid")]
            if pedido.id:
                db.query(models.ItemPedido).filter(models.ItemPedido.pedido_id == pedido.id).delete(
                    synchronize_session=False
                )
            if incoming_item_uuids:
                db.query(models.ItemPedido).filter(models.ItemPedido.uuid.in_(incoming_item_uuids)).delete(
                    synchronize_session=False
                )

            pedido.itens.clear()
            for it in body.get("itens") or []:
                item_uuid = it.get("uuid")
                if not item_uuid:
                    continue

                produto_id = _resolve_produto_id(it)
                if not produto_id:
                    raise ValueError("item.produto inválido (produto_id/produto_codigo)")

                pedido.itens.append(
                    models.ItemPedido(
                        uuid=item_uuid,
                        produto_id=produto_id,
                        quantidade=int(it.get("quantidade") or 1),
                        preco_unitario=it.get("preco_unitario") or 0,
                        observacao=it.get("observacao"),
                        created_at=_parse_dt(it.get("created_at")) or datetime.now(timezone.utc),
                        updated_at=_parse_dt(it.get("updated_at")) or remote_updated,
                    )
                )

            db.commit()
            results.append({"outbox_id": outbox_id, "ok": True})
        except Exception as e:
            db.rollback()
            results.append({"outbox_id": outbox_id, "ok": False, "error": str(e)})

    return {"results": results}


@router.get("/pull")
def pull(since: str | None = None, db: Session = Depends(get_db)):
    since_dt = _parse_dt(since)
    q = db.query(models.Pedido).options(
        joinedload(models.Pedido.itens).joinedload(models.ItemPedido.produto),
        joinedload(models.Pedido.mesa),
    )
    if since_dt is not None:
        q = q.filter(models.Pedido.updated_at > since_dt)
    pedidos = q.order_by(models.Pedido.updated_at.asc()).limit(500).all()

    def serialize(p: models.Pedido):
        return {
            "uuid": p.uuid,
            "mesa_id": p.mesa_id,
            "mesa_numero": (p.mesa.numero if getattr(p, "mesa", None) is not None else None),
            "lugar_numero": p.lugar_numero,
            "usuario_id": p.usuario_id,
            "status": p.status,
            "forma_pagamento_id": p.forma_pagamento_id,
            "valor_total": float(p.valor_total or 0),
            "valor_recebido": float(p.valor_recebido or 0),
            "troco": float(p.troco or 0),
            "observacao_cozinha": p.observacao_cozinha,
            "data_inicio": p.data_inicio.isoformat() if p.data_inicio else None,
            "data_fechamento": p.data_fechamento.isoformat() if p.data_fechamento else None,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "itens": [
                {
                    "uuid": it.uuid,
                    "produto_id": it.produto_id,
                    "produto_codigo": (it.produto.codigo if getattr(it, "produto", None) is not None else None),
                    "produto_nome": (it.produto.nome if getattr(it, "produto", None) is not None else None),
                    "produto_preco_venda": (
                        float(it.produto.preco_venda or 0) if getattr(it, "produto", None) is not None else None
                    ),
                    "quantidade": it.quantidade,
                    "preco_unitario": float(it.preco_unitario or 0),
                    "observacao": it.observacao,
                    "created_at": it.created_at.isoformat() if it.created_at else None,
                    "updated_at": it.updated_at.isoformat() if it.updated_at else None,
                }
                for it in (p.itens or [])
            ],
        }

    return {
        "server_now": _utc_now_iso(),
        "pedidos": [serialize(p) for p in pedidos],
    }
