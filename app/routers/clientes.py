from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete
from typing import List
import uuid
from datetime import datetime

from ..db.database import get_db_session
from ..db.models import Cliente
from app.core.realtime import manager as realtime_manager
from ..schemas.cliente import ClienteCreate, ClienteUpdate, ClienteResponse
from app.core.deps import get_tenant_id

router = APIRouter(prefix="/api/clientes", tags=["clientes"])

@router.get("/", response_model=List[ClienteResponse])
async def listar_clientes(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Lista todos os clientes."""
    try:
        result = await db.execute(
            select(Cliente).where(
                Cliente.ativo == True,
                Cliente.tenant_id == tenant_id,
            )
        )
        clientes = result.scalars().all()
        return clientes
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao listar clientes: {str(e)}")

@router.get("/{cliente_id}", response_model=ClienteResponse)
async def obter_cliente(
    cliente_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Obtém um cliente específico por UUID."""
    try:
        result = await db.execute(
            select(Cliente).where(
                Cliente.id == cliente_id,
                Cliente.tenant_id == tenant_id,
            )
        )
        cliente = result.scalar_one_or_none()
        
        if not cliente:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")
        
        return cliente
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de cliente inválido")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao obter cliente: {str(e)}")

@router.post("/", response_model=ClienteResponse)
async def criar_cliente(
    cliente: ClienteCreate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Cria um novo cliente."""
    try:
        # Criar novo cliente
        cliente_uuid = uuid.uuid4()
        if hasattr(cliente, 'uuid') and cliente.uuid:
            try:
                cliente_uuid = uuid.UUID(cliente.uuid)
            except ValueError:
                cliente_uuid = uuid.uuid4()
        
        novo_cliente = Cliente(
            id=cliente_uuid,
            tenant_id=tenant_id,
            nome=cliente.nome,
            documento=cliente.documento,
            telefone=cliente.telefone,
            endereco=cliente.endereco,
            ativo=True
        )
        
        db.add(novo_cliente)
        await db.commit()
        await db.refresh(novo_cliente)

        # Broadcast realtime: cliente criado
        try:
            await realtime_manager.broadcast("cliente.created", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(novo_cliente.id),
                    "nome": novo_cliente.nome,
                    "documento": novo_cliente.documento,
                    "telefone": novo_cliente.telefone,
                    "updated_at": novo_cliente.updated_at.isoformat() if getattr(novo_cliente, 'updated_at', None) else None,
                }
            })
        except Exception:
            pass

        return novo_cliente
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao criar cliente: {str(e)}")

@router.put("/{cliente_id}", response_model=ClienteResponse)
async def atualizar_cliente(
    cliente_id: str,
    cliente: ClienteUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Atualiza um cliente existente."""
    try:
        # Buscar cliente existente
        result = await db.execute(
            select(Cliente).where(
                Cliente.id == cliente_id,
                Cliente.tenant_id == tenant_id,
            )
        )
        cliente_existente = result.scalar_one_or_none()
        
        if not cliente_existente:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")
        
        # Atualizar campos (usar chaves string para evitar 'keywords must be strings')
        update_data = {}
        if cliente.nome is not None:
            update_data["nome"] = cliente.nome
        if cliente.documento is not None:
            update_data["documento"] = cliente.documento
        if cliente.telefone is not None:
            update_data["telefone"] = cliente.telefone
        if cliente.endereco is not None:
            update_data["endereco"] = cliente.endereco
        update_data["updated_at"] = datetime.utcnow()

        await db.execute(
            update(Cliente)
            .where(Cliente.id == cliente_id, Cliente.tenant_id == tenant_id)
            .values(update_data)
        )
        await db.commit()
        
        # Retornar cliente atualizado
        result = await db.execute(
            select(Cliente).where(
                Cliente.id == cliente_id,
                Cliente.tenant_id == tenant_id,
            )
        )
        cliente_atualizado = result.scalar_one()

        # Broadcast realtime: cliente atualizado
        try:
            await realtime_manager.broadcast("cliente.updated", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(cliente_atualizado.id),
                    "nome": cliente_atualizado.nome,
                    "documento": cliente_atualizado.documento,
                    "telefone": cliente_atualizado.telefone,
                    "updated_at": cliente_atualizado.updated_at.isoformat() if getattr(cliente_atualizado, 'updated_at', None) else None,
                }
            })
        except Exception:
            pass

        return cliente_atualizado
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar cliente: {str(e)}")

@router.delete("/{cliente_id}")
async def deletar_cliente(
    cliente_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Deleta um cliente (hard delete)."""
    try:
        # Buscar cliente existente (independente de ativo)
        result = await db.execute(
            select(Cliente).where(
                Cliente.id == cliente_id,
                Cliente.tenant_id == tenant_id,
            )
        )
        cliente_existente = result.scalar_one_or_none()
        
        if not cliente_existente:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")
        
        # Hard delete (remoção física)
        await db.execute(
            delete(Cliente).where(
                Cliente.id == cliente_id,
                Cliente.tenant_id == tenant_id,
            )
        )
        await db.commit()

        # Broadcast realtime: cliente deletado
        try:
            await realtime_manager.broadcast("cliente.deleted", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(cliente_id),
                }
            })
        except Exception:
            pass

        return {"message": "Cliente removido definitivamente"}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao deletar cliente: {str(e)}")
