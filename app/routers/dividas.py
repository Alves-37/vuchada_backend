from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
import uuid

from app.db.database import get_db_session
from app.db.models import Divida, ItemDivida, PagamentoDivida, Produto, Cliente, User
from app.core.deps import get_tenant_id


router = APIRouter(prefix="/api/dividas", tags=["dividas"])


class ItemDividaIn(BaseModel):
    produto_id: str
    quantidade: float
    preco_unitario: float
    subtotal: float


class DividaCreate(BaseModel):
    id_local: Optional[int] = None
    cliente_id: Optional[str] = None
    usuario_id: Optional[str] = None
    observacao: Optional[str] = None
    desconto_aplicado: float = 0.0
    percentual_desconto: float = 0.0
    itens: List[ItemDividaIn]


class PagamentoDividaIn(BaseModel):
    valor: float
    forma_pagamento: str
    usuario_id: Optional[str] = None


class DividaSyncRequest(BaseModel):
    """Payload para sincronização em lote de dívidas.

    Mantém o mesmo formato de DividaCreate, mas em lista no campo data,
    para permitir uso por ferramentas de sync genéricas.
    """
    data: List[DividaCreate]


class DividaOut(BaseModel):
    id: str
    id_local: Optional[int]
    cliente_id: Optional[str]
    usuario_id: Optional[str]
    cliente_nome: Optional[str] = None
    data_divida: str
    valor_total: float
    valor_original: float
    desconto_aplicado: float
    percentual_desconto: float
    valor_pago: float
    status: str
    observacao: Optional[str] = None

    class Config:
        from_attributes = True


def _parse_uuid(value: Optional[str]) -> Optional[uuid.UUID]:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


@router.post("/", response_model=DividaOut, status_code=201)
async def criar_divida(
    payload: DividaCreate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Cria uma nova dívida com itens, alinhada ao modelo local do PDV3."""
    if not payload.itens:
        raise HTTPException(status_code=400, detail="É necessário informar pelo menos um item na dívida.")

    try:
        # Converter IDs para UUIDs
        cliente_uuid = _parse_uuid(payload.cliente_id)
        usuario_uuid = _parse_uuid(payload.usuario_id)

        # Calcular valores
        valor_original = sum(float(i.subtotal) for i in payload.itens)
        desconto_aplicado = float(payload.desconto_aplicado or 0.0)
        if payload.percentual_desconto and payload.percentual_desconto > 0:
            desconto_aplicado = valor_original * (float(payload.percentual_desconto) / 100.0)
        valor_total = max(0.0, valor_original - desconto_aplicado)

        nova_divida = Divida(
            tenant_id=tenant_id,
            id_local=payload.id_local,
            cliente_id=cliente_uuid,
            usuario_id=usuario_uuid,
            valor_total=valor_total,
            valor_original=valor_original,
            desconto_aplicado=desconto_aplicado,
            percentual_desconto=float(payload.percentual_desconto or 0.0),
            valor_pago=0.0,
            status="Pendente",
            observacao=payload.observacao,
        )

        db.add(nova_divida)
        await db.flush()  # obter ID

        # Criar itens da dívida
        for item in payload.itens:
            produto_uuid = _parse_uuid(item.produto_id)
            if not produto_uuid:
                raise HTTPException(status_code=400, detail=f"produto_id inválido: {item.produto_id}")

            # Verificar se produto existe
            result_prod = await db.execute(
                select(Produto).where(
                    Produto.id == produto_uuid,
                    Produto.tenant_id == tenant_id,
                )
            )
            if not result_prod.scalar_one_or_none():
                raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {item.produto_id}")

            db.add(
                ItemDivida(
                    divida_id=nova_divida.id,
                    produto_id=produto_uuid,
                    quantidade=float(item.quantidade),
                    preco_unitario=float(item.preco_unitario),
                    subtotal=float(item.subtotal),
                )
            )

        await db.commit()
        await db.refresh(nova_divida)

        # Injetar nome do cliente, se carregado
        try:
            setattr(nova_divida, 'cliente_nome', getattr(getattr(nova_divida, 'cliente', None), 'nome', None))
        except Exception:
            setattr(nova_divida, 'cliente_nome', None)

        return DividaOut.model_validate(nova_divida)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar dívida: {str(e)}")


@router.post("/sync")
async def sync_dividas(
    payload: DividaSyncRequest,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Sincroniza dívidas em lote a partir do PDV, usando id_local como chave.

    Para cada registro em payload.data:
    - Se existir uma dívida com mesmo id_local, é ignorada (idempotente).
    - Caso contrário, é criada usando a mesma lógica da rota criar_divida.
    """
    if not payload.data:
        return {"status": "ok", "created": 0, "skipped": 0, "errors": []}

    created = 0
    skipped = 0
    errors: List[dict] = []

    for idx, item in enumerate(payload.data):
        try:
            # Verificar se já existe dívida com mesmo id_local
            if item.id_local is not None:
                stmt = select(Divida).where(
                    Divida.id_local == item.id_local,
                    Divida.tenant_id == tenant_id,
                )
                result = await db.execute(stmt)
                existente = result.scalar_one_or_none()
                if existente:
                    skipped += 1
                    continue

            # Reusar lógica básica de criação (sem duplicar validações de forma exata)
            if not item.itens:
                raise HTTPException(status_code=400, detail="Dívida sem itens não pode ser sincronizada.")

            cliente_uuid = _parse_uuid(item.cliente_id)
            usuario_uuid = _parse_uuid(item.usuario_id)

            valor_original = sum(float(i.subtotal) for i in item.itens)
            desconto_aplicado = float(item.desconto_aplicado or 0.0)
            if item.percentual_desconto and item.percentual_desconto > 0:
                desconto_aplicado = valor_original * (float(item.percentual_desconto) / 100.0)
            valor_total = max(0.0, valor_original - desconto_aplicado)

            nova_divida = Divida(
                tenant_id=tenant_id,
                id_local=item.id_local,
                cliente_id=cliente_uuid,
                usuario_id=usuario_uuid,
                valor_total=valor_total,
                valor_original=valor_original,
                desconto_aplicado=desconto_aplicado,
                percentual_desconto=float(item.percentual_desconto or 0.0),
                valor_pago=0.0,
                status="Pendente",
                observacao=item.observacao,
            )

            db.add(nova_divida)
            await db.flush()

            # Criar itens associados
            for it in item.itens:
                prod_uuid = _parse_uuid(it.produto_id)
                if not prod_uuid:
                    raise HTTPException(status_code=400, detail=f"produto_id inválido: {it.produto_id}")

                result_prod = await db.execute(
                    select(Produto).where(
                        Produto.id == prod_uuid,
                        Produto.tenant_id == tenant_id,
                    )
                )
                if not result_prod.scalar_one_or_none():
                    raise HTTPException(status_code=400, detail=f"Produto inexistente no servidor: {it.produto_id}")

                db.add(
                    ItemDivida(
                        divida_id=nova_divida.id,
                        produto_id=prod_uuid,
                        quantidade=float(it.quantidade),
                        preco_unitario=float(it.preco_unitario),
                        subtotal=float(it.subtotal),
                    )
                )

            created += 1
        except HTTPException as he:
            # Erro específico deste registro; acumular mas continuar os demais
            errors.append({
                "index": idx,
                "id_local": item.id_local,
                "detail": he.detail,
            })
            await db.rollback()
        except Exception as ex:
            errors.append({
                "index": idx,
                "id_local": item.id_local,
                "detail": str(ex),
            })
            await db.rollback()

    # Commit uma vez ao final para as dívidas bem sucedidas
    try:
        await db.commit()
    except Exception as ex:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao finalizar sync de dívidas: {str(ex)}")

    return {
        "status": "ok" if not errors else "partial",
        "created": created,
        "skipped": skipped,
        "errors": errors,
    }


@router.get("/abertas", response_model=List[DividaOut])
async def listar_dividas_abertas(
    cliente_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Lista dívidas com status diferente de 'Quitado', opcionalmente filtrando por cliente."""
    try:
        # Join com Cliente para obter nome
        stmt = (
            select(Divida, Cliente.nome.label("cliente_nome"))
            .join(Cliente, Divida.cliente_id == Cliente.id, isouter=True)
            .where(
                Divida.status != "Quitado",
                Divida.tenant_id == tenant_id,
            )
        )

        cliente_uuid = _parse_uuid(cliente_id)
        if cliente_uuid:
            stmt = stmt.where(Divida.cliente_id == cliente_uuid)

        result = await db.execute(stmt.order_by(Divida.data_divida.desc()))
        rows = result.all()

        resposta: list[DividaOut] = []
        for divida, cli_nome in rows:
            try:
                setattr(divida, 'cliente_nome', cli_nome)
            except Exception:
                setattr(divida, 'cliente_nome', None)
            resposta.append(DividaOut.model_validate(divida))
        return resposta
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar dívidas: {str(e)}")


@router.post("/{divida_id}/pagamentos", response_model=DividaOut)
async def registrar_pagamento_divida(
    divida_id: str,
    payload: PagamentoDividaIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Registra um pagamento (parcial ou total) para uma dívida existente."""
    if payload.valor <= 0:
        raise HTTPException(status_code=400, detail="Valor do pagamento deve ser maior que zero.")

    try:
        divida_uuid = _parse_uuid(divida_id)
        if not divida_uuid:
            raise HTTPException(status_code=400, detail="ID de dívida inválido.")

        result = await db.execute(
            select(Divida).where(
                Divida.id == divida_uuid,
                Divida.tenant_id == tenant_id,
            )
        )
        divida = result.scalar_one_or_none()
        if not divida:
            raise HTTPException(status_code=404, detail="Dívida não encontrada.")

        # Registrar pagamento
        usuario_uuid = _parse_uuid(payload.usuario_id)
        pagamento = PagamentoDivida(
            divida_id=divida.id,
            valor=float(payload.valor),
            forma_pagamento=payload.forma_pagamento,
            usuario_id=usuario_uuid,
        )
        db.add(pagamento)

        # Atualizar valores agregados da dívida
        novo_valor_pago = float(divida.valor_pago or 0.0) + float(payload.valor)
        divida.valor_pago = novo_valor_pago
        if novo_valor_pago >= float(divida.valor_total) - 0.01:
            divida.status = "Quitado"
        else:
            divida.status = "Parcial"

        await db.commit()
        await db.refresh(divida)

        # Injetar nome do cliente, se disponível
        try:
            setattr(divida, 'cliente_nome', getattr(getattr(divida, 'cliente', None), 'nome', None))
        except Exception:
            setattr(divida, 'cliente_nome', None)

        return DividaOut.model_validate(divida)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao registrar pagamento da dívida: {str(e)}")
