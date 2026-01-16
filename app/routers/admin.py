from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
import uuid

from app.db.database import get_db_session
from app.core.deps import get_current_admin_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.post("/reset-dados")
async def reset_dados_online(
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    """Reseta TODO o banco de dados online, apagando todos os registros.

    Somente administradores podem executar esta operação.
    """
    tables_to_truncate = [
        "itens_venda",
        "vendas",
        "produtos",
        "clientes",
        "usuarios",
        "empresa_config",
    ]

    try:
        for table in tables_to_truncate:
            await db.execute(
                text(f'TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;')
            )
        await db.commit()

        return {
            "status": "ok",
            "message": "Banco de dados online foi totalmente resetado (tabelas principais esvaziadas).",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao resetar banco de dados: {str(e)}",
        )


@router.post("/tenants/{tenant_id}/reset-dados")
async def reset_dados_tenant(
    tenant_id: str,
    db: AsyncSession = Depends(get_db_session),
    user=Depends(get_current_admin_user),
):
    try:
        tid = uuid.UUID(tenant_id)
    except Exception:
        raise HTTPException(status_code=400, detail="tenant_id inválido (UUID esperado)")

    exists = await db.execute(text("SELECT 1 FROM tenants WHERE id = :tid"), {"tid": str(tid)})
    if not exists.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    try:
        # Apagar dependências primeiro (mesmo se não tiverem tenant_id no model/tabela)
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_venda
                WHERE venda_id IN (
                    SELECT id FROM pdv.vendas WHERE tenant_id = :tid
                )
                """
            ),
            {"tid": str(tid)},
        )

        await db.execute(
            text(
                """
                DELETE FROM pdv.pagamentos_divida
                WHERE divida_id IN (
                    SELECT id FROM pdv.dividas WHERE tenant_id = :tid
                )
                """
            ),
            {"tid": str(tid)},
        )

        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_divida
                WHERE divida_id IN (
                    SELECT id FROM pdv.dividas WHERE tenant_id = :tid
                )
                """
            ),
            {"tid": str(tid)},
        )

        # Agora apagar tabelas principais do tenant
        await db.execute(text("DELETE FROM pdv.vendas WHERE tenant_id = :tid"), {"tid": str(tid)})
        await db.execute(text("DELETE FROM pdv.dividas WHERE tenant_id = :tid"), {"tid": str(tid)})
        await db.execute(text("DELETE FROM pdv.produtos WHERE tenant_id = :tid"), {"tid": str(tid)})
        await db.execute(text("DELETE FROM pdv.clientes WHERE tenant_id = :tid"), {"tid": str(tid)})
        await db.execute(text("DELETE FROM pdv.usuarios WHERE tenant_id = :tid"), {"tid": str(tid)})
        await db.execute(text("DELETE FROM pdv.empresa_config WHERE tenant_id = :tid"), {"tid": str(tid)})

        await db.commit()
        return {
            "status": "ok",
            "tenant_id": str(tid),
            "message": "Dados do tenant foram resetados.",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao resetar dados do tenant: {str(e)}")
