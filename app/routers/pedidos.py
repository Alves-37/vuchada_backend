from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app import models
from app import schemas

router = APIRouter(prefix="/pedidos", tags=["pedidos"])


@router.get("/", response_model=list[schemas.PedidoOut])
def listar_pedidos(
    abertos: bool | None = None,
    mesa_id: int | None = None,
    origem: str | None = None,
    since_id: int | None = None,
    limit: int | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(models.Pedido).options(joinedload(models.Pedido.itens), joinedload(models.Pedido.mesa))

    if abertos is True:
        q = q.filter(models.Pedido.data_fechamento.is_(None))
    elif abertos is False:
        q = q.filter(models.Pedido.data_fechamento.is_not(None))

    if mesa_id is not None:
        q = q.filter(models.Pedido.mesa_id == mesa_id)

    if origem is not None:
        q = q.filter(models.Pedido.origem == origem)

    if since_id is not None:
        q = q.filter(models.Pedido.id > since_id)

    q = q.order_by(models.Pedido.id.desc())

    if limit is not None:
        limit = max(1, min(int(limit), 500))
        q = q.limit(limit)

    return q.all()


@router.get("/{pedido_id}", response_model=schemas.PedidoOut)
def obter_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = (
        db.query(models.Pedido)
        .options(joinedload(models.Pedido.itens), joinedload(models.Pedido.mesa))
        .filter(models.Pedido.id == pedido_id)
        .first()
    )
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return pedido


@router.post("/", response_model=schemas.PedidoOut)
def criar_pedido(payload: schemas.PedidoCreate, db: Session = Depends(get_db)):
    mesa = db.get(models.Mesa, payload.mesa_id)
    if not mesa:
        raise HTTPException(status_code=400, detail="Mesa inválida")

    pedido = models.Pedido(
        mesa_id=payload.mesa_id,
        lugar_numero=payload.lugar_numero,
        usuario_id=payload.usuario_id,
        status=payload.status,
        forma_pagamento_id=payload.forma_pagamento_id,
        valor_total=payload.valor_total,
        valor_recebido=payload.valor_recebido,
        troco=payload.troco,
        observacao_cozinha=payload.observacao_cozinha,
        origem="pdv",
        data_inicio=payload.data_inicio or datetime.utcnow(),
        data_fechamento=payload.data_fechamento,
    )

    for item in payload.itens:
        produto = db.get(models.Produto, item.produto_id)
        if not produto:
            raise HTTPException(status_code=400, detail=f"Produto inválido: {item.produto_id}")

        pedido.itens.append(
            models.ItemPedido(
                produto_id=item.produto_id,
                quantidade=item.quantidade,
                preco_unitario=item.preco_unitario,
                observacao=item.observacao,
            )
        )

    db.add(pedido)
    db.commit()
    db.refresh(pedido)

    pedido = (
        db.query(models.Pedido)
        .options(joinedload(models.Pedido.itens))
        .filter(models.Pedido.id == pedido.id)
        .first()
    )
    return pedido


@router.put("/{pedido_id}", response_model=schemas.PedidoOut)
def atualizar_pedido(pedido_id: int, payload: schemas.PedidoUpdate, db: Session = Depends(get_db)):
    pedido = db.get(models.Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    data = payload.model_dump(exclude_unset=True)

    if "mesa_id" in data:
        mesa = db.get(models.Mesa, data["mesa_id"])
        if not mesa:
            raise HTTPException(status_code=400, detail="Mesa inválida")

    for k, v in data.items():
        setattr(pedido, k, v)

    db.commit()

    pedido = (
        db.query(models.Pedido)
        .options(joinedload(models.Pedido.itens))
        .filter(models.Pedido.id == pedido_id)
        .first()
    )
    return pedido


@router.post("/{pedido_id}/fechar", response_model=schemas.PedidoOut)
def fechar_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.get(models.Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    pedido.data_fechamento = datetime.utcnow()
    if (pedido.status or "").lower() == "aberto":
        pedido.status = "pago"

    db.commit()

    pedido = (
        db.query(models.Pedido)
        .options(joinedload(models.Pedido.itens))
        .filter(models.Pedido.id == pedido_id)
        .first()
    )
    return pedido


@router.delete("/{pedido_id}")
def excluir_pedido(pedido_id: int, db: Session = Depends(get_db)):
    pedido = db.get(models.Pedido, pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    db.delete(pedido)
    db.commit()
    return {"ok": True}
