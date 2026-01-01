from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app import models, schemas
from app.db import get_db
from app.settings import settings

router = APIRouter(prefix="/public", tags=["public"])


@router.get("/menu/produtos", response_model=list[schemas.ProdutoOut])
def menu_produtos(
    q: str | None = None,
    somente_disponiveis: bool = True,
    db: Session = Depends(get_db),
):
    query = db.query(models.Produto).filter(models.Produto.ativo.is_(True))

    if somente_disponiveis:
        query = query.filter(models.Produto.estoque > 0)

    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(models.Produto.nome.ilike(like), models.Produto.codigo.ilike(like)))

    return query.order_by(models.Produto.nome.asc()).all()


@router.post("/pedidos", response_model=schemas.PublicPedidoOut)
def criar_pedido_publico(
    payload: schemas.PublicPedidoCreate,
    db: Session = Depends(get_db),
    x_kiosk_pin: str | None = Header(default=None, alias="X-Kiosk-Pin"),
):
    if not payload.itens:
        raise HTTPException(status_code=400, detail="Carrinho vazio")

    # Kiosk protection:
    # - Orders by mesa_numero/mesa_id are allowed only with correct PIN (tablet mode).
    # - Orders by mesa_token (QR) do not require PIN.
    if payload.mesa_token is None:
        if settings.kiosk_pin:
            if x_kiosk_pin != settings.kiosk_pin:
                raise HTTPException(status_code=401, detail="PIN do tablet inválido")

    # Resolver mesa
    mesa = None
    if payload.mesa_id is not None:
        mesa = db.get(models.Mesa, payload.mesa_id)
    elif payload.mesa_numero is not None:
        mesa = db.query(models.Mesa).filter(models.Mesa.numero == payload.mesa_numero).first()
    elif payload.mesa_token is not None:
        mesa = db.query(models.Mesa).filter(models.Mesa.mesa_token == payload.mesa_token).first()

    if not mesa:
        raise HTTPException(status_code=400, detail="Mesa inválida")

    # Transação: valida estoque e baixa estoque
    try:
        total = 0

        pedido = models.Pedido(
            mesa_id=mesa.id,
            lugar_numero=payload.lugar_numero,
            usuario_id=None,
            status="aberto",
            forma_pagamento_id=None,
            valor_total=0,
            valor_recebido=0,
            troco=0,
            observacao_cozinha=payload.observacao_cozinha,
            origem="online",
            data_inicio=datetime.utcnow(),
            data_fechamento=None,
        )

        for item in payload.itens:
            if item.quantidade <= 0:
                raise HTTPException(status_code=400, detail="Quantidade inválida")

            produto = (
                db.query(models.Produto)
                .filter(models.Produto.id == item.produto_id, models.Produto.ativo.is_(True))
                .with_for_update()
                .first()
            )
            if not produto:
                raise HTTPException(status_code=400, detail=f"Produto inválido/inativo: {item.produto_id}")

            if produto.estoque is None or produto.estoque < item.quantidade:
                raise HTTPException(
                    status_code=409,
                    detail=f"Estoque insuficiente para {produto.nome}. Disponível: {produto.estoque}",
                )

            # Baixar estoque na criação do pedido
            produto.estoque -= item.quantidade

            preco_unitario = float(produto.preco_venda)
            total += float(item.quantidade) * preco_unitario

            pedido.itens.append(
                models.ItemPedido(
                    produto_id=produto.id,
                    quantidade=item.quantidade,
                    preco_unitario=preco_unitario,
                    observacao=item.observacao,
                )
            )

        pedido.valor_total = total

        db.add(pedido)
        db.commit()
        db.refresh(pedido)

        pedido = (
            db.query(models.Pedido)
            .options(joinedload(models.Pedido.itens))
            .filter(models.Pedido.id == pedido.id)
            .first()
        )

        return schemas.PublicPedidoOut(
            pedido_id=pedido.id,
            status=pedido.status,
            valor_total=pedido.valor_total,
            mesa_id=pedido.mesa_id,
            lugar_numero=pedido.lugar_numero,
            created_at=pedido.created_at,
        )

    except HTTPException:
        db.rollback()
        raise
    except Exception as ex:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar pedido: {ex}")


@router.get("/mesa/{mesa_token}/menu/produtos", response_model=list[schemas.ProdutoOut])
def menu_produtos_por_qr(
    mesa_token: str,
    q: str | None = None,
    somente_disponiveis: bool = True,
    db: Session = Depends(get_db),
):
    mesa = db.query(models.Mesa).filter(models.Mesa.mesa_token == mesa_token).first()
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    return menu_produtos(q=q, somente_disponiveis=somente_disponiveis, db=db)


@router.post("/mesa/{mesa_token}/pedidos", response_model=schemas.PublicPedidoOut)
def criar_pedido_por_qr(
    mesa_token: str,
    payload: schemas.PublicPedidoCreate,
    db: Session = Depends(get_db),
):
    # Força a mesa via token do path
    payload = payload.model_copy(update={"mesa_token": mesa_token, "mesa_id": None, "mesa_numero": None})
    return criar_pedido_publico(payload=payload, db=db)


@router.get("/pedidos/{pedido_id}", response_model=schemas.PedidoOut)
def consultar_pedido_publico(pedido_id: int, db: Session = Depends(get_db)):
    pedido = (
        db.query(models.Pedido)
        .options(joinedload(models.Pedido.itens))
        .filter(models.Pedido.id == pedido_id)
        .first()
    )
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")
    return pedido
