from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
import uuid

from app.db import get_db
from app import models
from app import schemas

router = APIRouter(prefix="/mesas", tags=["mesas"])


@router.get("/", response_model=list[schemas.MesaOut])
def listar_mesas(db: Session = Depends(get_db)):
    return db.query(models.Mesa).order_by(models.Mesa.numero.asc()).all()


@router.get("/disponibilidade", response_model=list[schemas.MesaDisponibilidadeOut])
def listar_mesas_disponibilidade(db: Session = Depends(get_db)):
    # Regra igual ao PDV: considera pedidos ativos quando data_fechamento IS NULL
    # e status não está em pago/cancelado/entregue.
    pedidos_ativos_expr = func.coalesce(func.count(models.Pedido.id), 0)

    query = (
        db.query(
            models.Mesa,
            pedidos_ativos_expr.label("pedidos_ativos"),
        )
        .outerjoin(
            models.Pedido,
            (models.Pedido.mesa_id == models.Mesa.id)
            & (models.Pedido.data_fechamento.is_(None))
            & (func.lower(models.Pedido.status).notin_(["pago", "cancelado", "entregue"])),
        )
        .group_by(models.Mesa.id)
        .order_by(models.Mesa.numero.asc())
    )

    result = []
    for mesa, pedidos_ativos in query.all():
        capacidade = int(mesa.capacidade or 0)
        pedidos_ativos = int(pedidos_ativos or 0)
        lugares_disponiveis = capacidade - pedidos_ativos

        result.append(
            schemas.MesaDisponibilidadeOut(
                **schemas.MesaOut.model_validate(mesa).model_dump(),
                pedidos_ativos=pedidos_ativos,
                lugares_disponiveis=lugares_disponiveis,
            )
        )

    return result


@router.get("/{mesa_id}", response_model=schemas.MesaOut)
def obter_mesa(mesa_id: int, db: Session = Depends(get_db)):
    mesa = db.get(models.Mesa, mesa_id)
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")
    return mesa


@router.get("/token/{mesa_token}", response_model=schemas.MesaOut)
def obter_mesa_por_token(mesa_token: str, db: Session = Depends(get_db)):
    mesa = db.query(models.Mesa).filter(models.Mesa.mesa_token == mesa_token).first()
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")
    return mesa


@router.post("/", response_model=schemas.MesaOut)
def criar_mesa(payload: schemas.MesaCreate, db: Session = Depends(get_db)):
    existente = db.query(models.Mesa).filter(models.Mesa.numero == payload.numero).first()
    if existente:
        raise HTTPException(status_code=409, detail="Já existe uma mesa com este número")

    mesa = models.Mesa(**payload.model_dump())
    if not getattr(mesa, "mesa_token", None):
        mesa.mesa_token = uuid.uuid4().hex
    db.add(mesa)
    db.commit()
    db.refresh(mesa)
    return mesa


@router.put("/{mesa_id}", response_model=schemas.MesaOut)
def atualizar_mesa(mesa_id: int, payload: schemas.MesaUpdate, db: Session = Depends(get_db)):
    mesa = db.get(models.Mesa, mesa_id)
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    data = payload.model_dump(exclude_unset=True)
    if "numero" in data:
        existente = (
            db.query(models.Mesa)
            .filter(models.Mesa.numero == data["numero"], models.Mesa.id != mesa_id)
            .first()
        )
        if existente:
            raise HTTPException(status_code=409, detail="Já existe uma mesa com este número")

    for k, v in data.items():
        setattr(mesa, k, v)

    db.commit()
    db.refresh(mesa)
    return mesa


@router.delete("/{mesa_id}")
def excluir_mesa(mesa_id: int, db: Session = Depends(get_db)):
    mesa = db.get(models.Mesa, mesa_id)
    if not mesa:
        raise HTTPException(status_code=404, detail="Mesa não encontrada")

    db.delete(mesa)
    db.commit()
    return {"ok": True}
