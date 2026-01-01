from datetime import datetime
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Produto(Base):
    __tablename__ = "produtos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    codigo: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    nome: Mapped[str] = mapped_column(String(255), index=True)
    descricao: Mapped[str | None] = mapped_column(Text, nullable=True)
    imagem: Mapped[str | None] = mapped_column(Text, nullable=True)
    preco_custo: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    preco_venda: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    estoque: Mapped[int] = mapped_column(Integer, default=0)
    estoque_minimo: Mapped[int] = mapped_column(Integer, default=0)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Mesa(Base):
    __tablename__ = "mesas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    numero: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    mesa_token: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        default=lambda: uuid.uuid4().hex,
    )
    capacidade: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(50), default="livre")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Pedido(Base):
    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        default=lambda: uuid.uuid4().hex,
    )
    mesa_id: Mapped[int] = mapped_column(ForeignKey("mesas.id"))
    lugar_numero: Mapped[int] = mapped_column(Integer, default=1)
    usuario_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="aberto")

    forma_pagamento_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor_total: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    valor_recebido: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    troco: Mapped[float] = mapped_column(Numeric(10, 2), default=0)

    observacao_cozinha: Mapped[str | None] = mapped_column(Text, nullable=True)

    origem: Mapped[str] = mapped_column(String(20), default="pdv")

    data_inicio: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    data_fechamento: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    mesa: Mapped[Mesa] = relationship("Mesa")
    itens: Mapped[list["ItemPedido"]] = relationship(
        "ItemPedido",
        back_populates="pedido",
        cascade="all, delete-orphan",
    )


class ItemPedido(Base):
    __tablename__ = "itens_pedido"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    uuid: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        default=lambda: uuid.uuid4().hex,
    )
    pedido_id: Mapped[int] = mapped_column(ForeignKey("pedidos.id", ondelete="CASCADE"))
    produto_id: Mapped[int] = mapped_column(ForeignKey("produtos.id"))

    quantidade: Mapped[int] = mapped_column(Integer, default=1)
    preco_unitario: Mapped[float] = mapped_column(Numeric(10, 2), default=0)
    observacao: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pedido: Mapped[Pedido] = relationship("Pedido", back_populates="itens")
    produto: Mapped[Produto] = relationship("Produto")
