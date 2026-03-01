from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, text
from sqlalchemy.orm import selectinload
import uuid
from datetime import date, datetime

from app.db.database import get_db_session
from app.core.deps import get_current_admin_user, get_tenant_id
from app.db.models import ItemVenda, TenantBackup, Venda, VendaBackup

router = APIRouter(prefix="/api/admin", tags=["admin"])


class VendaBackupCreateIn(BaseModel):
    nome: str | None = None


class VendaBackupOut(BaseModel):
    id: uuid.UUID
    nome: str
    created_at: str


class VendaBackupDetailOut(BaseModel):
    id: uuid.UUID
    nome: str
    created_at: str
    snapshot: dict


class TenantBackupCreateIn(BaseModel):
    nome: str | None = None


class TenantBackupOut(BaseModel):
    id: uuid.UUID
    nome: str
    created_at: str


@router.post("/reset-dados")
async def reset_dados_online(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        # Reset completo do tenant (preserva backups)
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_venda
                WHERE venda_id IN (SELECT id FROM pdv.vendas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.vendas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(
            text(
                """
                DELETE FROM pdv.pagamentos_divida
                WHERE divida_id IN (SELECT id FROM pdv.dividas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_divida
                WHERE divida_id IN (SELECT id FROM pdv.dividas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.dividas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(
            text(
                """
                DELETE FROM pdv.turno_membros
                WHERE turno_id IN (SELECT id FROM pdv.turnos WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.turnos WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(text("DELETE FROM pdv.payment_transactions WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.mesas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.produtos WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.clientes WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.usuarios WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.empresa_config WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.commit()

        return {
            "status": "ok",
            "message": "Dados do tenant foram resetados (backups mantidos).",
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Erro ao resetar banco de dados: {str(e)}",
        )


@router.get("/vendas-backups", response_model=list[VendaBackupOut])
async def listar_backups_vendas(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res = await db.execute(
        select(VendaBackup)
        .where(VendaBackup.tenant_id == tenant_id)
        .order_by(VendaBackup.created_at.desc())
        .limit(200)
    )
    rows = res.scalars().all()
    return [
        VendaBackupOut(
            id=getattr(b, "id"),
            nome=str(getattr(b, "nome")),
            created_at=str(getattr(b, "created_at")),
        )
        for b in rows
    ]


@router.post("/vendas-backups", response_model=VendaBackupOut)
async def criar_backup_vendas(
    payload: VendaBackupCreateIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res_v = await db.execute(
        select(Venda)
        .options(selectinload(Venda.itens))
        .where(Venda.tenant_id == tenant_id)
        .order_by(Venda.created_at.asc())
        .limit(5000)
    )
    vendas_rows = res_v.scalars().all()

    vendas: list[dict] = []
    itens: list[dict] = []
    for v in vendas_rows:
        vendas.append(
            {
                "id": str(getattr(v, "id")),
                "created_at": str(getattr(v, "created_at")),
                "updated_at": str(getattr(v, "updated_at")),
                "tenant_id": str(getattr(v, "tenant_id")) if getattr(v, "tenant_id", None) else None,
                "usuario_id": str(getattr(v, "usuario_id")) if getattr(v, "usuario_id", None) else None,
                "cliente_id": str(getattr(v, "cliente_id")) if getattr(v, "cliente_id", None) else None,
                "total": float(getattr(v, "total")),
                "desconto": float(getattr(v, "desconto")),
                "forma_pagamento": str(getattr(v, "forma_pagamento")),
                "tipo_pedido": getattr(v, "tipo_pedido", None),
                "status_pedido": getattr(v, "status_pedido", None),
                "mesa_id": getattr(v, "mesa_id", None),
                "lugar_numero": getattr(v, "lugar_numero", None),
                "distancia_tipo": getattr(v, "distancia_tipo", None),
                "cliente_nome": getattr(v, "cliente_nome", None),
                "cliente_telefone": getattr(v, "cliente_telefone", None),
                "endereco_entrega": getattr(v, "endereco_entrega", None),
                "taxa_entrega": float(getattr(v, "taxa_entrega")),
                "observacoes": getattr(v, "observacoes", None),
                "status_updated_by_nome": getattr(v, "status_updated_by_nome", None),
                "status_updated_at": str(getattr(v, "status_updated_at")) if getattr(v, "status_updated_at", None) else None,
                "cancelada": bool(getattr(v, "cancelada")),
            }
        )
        for it in getattr(v, "itens", []) or []:
            itens.append(
                {
                    "id": str(getattr(it, "id")),
                    "created_at": str(getattr(it, "created_at")),
                    "updated_at": str(getattr(it, "updated_at")),
                    "venda_id": str(getattr(it, "venda_id")),
                    "produto_id": str(getattr(it, "produto_id")),
                    "quantidade": int(getattr(it, "quantidade")),
                    "peso_kg": float(getattr(it, "peso_kg")),
                    "preco_unitario": float(getattr(it, "preco_unitario")),
                    "subtotal": float(getattr(it, "subtotal")),
                    "taxa_iva": float(getattr(it, "taxa_iva")),
                    "base_iva": float(getattr(it, "base_iva")),
                    "valor_iva": float(getattr(it, "valor_iva")),
                }
            )

    nome = (payload.nome or "").strip() or f"Backup {len(vendas)} vendas"
    b = VendaBackup(tenant_id=tenant_id, nome=nome, snapshot={"vendas": vendas, "itens_venda": itens})
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return VendaBackupOut(id=getattr(b, "id"), nome=str(getattr(b, "nome")), created_at=str(getattr(b, "created_at")))


@router.get("/vendas-backups/{backup_id}", response_model=VendaBackupDetailOut)
async def obter_backup_vendas(
    backup_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        bid = uuid.UUID(backup_id)
    except Exception:
        raise HTTPException(status_code=400, detail="backup_id inválido (UUID esperado)")

    res = await db.execute(select(VendaBackup).where(VendaBackup.tenant_id == tenant_id, VendaBackup.id == bid))
    b = res.scalars().first()
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")

    return VendaBackupDetailOut(
        id=getattr(b, "id"),
        nome=str(getattr(b, "nome")),
        created_at=str(getattr(b, "created_at")),
        snapshot=getattr(b, "snapshot") or {},
    )


@router.post("/vendas-backups/{backup_id}/restaurar")
async def restaurar_backup_vendas(
    backup_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        bid = uuid.UUID(backup_id)
    except Exception:
        raise HTTPException(status_code=400, detail="backup_id inválido (UUID esperado)")

    res = await db.execute(select(VendaBackup).where(VendaBackup.tenant_id == tenant_id, VendaBackup.id == bid))
    b = res.scalars().first()
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")

    snapshot = getattr(b, "snapshot") or {}
    vendas = snapshot.get("vendas") or []
    itens = snapshot.get("itens_venda") or []

    try:
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_venda
                WHERE venda_id IN (
                    SELECT id FROM pdv.vendas WHERE tenant_id = :tid
                )
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.vendas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao limpar vendas atuais: {str(e)}")

    try:
        for v in vendas:
            db.add(
                Venda(
                    id=uuid.UUID(v["id"]),
                    tenant_id=tenant_id,
                    usuario_id=uuid.UUID(v["usuario_id"]) if v.get("usuario_id") else None,
                    cliente_id=uuid.UUID(v["cliente_id"]) if v.get("cliente_id") else None,
                    total=float(v.get("total") or 0),
                    desconto=float(v.get("desconto") or 0),
                    forma_pagamento=str(v.get("forma_pagamento") or ""),
                    tipo_pedido=v.get("tipo_pedido"),
                    status_pedido=v.get("status_pedido"),
                    mesa_id=v.get("mesa_id"),
                    lugar_numero=v.get("lugar_numero"),
                    distancia_tipo=v.get("distancia_tipo"),
                    cliente_nome=v.get("cliente_nome"),
                    cliente_telefone=v.get("cliente_telefone"),
                    endereco_entrega=v.get("endereco_entrega"),
                    taxa_entrega=float(v.get("taxa_entrega") or 0),
                    observacoes=v.get("observacoes"),
                    status_updated_by_nome=v.get("status_updated_by_nome"),
                    cancelada=bool(v.get("cancelada") or False),
                )
            )

        for it in itens:
            db.add(
                ItemVenda(
                    id=uuid.UUID(it["id"]),
                    venda_id=uuid.UUID(it["venda_id"]),
                    produto_id=uuid.UUID(it["produto_id"]),
                    quantidade=int(it.get("quantidade") or 0),
                    peso_kg=float(it.get("peso_kg") or 0),
                    preco_unitario=float(it.get("preco_unitario") or 0),
                    subtotal=float(it.get("subtotal") or 0),
                    taxa_iva=float(it.get("taxa_iva") or 0),
                    base_iva=float(it.get("base_iva") or 0),
                    valor_iva=float(it.get("valor_iva") or 0),
                )
            )

        await db.commit()
        return {"status": "ok", "message": "Backup restaurado."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao restaurar backup: {str(e)}")


@router.delete("/vendas-backups/{backup_id}")
async def apagar_backup_vendas(
    backup_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        bid = uuid.UUID(backup_id)
    except Exception:
        raise HTTPException(status_code=400, detail="backup_id inválido (UUID esperado)")

    res = await db.execute(select(VendaBackup).where(VendaBackup.tenant_id == tenant_id, VendaBackup.id == bid))
    b = res.scalars().first()
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")

    await db.execute(delete(VendaBackup).where(VendaBackup.tenant_id == tenant_id, VendaBackup.id == bid))
    await db.commit()
    return {"ok": True}


def _jsonable(v):
    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (int, float, bool, str)):
        return v
    try:
        return float(v)
    except Exception:
        pass
    return str(v)


async def _select_rows(db: AsyncSession, sql: str, params: dict) -> list[dict]:
    res = await db.execute(text(sql), params)
    rows = res.mappings().all()
    out: list[dict] = []
    for r in rows:
        out.append({k: _jsonable(v) for k, v in dict(r).items()})
    return out


@router.get("/tenant-backups", response_model=list[TenantBackupOut])
async def listar_backups_tenant(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res = await db.execute(
        select(TenantBackup)
        .where(TenantBackup.tenant_id == tenant_id)
        .order_by(TenantBackup.created_at.desc())
        .limit(200)
    )
    rows = res.scalars().all()
    return [
        TenantBackupOut(
            id=getattr(b, "id"),
            nome=str(getattr(b, "nome")),
            created_at=str(getattr(b, "created_at")),
        )
        for b in rows
    ]


@router.post("/tenant-backups", response_model=TenantBackupOut)
async def criar_backup_tenant(
    payload: TenantBackupCreateIn,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    params = {"tid": str(tenant_id)}

    snapshot: dict = {
        "usuarios": await _select_rows(db, "SELECT * FROM pdv.usuarios WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "produtos": await _select_rows(db, "SELECT * FROM pdv.produtos WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "clientes": await _select_rows(db, "SELECT * FROM pdv.clientes WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "mesas": await _select_rows(db, "SELECT * FROM pdv.mesas WHERE tenant_id = :tid ORDER BY numero ASC", params),
        "turnos": await _select_rows(db, "SELECT * FROM pdv.turnos WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "turno_membros": await _select_rows(
            db,
            """
            SELECT tm.*
            FROM pdv.turno_membros tm
            JOIN pdv.turnos t ON t.id = tm.turno_id
            WHERE t.tenant_id = :tid
            ORDER BY tm.created_at ASC
            """,
            params,
        ),
        "empresa_config": await _select_rows(db, "SELECT * FROM pdv.empresa_config WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "dividas": await _select_rows(db, "SELECT * FROM pdv.dividas WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "itens_divida": await _select_rows(
            db,
            """
            SELECT idv.*
            FROM pdv.itens_divida idv
            JOIN pdv.dividas d ON d.id = idv.divida_id
            WHERE d.tenant_id = :tid
            ORDER BY idv.created_at ASC
            """,
            params,
        ),
        "pagamentos_divida": await _select_rows(
            db,
            """
            SELECT pd.*
            FROM pdv.pagamentos_divida pd
            JOIN pdv.dividas d ON d.id = pd.divida_id
            WHERE d.tenant_id = :tid
            ORDER BY pd.created_at ASC
            """,
            params,
        ),
        "vendas": await _select_rows(db, "SELECT * FROM pdv.vendas WHERE tenant_id = :tid ORDER BY created_at ASC", params),
        "itens_venda": await _select_rows(
            db,
            """
            SELECT iv.*
            FROM pdv.itens_venda iv
            JOIN pdv.vendas v ON v.id = iv.venda_id
            WHERE v.tenant_id = :tid
            ORDER BY iv.created_at ASC
            """,
            params,
        ),
        "payment_transactions": await _select_rows(db, "SELECT * FROM pdv.payment_transactions WHERE tenant_id = :tid ORDER BY created_at ASC", params),
    }

    nome = (payload.nome or "").strip() or "Backup completo"
    b = TenantBackup(tenant_id=tenant_id, nome=nome, snapshot=snapshot)
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return TenantBackupOut(id=getattr(b, "id"), nome=str(getattr(b, "nome")), created_at=str(getattr(b, "created_at")))


async def _restore_table(db: AsyncSession, table: str, rows: list[dict]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    col_sql = ", ".join(cols)
    val_sql = ", ".join([f":{c}" for c in cols])
    stmt = text(f"INSERT INTO pdv.{table} ({col_sql}) VALUES ({val_sql})")
    for r in rows:
        await db.execute(stmt, r)


@router.post("/tenant-backups/{backup_id}/restaurar")
async def restaurar_backup_tenant(
    backup_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        bid = uuid.UUID(backup_id)
    except Exception:
        raise HTTPException(status_code=400, detail="backup_id inválido (UUID esperado)")

    res = await db.execute(select(TenantBackup).where(TenantBackup.tenant_id == tenant_id, TenantBackup.id == bid))
    b = res.scalars().first()
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")

    snapshot = getattr(b, "snapshot") or {}

    try:
        # Limpar dados atuais do tenant (não apaga backups)
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_venda
                WHERE venda_id IN (SELECT id FROM pdv.vendas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.vendas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(
            text(
                """
                DELETE FROM pdv.pagamentos_divida
                WHERE divida_id IN (SELECT id FROM pdv.dividas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(
            text(
                """
                DELETE FROM pdv.itens_divida
                WHERE divida_id IN (SELECT id FROM pdv.dividas WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.dividas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(
            text(
                """
                DELETE FROM pdv.turno_membros
                WHERE turno_id IN (SELECT id FROM pdv.turnos WHERE tenant_id = :tid)
                """
            ),
            {"tid": str(tenant_id)},
        )
        await db.execute(text("DELETE FROM pdv.turnos WHERE tenant_id = :tid"), {"tid": str(tenant_id)})

        await db.execute(text("DELETE FROM pdv.payment_transactions WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.mesas WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.produtos WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.clientes WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.usuarios WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.execute(text("DELETE FROM pdv.empresa_config WHERE tenant_id = :tid"), {"tid": str(tenant_id)})
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao limpar dados atuais: {str(e)}")

    try:
        # Restaurar na ordem correta
        await _restore_table(db, "usuarios", snapshot.get("usuarios") or [])
        await _restore_table(db, "clientes", snapshot.get("clientes") or [])
        await _restore_table(db, "produtos", snapshot.get("produtos") or [])
        await _restore_table(db, "mesas", snapshot.get("mesas") or [])
        await _restore_table(db, "turnos", snapshot.get("turnos") or [])
        await _restore_table(db, "turno_membros", snapshot.get("turno_membros") or [])
        await _restore_table(db, "empresa_config", snapshot.get("empresa_config") or [])
        await _restore_table(db, "dividas", snapshot.get("dividas") or [])
        await _restore_table(db, "itens_divida", snapshot.get("itens_divida") or [])
        await _restore_table(db, "pagamentos_divida", snapshot.get("pagamentos_divida") or [])
        await _restore_table(db, "vendas", snapshot.get("vendas") or [])
        await _restore_table(db, "itens_venda", snapshot.get("itens_venda") or [])
        await _restore_table(db, "payment_transactions", snapshot.get("payment_transactions") or [])
        await db.commit()
        return {"status": "ok", "message": "Backup completo restaurado."}
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao restaurar backup: {str(e)}")


@router.delete("/tenant-backups/{backup_id}")
async def apagar_backup_tenant(
    backup_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    try:
        bid = uuid.UUID(backup_id)
    except Exception:
        raise HTTPException(status_code=400, detail="backup_id inválido (UUID esperado)")

    res = await db.execute(select(TenantBackup).where(TenantBackup.tenant_id == tenant_id, TenantBackup.id == bid))
    b = res.scalars().first()
    if not b:
        raise HTTPException(status_code=404, detail="Backup não encontrado")

    await db.execute(delete(TenantBackup).where(TenantBackup.tenant_id == tenant_id, TenantBackup.id == bid))
    await db.commit()
    return {"ok": True}


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
