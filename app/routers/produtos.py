import os
import re
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.db import get_db
from app import models
from app import schemas

router = APIRouter(prefix="/produtos", tags=["produtos"])


_IMG_NAME_RE = re.compile(r"\.(png|jpg|jpeg|gif|webp)$", re.IGNORECASE)


def _uploads_dir() -> str:
    # app/static/uploads
    base = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads")
    os.makedirs(base, exist_ok=True)
    return base


def _looks_like_image_path(value: str | None) -> bool:
    if not value:
        return False
    s = str(value).strip().lower()
    if "/" in s or "\\" in s:
        return bool(_IMG_NAME_RE.search(s))
    return False


@router.get("/", response_model=list[schemas.ProdutoOut])
def listar_produtos(ativo: bool | None = True, db: Session = Depends(get_db)):
    q = db.query(models.Produto)
    if ativo is not None:
        q = q.filter(models.Produto.ativo == ativo)
    return q.order_by(models.Produto.nome.asc()).all()


@router.get("/{produto_id}", response_model=schemas.ProdutoOut)
def obter_produto(produto_id: int, db: Session = Depends(get_db)):
    produto = db.get(models.Produto, produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return produto


@router.post("/upsert", response_model=schemas.ProdutoOut)
def upsert_produto(payload: schemas.ProdutoCreate, db: Session = Depends(get_db)):
    existente = db.query(models.Produto).filter(models.Produto.codigo == payload.codigo).first()
    if existente:
        data = payload.model_dump()

        # Estoque é controlado pelo servidor (ex.: pedidos públicos baixam estoque).
        # Não permitir que o cliente (NEOPDV2) sobrescreva estoque ao sincronizar catálogo.
        try:
            incoming_estoque = data.get("estoque")
            incoming_estoque_minimo = data.get("estoque_minimo")
            server_estoque = getattr(existente, "estoque", None)
            server_estoque_minimo = getattr(existente, "estoque_minimo", None)

            # Permitir "seeding" inicial apenas quando o servidor ainda não tem estoque definido.
            if server_estoque is not None and int(server_estoque) > 0:
                data.pop("estoque", None)
            elif incoming_estoque is None:
                data.pop("estoque", None)

            if server_estoque_minimo is not None and int(server_estoque_minimo) > 0:
                data.pop("estoque_minimo", None)
            elif incoming_estoque_minimo is None:
                data.pop("estoque_minimo", None)
        except Exception:
            data.pop("estoque", None)
            data.pop("estoque_minimo", None)

        # Guard: if payload.nome looks like an image path, do not overwrite nome.
        try:
            if _looks_like_image_path(data.get("nome")):
                data.pop("nome", None)
        except Exception:
            pass

        for k, v in data.items():
            setattr(existente, k, v)

        # Garantir atualização de timestamp no servidor
        existente.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(existente)
        return existente

    produto = models.Produto(**payload.model_dump())
    db.add(produto)
    db.commit()
    db.refresh(produto)
    return produto


@router.post("/upload-imagem")
async def upload_imagem(file: UploadFile = File(...)):
    # Store image and return public URL.
    ext = os.path.splitext(file.filename or "")[1].lower()
    if not ext or not _IMG_NAME_RE.search(ext):
        raise HTTPException(status_code=400, detail="Formato de imagem inválido")

    name = f"produto_{uuid.uuid4().hex}{ext}"
    path = os.path.join(_uploads_dir(), name)

    try:
        content = await file.read()
        with open(path, "wb") as f:
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao salvar imagem: {e}")

    return {"url": f"/static/uploads/{name}"}


@router.post("/", response_model=schemas.ProdutoOut)
def criar_produto(payload: schemas.ProdutoCreate, db: Session = Depends(get_db)):
    existente = db.query(models.Produto).filter(models.Produto.codigo == payload.codigo).first()
    if existente:
        raise HTTPException(status_code=409, detail="Já existe um produto com este código")

    produto = models.Produto(**payload.model_dump())
    db.add(produto)
    db.commit()
    db.refresh(produto)
    return produto


@router.put("/{produto_id}", response_model=schemas.ProdutoOut)
def atualizar_produto(produto_id: int, payload: schemas.ProdutoUpdate, db: Session = Depends(get_db)):
    produto = db.get(models.Produto, produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    data = payload.model_dump(exclude_unset=True)
    if "codigo" in data:
        existente = (
            db.query(models.Produto)
            .filter(models.Produto.codigo == data["codigo"], models.Produto.id != produto_id)
            .first()
        )
        if existente:
            raise HTTPException(status_code=409, detail="Já existe um produto com este código")

    for k, v in data.items():
        setattr(produto, k, v)

    # Garantir atualização de timestamp no servidor
    produto.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(produto)
    return produto


@router.delete("/{produto_id}")
def excluir_produto(produto_id: int, db: Session = Depends(get_db)):
    produto = db.get(models.Produto, produto_id)
    if not produto:
        raise HTTPException(status_code=404, detail="Produto não encontrado")

    db.delete(produto)
    db.commit()
    return {"ok": True}
