from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_admin_user, get_current_user, get_tenant_id
from app.db.database import get_db_session
from app.db.models import Produto, Venda, ItemVenda


router = APIRouter(prefix="/api/pedidos", tags=["pedidos"])


class PedidoListItem(BaseModel):
    pedido_uuid: str
    pedido_id: str
    tipo_pedido: Optional[str] = None
    status: str
    total: float
    usuario_nome: Optional[str] = None
    status_updated_by_nome: Optional[str] = None
    status_updated_at: Optional[datetime] = None
    mesa_id: Optional[int] = None
    lugar_numero: Optional[int] = None
    distancia_tipo: Optional[str] = None
    cliente_nome: Optional[str] = None
    cliente_telefone: Optional[str] = None
    endereco_entrega: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PedidoDetail(BaseModel):
    pedido_uuid: str
    pedido_id: str
    tipo_pedido: Optional[str] = None
    status: str
    total: float
    taxa_entrega: float = 0.0
    usuario_nome: Optional[str] = None
    mesa_id: Optional[int] = None
    lugar_numero: Optional[int] = None
    distancia_tipo: Optional[str] = None
    cliente_nome: Optional[str] = None
    cliente_telefone: Optional[str] = None
    endereco_entrega: Optional[str] = None
    observacoes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    itens: list[dict] = []


class PedidoStatusUpdate(BaseModel):
    status: str


class PedidoItemIn(BaseModel):
    produto_id: str
    quantidade: int


class PedidoCreateIn(BaseModel):
    mesa_id: int
    lugar_numero: Optional[int] = None
    cliente_id: Optional[str] = None
    observacoes: Optional[str] = None
    itens: list[PedidoItemIn]


class PedidoCreateOut(BaseModel):
    pedido_uuid: str
    pedido_id: str
    status: str


def _resolve_status(v: Venda) -> str:
    status_pedido = (getattr(v, "status_pedido", None) or "").strip()
    if status_pedido:
        return status_pedido
    if bool(getattr(v, "cancelada", False)):
        return "cancelado"
    fp = (getattr(v, "forma_pagamento", None) or "").strip().upper()
    if fp == "PENDENTE_PAGAMENTO":
        return "criado"
    return "aguardando_pagamento"


@router.post("/", response_model=PedidoCreateOut)
async def criar_pedido(
    payload: PedidoCreateIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_user),
):
    """Cria um pedido de mesa (restaurante).

    Observação: Persistimos em `vendas` para manter compatibilidade, mas não deve
    ser contabilizado como venda até `status_pedido='pago'`.
    """
    try:
        if not payload.itens:
            raise HTTPException(status_code=400, detail="Pedido sem itens")

        mesa_id = int(payload.mesa_id)
        if mesa_id <= 0:
            raise HTTPException(status_code=400, detail="mesa_id inválido")

        lugar = int(payload.lugar_numero) if payload.lugar_numero is not None else None
        if lugar is not None and lugar <= 0:
            raise HTTPException(status_code=400, detail="lugar_numero inválido")

        cliente_uuid = None
        if payload.cliente_id:
            try:
                cliente_uuid = uuid.UUID(str(payload.cliente_id))
            except Exception:
                cliente_uuid = None

        pedido_uuid = uuid.uuid4()
        v = Venda(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            usuario_id=getattr(user, "id", None),
            cliente_id=cliente_uuid,
            total=0.0,
            desconto=0.0,
            forma_pagamento="PENDENTE_PAGAMENTO",
            tipo_pedido="local",
            status_pedido="aberto",
            mesa_id=mesa_id,
            lugar_numero=lugar,
            observacoes=payload.observacoes,
            cancelada=False,
            created_at=datetime.utcnow(),
        )
        db.add(v)
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

            db.add(
                ItemVenda(
                    venda_id=v.id,
                    produto_id=produto_uuid,
                    quantidade=qtd,
                    peso_kg=0.0,
                    preco_unitario=preco_unit,
                    subtotal=subtotal,
                    taxa_iva=taxa_iva,
                    base_iva=base_iva,
                    valor_iva=valor_iva,
                )
            )
            total += subtotal

        v.total = float(total)
        await db.commit()
        return PedidoCreateOut(
            pedido_uuid=str(v.id),
            pedido_id=str(v.id)[:8],
            status=str(getattr(v, "status_pedido", None) or "aberto"),
        )
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar pedido: {str(e)}")


@router.get("/", response_model=list[PedidoListItem])
async def listar_pedidos(
    status_filter: Optional[str] = None,
    mesa_id: Optional[int] = None,
    incluir_cancelados: bool = False,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_user),
):
    limit = max(1, min(500, int(limit)))

    stmt = (
        select(Venda)
        .options(selectinload(Venda.usuario))
        .where(Venda.tenant_id == tenant_id)
        .order_by(Venda.created_at.desc())
        .limit(limit)
    )

    if not incluir_cancelados:
        stmt = stmt.where(Venda.cancelada == False)

    if mesa_id is not None:
        try:
            mid = int(mesa_id)
        except Exception:
            mid = None
        if mid is not None:
            stmt = stmt.where(Venda.mesa_id == mid)
    else:
        # Evitar misturar vendas de balcão com pedidos: pedidos de mesa sempre têm mesa_id > 0.
        stmt = stmt.where(Venda.mesa_id.is_not(None), Venda.mesa_id > 0)

    # Restaurante: pedidos são vendas com tipo_pedido preenchido ou mesa_id/lugar_numero.
    # Não aplicamos filtro rígido aqui para manter compatibilidade com mercearia, mas o PDV web pode filtrar.

    res = await db.execute(stmt)
    rows = res.scalars().all()

    out: list[PedidoListItem] = []
    for v in rows:
        s = _resolve_status(v)
        if status_filter and s.lower() != str(status_filter).strip().lower():
            continue
        out.append(
            PedidoListItem(
                pedido_uuid=str(v.id),
                pedido_id=str(v.id)[:8],
                tipo_pedido=getattr(v, "tipo_pedido", None),
                status=s,
                total=float(getattr(v, "total", 0.0) or 0.0),
                usuario_nome=getattr(getattr(v, "usuario", None), "nome", None),
                status_updated_by_nome=getattr(v, "status_updated_by_nome", None),
                status_updated_at=getattr(v, "status_updated_at", None),
                mesa_id=getattr(v, "mesa_id", None),
                lugar_numero=getattr(v, "lugar_numero", None),
                distancia_tipo=getattr(v, "distancia_tipo", None),
                cliente_nome=getattr(v, "cliente_nome", None),
                cliente_telefone=getattr(v, "cliente_telefone", None),
                endereco_entrega=getattr(v, "endereco_entrega", None),
                created_at=getattr(v, "created_at", None),
                updated_at=getattr(v, "updated_at", None) or getattr(v, "created_at", None),
            )
        )
    return out


@router.get("/uuid/{pedido_uuid}", response_model=PedidoDetail)
async def obter_pedido(
    pedido_uuid: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_user),
):
    try:
        vid = uuid.UUID(str(pedido_uuid))
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res = await db.execute(
        select(Venda)
        .options(selectinload(Venda.usuario))
        .where(Venda.id == vid, Venda.tenant_id == tenant_id)
    )
    v = res.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    res_itens = await db.execute(
        select(ItemVenda, Produto)
        .join(Produto, Produto.id == ItemVenda.produto_id)
        .where(ItemVenda.venda_id == v.id, Produto.tenant_id == tenant_id)
    )
    itens_rows = res_itens.all()
    itens_out = []
    for item, produto in itens_rows:
        itens_out.append(
            {
                "produto_id": str(getattr(produto, "id", "")),
                "produto_nome": str(getattr(produto, "nome", None) or getattr(produto, "descricao", None) or "Produto"),
                "quantidade": int(getattr(item, "quantidade", 0) or 0),
                "preco_unitario": float(getattr(item, "preco_unitario", 0.0) or 0.0),
                "subtotal": float(getattr(item, "subtotal", 0.0) or 0.0),
            }
        )

    taxa_entrega = float(getattr(v, "taxa_entrega", 0.0) or 0.0)
    total_base = float(getattr(v, "total", 0.0) or 0.0)

    updated_at = getattr(v, "updated_at", None) or getattr(v, "created_at", None)

    return PedidoDetail(
        pedido_uuid=str(v.id),
        pedido_id=str(v.id)[:8],
        tipo_pedido=getattr(v, "tipo_pedido", None),
        status=_resolve_status(v),
        total=total_base,
        taxa_entrega=taxa_entrega,
        usuario_nome=getattr(getattr(v, "usuario", None), "nome", None),
        status_updated_by_nome=getattr(v, "status_updated_by_nome", None),
        status_updated_at=getattr(v, "status_updated_at", None),
        mesa_id=getattr(v, "mesa_id", None),
        lugar_numero=getattr(v, "lugar_numero", None),
        distancia_tipo=getattr(v, "distancia_tipo", None),
        cliente_nome=getattr(v, "cliente_nome", None),
        cliente_telefone=getattr(v, "cliente_telefone", None),
        endereco_entrega=getattr(v, "endereco_entrega", None),
        observacoes=getattr(v, "observacoes", None),
        created_at=getattr(v, "created_at", None),
        updated_at=updated_at,
        itens=itens_out,
    )


@router.put("/uuid/{pedido_uuid}/status")
async def atualizar_status_pedido(
    pedido_uuid: str,
    payload: PedidoStatusUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    novo = (payload.status or "").strip().lower()
    if not novo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status é obrigatório")

    try:
        vid = uuid.UUID(str(pedido_uuid))
    except Exception:
        raise HTTPException(status_code=400, detail="pedido_uuid inválido")

    res = await db.execute(select(Venda).where(Venda.id == vid, Venda.tenant_id == tenant_id))
    v = res.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    v.status_pedido = novo
    try:
        v.status_updated_by_nome = str(getattr(user, "nome", None) or getattr(user, "usuario", None) or "") or None
    except Exception:
        v.status_updated_by_nome = None
    v.status_updated_at = datetime.utcnow()
    if novo in ("cancelado", "cancelada"):
        v.cancelada = True
    await db.commit()

    return {"ok": True, "pedido_uuid": str(v.id), "status": novo}
