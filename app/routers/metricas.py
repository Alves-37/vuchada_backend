from fastapi import APIRouter, HTTPException, Depends
from fastapi import Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, or_
from datetime import datetime, date
import asyncio
import uuid

from ..db.database import get_db_session
from ..db.models import Venda, ItemVenda, Produto
from ..core.deps import get_tenant_id

router = APIRouter(prefix="/api/metricas", tags=["metricas"]) 

# Cache em memória simples para suavizar cold start (TTL curto)
_metrics_cache = {}
_cache_ttl_seconds = 15
_cache_lock = asyncio.Lock()

def _now_ts() -> float:
    return datetime.utcnow().timestamp()

def _cache_get(key: str):
    entry = _metrics_cache.get(key)
    if not entry:
        return None
    if (_now_ts() - float(entry.get("ts", 0.0))) < _cache_ttl_seconds:
        return entry.get("value")
    return None

def _cache_set(key: str, value):
    _metrics_cache[key] = {"value": value, "ts": _now_ts()}

@router.get("/vendas-dia")
async def vendas_dia(
    data: str | None = Query(default=None, description="Data no formato YYYY-MM-DD (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Retorna o total de vendas (não canceladas) do dia informado (ou dia atual)."""
    # Data alvo: usar a recebida do cliente ou o dia do servidor
    try:
        alvo = date.fromisoformat(data) if data else date.today()
    except Exception:
        alvo = date.today()
    cache_key = f"vendas_dia:{tenant_id}:{alvo.isoformat()}"
    async with _cache_lock:
        cached = _cache_get(cache_key)
        if cached is not None:
            return {"data": str(alvo), "total": float(cached)}

    stmt = select(func.coalesce(func.sum(Venda.total), 0.0)).where(
        Venda.cancelada == False,
        func.date(Venda.created_at) == alvo,
        Venda.tenant_id == tenant_id,
        or_(
            Venda.status_pedido.is_(None),
            func.lower(func.coalesce(Venda.status_pedido, "")) == "pago",
        ),
    )
    # Tentativa principal + 1 retry leve
    for attempt in range(2):
        try:
            result = await db.execute(stmt)
            total = float(result.scalar() or 0.0)
            async with _cache_lock:
                _cache_set(cache_key, total)
            return {"data": str(alvo), "total": total}
        except Exception:
            if attempt == 0:
                await asyncio.sleep(0.2)
                continue
            # Fallback: servir cache antigo se existir, senão 0
            async with _cache_lock:
                cached = _metrics_cache.get(cache_key, {}).get("value")
            return {"data": str(alvo), "total": float(cached or 0.0), "warning": "cached"}

@router.get("/vendas-mes")
async def vendas_mes(
    ano_mes: str | None = Query(default=None, description="Ano-mês no formato YYYY-MM (timezone do cliente)"),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Retorna o total de vendas (não canceladas) do mês informado (ou mês atual)."""
    try:
        if ano_mes:
            try:
                ano, mes = map(int, ano_mes.split("-"))
                primeiro_dia = date(ano, mes, 1)
            except Exception:
                dnow = datetime.utcnow()
                primeiro_dia = date(dnow.year, dnow.month, 1)
        else:
            dnow = datetime.utcnow()
            primeiro_dia = date(dnow.year, dnow.month, 1)
        # próximo mês
        proximo_mes = date(
            primeiro_dia.year + (1 if primeiro_dia.month == 12 else 0),
            1 if primeiro_dia.month == 12 else primeiro_dia.month + 1,
            1
        )
        # Intervalo [primeiro_dia, proximo_mes)
        stmt = select(func.coalesce(func.sum(Venda.total), 0.0)).where(
            Venda.cancelada == False,
            Venda.created_at >= primeiro_dia,
            Venda.created_at < proximo_mes,
            Venda.tenant_id == tenant_id,
            or_(
                Venda.status_pedido.is_(None),
                func.lower(func.coalesce(Venda.status_pedido, "")) == "pago",
            ),
        )
        cache_key = f"vendas_mes:{tenant_id}:{primeiro_dia.strftime('%Y-%m')}"
        # Servir cache se fresco
        async with _cache_lock:
            cached = _cache_get(cache_key)
            if cached is not None:
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": float(cached)}

        for attempt in range(2):
            try:
                result = await db.execute(stmt)
                total = float(result.scalar() or 0.0)
                async with _cache_lock:
                    _cache_set(cache_key, total)
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": total}
            except Exception:
                if attempt == 0:
                    await asyncio.sleep(0.2)
                    continue
                async with _cache_lock:
                    cached = _metrics_cache.get(cache_key, {}).get("value")
                return {"ano_mes": primeiro_dia.strftime("%Y-%m"), "total": float(cached or 0.0), "warning": "cached"}
    except Exception:
        # Falha inesperada: retornar cache ou zero
        async with _cache_lock:
            cached = _metrics_cache.get(f"vendas_mes:{tenant_id}:{datetime.utcnow().strftime('%Y-%m')}", {}).get("value")
        return {"ano_mes": datetime.utcnow().strftime("%Y-%m"), "total": float(cached or 0.0), "warning": "cached"}

@router.get("/estoque")
async def metricas_estoque(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Retorna métricas de estoque: valor_estoque (custo), valor_potencial (venda) e lucro_potencial.

    Fórmulas:
    - valor_estoque = SUM(estoque * preco_custo) WHERE ativo = true
    - valor_potencial = SUM(estoque * preco_venda) WHERE ativo = true
    - lucro_potencial = valor_potencial - valor_estoque
    """
    try:
        stmt_custo = select(func.coalesce(func.sum(Produto.estoque * Produto.preco_custo), 0.0)).where(
            Produto.ativo == True,
            Produto.tenant_id == tenant_id,
        )
        stmt_venda = select(func.coalesce(func.sum(Produto.estoque * Produto.preco_venda), 0.0)).where(
            Produto.ativo == True,
            Produto.tenant_id == tenant_id,
        )

        result_custo = await db.execute(stmt_custo)
        result_venda = await db.execute(stmt_venda)

        valor_estoque = float(result_custo.scalar() or 0.0)
        valor_potencial = float(result_venda.scalar() or 0.0)
        lucro_potencial = float(valor_potencial - valor_estoque)

        return {
            "valor_estoque": valor_estoque,
            "valor_potencial": valor_potencial,
            "lucro_potencial": lucro_potencial,
        }
    except Exception as e:
        # Em falha, retornar zeros para não quebrar o cliente
        return {
            "valor_estoque": 0.0,
            "valor_potencial": 0.0,
            "lucro_potencial": 0.0,
            "warning": str(e)
        }
