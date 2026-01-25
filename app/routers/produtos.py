"""Endpoints para gerenciamento de produtos com sincronização."""
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, or_
from sqlalchemy.exc import IntegrityError
from typing import List, Optional
import uuid
from datetime import datetime
import os

from app.db.database import get_db_session
from app.db.models import Produto
from app.core.realtime import manager as realtime_manager
from app.core.deps import get_tenant_id
from pydantic import BaseModel

router = APIRouter(prefix="/api/produtos", tags=["Produtos"])

# Schemas Pydantic
class ProdutoCreate(BaseModel):
    codigo: str
    nome: str
    descricao: str = ""
    preco_custo: float = 0.0
    preco_venda: float
    estoque: float = 0.0
    estoque_minimo: float = 0.0
    categoria_id: Optional[int] = None
    venda_por_peso: bool = False
    unidade_medida: str = "un"
    taxa_iva: float = 0.0
    ativo: bool = True
    uuid: Optional[str] = None

class ProdutoUpdate(BaseModel):
    codigo: Optional[str] = None
    nome: Optional[str] = None
    descricao: Optional[str] = None
    preco_custo: Optional[float] = None
    preco_venda: Optional[float] = None
    estoque: Optional[float] = None
    estoque_minimo: Optional[float] = None
    categoria_id: Optional[int] = None
    venda_por_peso: Optional[bool] = None
    unidade_medida: Optional[str] = None
    taxa_iva: Optional[float] = None
    ativo: Optional[bool] = None


class ProdutoUpsert(BaseModel):
    codigo: str
    nome: str
    descricao: Optional[str] = None
    imagem: Optional[str] = None
    preco_custo: Optional[float] = 0.0
    preco_venda: Optional[float] = 0.0
    estoque: Optional[float] = 0.0
    estoque_minimo: Optional[float] = 0.0
    categoria_id: Optional[int] = None
    venda_por_peso: Optional[bool] = False
    unidade_medida: Optional[str] = "un"
    taxa_iva: Optional[float] = 0.0
    ativo: Optional[bool] = True
    updated_at: Optional[str] = None


def _parse_iso_dt(value: Optional[str]):
    if not value:
        return None
    try:
        s = str(value)
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        try:
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.replace(tzinfo=None)
        except Exception:
            pass
        return dt
    except Exception:
        return None


@router.post("/upsert")
async def upsert_produto(
    payload: ProdutoUpsert,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    codigo = (payload.codigo or "").strip()
    nome = (payload.nome or "").strip()
    if not codigo or not nome:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="codigo e nome são obrigatórios")

    result = await db.execute(
        select(Produto).where(
            Produto.codigo == codigo,
            Produto.tenant_id == tenant_id,
        )
    )
    existing = result.scalar_one_or_none()

    incoming_updated = _parse_iso_dt(payload.updated_at)

    if existing:
        if incoming_updated and existing.updated_at and incoming_updated <= existing.updated_at:
            return {"status": "skipped", "reason": "older_or_equal", "id": str(existing.id)}

        update_data = {
            "nome": nome,
            "descricao": payload.descricao or "",
            "preco_custo": float(payload.preco_custo or 0),
            "preco_venda": float(payload.preco_venda or 0),
            "estoque": float(payload.estoque or 0),
            "estoque_minimo": float(payload.estoque_minimo or 0),
            "categoria_id": payload.categoria_id,
            "venda_por_peso": bool(payload.venda_por_peso or False),
            "unidade_medida": payload.unidade_medida or "un",
            "taxa_iva": float(payload.taxa_iva or 0.0),
            "ativo": bool(payload.ativo if payload.ativo is not None else True),
            "updated_at": incoming_updated or datetime.utcnow(),
        }

        if isinstance(payload.imagem, str):
            update_data["imagem_path"] = payload.imagem

        await db.execute(
            update(Produto)
            .where(
                Produto.id == existing.id,
                Produto.tenant_id == tenant_id,
            )
            .values(**update_data)
        )
        await db.commit()
        return {"status": "updated", "id": str(existing.id)}

    produto = Produto(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        codigo=codigo,
        nome=nome,
        descricao=payload.descricao or "",
        preco_custo=float(payload.preco_custo or 0),
        preco_venda=float(payload.preco_venda or 0),
        estoque=float(payload.estoque or 0),
        estoque_minimo=float(payload.estoque_minimo or 0),
        categoria_id=payload.categoria_id,
        venda_por_peso=bool(payload.venda_por_peso or False),
        unidade_medida=payload.unidade_medida or "un",
        taxa_iva=float(payload.taxa_iva or 0.0),
        ativo=bool(payload.ativo if payload.ativo is not None else True),
        updated_at=incoming_updated or datetime.utcnow(),
    )
    if isinstance(payload.imagem, str) and payload.imagem.strip():
        produto.imagem_path = payload.imagem.strip()
    db.add(produto)
    await db.commit()
    return {"status": "created", "id": str(produto.id)}

class ProdutoResponse(BaseModel):
    id: str
    codigo: str
    nome: str
    descricao: Optional[str] = None
    preco_custo: float
    preco_venda: float
    estoque: float
    estoque_minimo: float
    categoria_id: Optional[int] = None
    venda_por_peso: bool
    unidade_medida: str
    taxa_iva: float
    ativo: bool
    imagem_path: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        
    @classmethod
    def from_orm(cls, obj):
        return cls(
            id=str(obj.id),
            codigo=obj.codigo,
            nome=obj.nome,
            descricao=obj.descricao,
            preco_custo=obj.preco_custo,
            preco_venda=obj.preco_venda,
            estoque=obj.estoque,
            estoque_minimo=obj.estoque_minimo,
            categoria_id=obj.categoria_id,
            venda_por_peso=obj.venda_por_peso,
            unidade_medida=obj.unidade_medida,
            taxa_iva=getattr(obj, "taxa_iva", 0.0),
            ativo=obj.ativo,
            imagem_path=getattr(obj, "imagem_path", None),
            created_at=obj.created_at,
            updated_at=obj.updated_at
        )

@router.get("/", response_model=List[ProdutoResponse])
async def get_produtos(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    q: Optional[str] = None,
    incluir_inativos: bool = False,
):
    """Lista todos os produtos ativos."""
    try:
        query = select(Produto).where(Produto.tenant_id == tenant_id)
        if not incluir_inativos:
            query = query.where(Produto.ativo == True)
        if q:
            term = f"%{q.strip()}%"
            query = query.where(or_(Produto.nome.ilike(term), Produto.codigo.ilike(term)))

        result = await db.execute(query.order_by(Produto.nome))
        produtos = result.scalars().all()
        return [ProdutoResponse.from_orm(p) for p in produtos]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar produtos: {str(e)}"
        )

@router.get("/{produto_uuid}", response_model=ProdutoResponse)
async def get_produto(
    produto_uuid: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Busca produto por UUID."""
    try:
        # Tentar converter para UUID
        produto_id = uuid.UUID(produto_uuid)
        
        result = await db.execute(
            select(Produto).where(
                Produto.id == produto_id,
                Produto.ativo == True,
                Produto.tenant_id == tenant_id,
            )
        )
        produto = result.scalar_one_or_none()
        
        if not produto:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produto não encontrado"
            )
        
        return ProdutoResponse.from_orm(produto)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UUID inválido"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar produto: {str(e)}"
        )

@router.post("/", response_model=ProdutoResponse, status_code=status.HTTP_201_CREATED)
async def create_produto(
    produto_data: ProdutoCreate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Cria novo produto."""
    try:
        # Gerar UUID se não fornecido
        produto_uuid = uuid.UUID(produto_data.uuid) if produto_data.uuid else uuid.uuid4()
        
        # Verificar se UUID já existe
        existing = await db.execute(
            select(Produto).where(
                Produto.id == produto_uuid,
                Produto.tenant_id == tenant_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Produto com este UUID já existe"
            )
        
        # Criar produto
        produto = Produto(
            id=produto_uuid,
            tenant_id=tenant_id,
            codigo=produto_data.codigo,
            nome=produto_data.nome,
            descricao=produto_data.descricao,
            preco_custo=produto_data.preco_custo,
            preco_venda=produto_data.preco_venda,
            estoque=produto_data.estoque,
            estoque_minimo=produto_data.estoque_minimo,
            categoria_id=produto_data.categoria_id,
            venda_por_peso=produto_data.venda_por_peso,
            unidade_medida=produto_data.unidade_medida,
            taxa_iva=getattr(produto_data, "taxa_iva", 0.0),
            ativo=bool(getattr(produto_data, "ativo", True))
        )
        
        db.add(produto)
        await db.commit()
        await db.refresh(produto)
        
        # Broadcast realtime: produto criado
        try:
            await realtime_manager.broadcast("produto.created", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(produto.id),
                    "codigo": produto.codigo,
                    "nome": produto.nome,
                    "estoque": float(produto.estoque or 0),
                    "preco_venda": float(produto.preco_venda or 0),
                    "updated_at": produto.updated_at.isoformat() if produto.updated_at else None,
                }
            })
        except Exception:
            pass

        return ProdutoResponse.from_orm(produto)
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao criar produto: {str(e)}"
        )

@router.put("/{produto_uuid}", response_model=ProdutoResponse)
async def update_produto(
    produto_uuid: str, 
    produto_data: ProdutoUpdate, 
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Atualiza produto existente."""
    try:
        # Converter UUID
        produto_id = uuid.UUID(produto_uuid)
        
        # Buscar produto
        result = await db.execute(
            select(Produto).where(
                Produto.id == produto_id,
                Produto.ativo == True,
                Produto.tenant_id == tenant_id,
            )
        )
        produto = result.scalar_one_or_none()
        
        if not produto:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produto não encontrado"
            )
        
        # Atualizar campos fornecidos
        update_data = produto_data.dict(exclude_unset=True)
        if update_data:
            update_data['updated_at'] = datetime.utcnow()
            
            await db.execute(
                update(Produto)
                .where(
                    Produto.id == produto_id,
                    Produto.tenant_id == tenant_id,
                )
                .values(**update_data)
            )
            await db.commit()
            await db.refresh(produto)
        
        # Broadcast realtime: produto atualizado
        try:
            await realtime_manager.broadcast("produto.updated", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(produto.id),
                    "codigo": produto.codigo,
                    "nome": produto.nome,
                    "estoque": float(produto.estoque or 0),
                    "preco_venda": float(produto.preco_venda or 0),
                    "updated_at": produto.updated_at.isoformat() if produto.updated_at else None,
                }
            })
        except Exception:
            pass

        return ProdutoResponse.from_orm(produto)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UUID inválido"
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao atualizar produto: {str(e)}"
        )


@router.post("/{produto_uuid}/imagem", response_model=ProdutoResponse)
async def upload_imagem_produto(
    produto_uuid: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    try:
        produto_id = uuid.UUID(produto_uuid)
        result = await db.execute(
            select(Produto).where(
                Produto.id == produto_id,
                Produto.tenant_id == tenant_id,
            )
        )
        produto = result.scalar_one_or_none()
        if not produto:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Produto não encontrado")

        filename = (file.filename or "").strip()
        ext = os.path.splitext(filename)[1].lower()
        if ext not in [".jpg", ".jpeg", ".png", ".webp"]:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato inválido (use jpg, png ou webp)")

        media_dir = os.getenv("MEDIA_DIR", "media")
        rel_dir = os.path.join("produtos", str(tenant_id))
        abs_dir = os.path.join(media_dir, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)

        out_name = f"{produto_id}{ext}"
        abs_path = os.path.join(abs_dir, out_name)

        content = await file.read()
        with open(abs_path, "wb") as f:
            f.write(content)

        imagem_path = f"/media/produtos/{tenant_id}/{out_name}"
        await db.execute(
            update(Produto)
            .where(Produto.id == produto_id, Produto.tenant_id == tenant_id)
            .values(imagem_path=imagem_path, updated_at=datetime.utcnow())
        )
        await db.commit()

        result2 = await db.execute(
            select(Produto).where(Produto.id == produto_id, Produto.tenant_id == tenant_id)
        )
        produto2 = result2.scalar_one()
        return ProdutoResponse.from_orm(produto2)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="UUID inválido")
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao enviar imagem: {str(e)}")

@router.delete("/{produto_uuid}")
async def delete_produto(
    produto_uuid: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Exclusão do produto."""
    try:
        # Converter UUID
        produto_id = uuid.UUID(produto_uuid)

        # Verificar se produto existe (independente de ativo)
        result = await db.execute(
            select(Produto).where(
                Produto.id == produto_id,
                Produto.tenant_id == tenant_id,
            )
        )
        produto = result.scalar_one_or_none()

        if not produto:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Produto não encontrado"
            )

        try:
            await db.execute(
                delete(Produto)
                .where(
                    Produto.id == produto_id,
                    Produto.tenant_id == tenant_id,
                )
            )
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Não foi possível excluir: produto já foi usado em vendas. Desative o produto em vez de excluir.",
            )

        # Broadcast realtime: produto deletado
        try:
            await realtime_manager.broadcast("produto.deleted", {
                "ts": datetime.utcnow().isoformat(),
                "data": {
                    "id": str(produto_id),
                    "soft": False,
                }
            })
        except Exception:
            pass

        return {"message": "Produto excluído"}
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="UUID inválido"
        )
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao deletar produto: {str(e)}"
        )

# Endpoints de sincronização
@router.post("/sync/push")
async def sync_push_produtos(
    produtos: List[dict], 
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Recebe produtos do cliente para sincronização."""
    try:
        synced_count = 0
        errors = []
        
        for produto_data in produtos:
            try:
                produto_uuid = uuid.UUID(produto_data['uuid'])
                
                # Verificar se produto já existe
                result = await db.execute(
                    select(Produto).where(
                        Produto.id == produto_uuid,
                        Produto.tenant_id == tenant_id,
                    )
                )
                existing = result.scalar_one_or_none()
                
                if existing:
                    # Atualizar produto existente
                    update_data = {
                        'codigo': produto_data.get('codigo', ''),
                        'nome': produto_data['nome'],
                        'descricao': produto_data.get('descricao', ''),
                        'preco_custo': produto_data.get('preco_custo', 0),
                        'preco_venda': produto_data.get('preco_venda', 0),
                        'estoque': produto_data.get('estoque', 0),
                        'estoque_minimo': produto_data.get('estoque_minimo', 0),
                        'categoria_id': produto_data.get('categoria_id'),
                        'venda_por_peso': produto_data.get('venda_por_peso', False),
                        'unidade_medida': produto_data.get('unidade_medida', 'un'),
                        'taxa_iva': produto_data.get('taxa_iva', 0.0),
                        'updated_at': datetime.utcnow()
                    }
                    
                    await db.execute(
                        update(Produto)
                        .where(
                            Produto.id == produto_uuid,
                            Produto.tenant_id == tenant_id,
                        )
                        .values(**update_data)
                    )
                else:
                    # Criar novo produto
                    produto = Produto(
                        id=produto_uuid,
                        tenant_id=tenant_id,
                        codigo=produto_data.get('codigo', ''),
                        nome=produto_data['nome'],
                        descricao=produto_data.get('descricao', ''),
                        preco_custo=produto_data.get('preco_custo', 0),
                        preco_venda=produto_data.get('preco_venda', 0),
                        estoque=produto_data.get('estoque', 0),
                        estoque_minimo=produto_data.get('estoque_minimo', 0),
                        categoria_id=produto_data.get('categoria_id'),
                        venda_por_peso=produto_data.get('venda_por_peso', False),
                        unidade_medida=produto_data.get('unidade_medida', 'un'),
                        taxa_iva=produto_data.get('taxa_iva', 0.0),
                        ativo=True
                    )
                    db.add(produto)
                
                synced_count += 1
                
            except Exception as e:
                errors.append({
                    'uuid': produto_data.get('uuid', 'unknown'),
                    'error': str(e)
                })
        
        await db.commit()
        
        return {
            'synced_count': synced_count,
            'errors': errors,
            'message': f'{synced_count} produtos sincronizados com sucesso'
        }
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro na sincronização: {str(e)}"
        )

@router.get("/sync/pull")
async def sync_pull_produtos(
    last_sync: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
):
    """Envia produtos atualizados para o cliente."""
    try:
        query = select(Produto).where(
            Produto.ativo == True,
            Produto.tenant_id == tenant_id,
        )
        
        # Filtrar por data de última sincronização se fornecida
        if last_sync:
            try:
                last_sync_date = datetime.fromisoformat(last_sync.replace('Z', '+00:00'))
                query = query.where(Produto.updated_at > last_sync_date)
            except ValueError:
                pass  # Ignorar data inválida
        
        result = await db.execute(query.order_by(Produto.updated_at))
        produtos = result.scalars().all()
        
        return {
            'produtos': [
                {
                    'uuid': str(produto.id),
                    'codigo': produto.codigo,
                    'nome': produto.nome,
                    'descricao': produto.descricao,
                    'preco_custo': produto.preco_custo,
                    'preco_venda': produto.preco_venda,
                    'estoque': produto.estoque,
                    'estoque_minimo': produto.estoque_minimo,
                    'categoria_id': produto.categoria_id,
                    'venda_por_peso': produto.venda_por_peso,
                    'unidade_medida': produto.unidade_medida,
                    'taxa_iva': getattr(produto, 'taxa_iva', 0.0),
                    'ativo': produto.ativo,
                    'created_at': produto.created_at.isoformat(),
                    'updated_at': produto.updated_at.isoformat()
                }
                for produto in produtos
            ],
            'count': len(produtos),
            'sync_timestamp': datetime.utcnow().isoformat()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar produtos para sincronização: {str(e)}"
        )
