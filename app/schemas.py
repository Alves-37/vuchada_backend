from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ProdutoBase(BaseModel):
    codigo: str
    nome: str
    descricao: str | None = None
    imagem: str | None = None
    preco_custo: Decimal | float = 0
    preco_venda: Decimal | float = 0
    estoque: int = 0
    estoque_minimo: int = 0
    ativo: bool = True


class ProdutoCreate(ProdutoBase):
    pass


class ProdutoUpdate(BaseModel):
    codigo: str | None = None
    nome: str | None = None
    descricao: str | None = None
    imagem: str | None = None
    preco_custo: Decimal | float | None = None
    preco_venda: Decimal | float | None = None
    estoque: int | None = None
    estoque_minimo: int | None = None
    ativo: bool | None = None


class ProdutoOut(ProdutoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    updated_at: datetime


class MesaBase(BaseModel):
    numero: int
    capacidade: int
    status: str = "livre"


class MesaCreate(MesaBase):
    pass


class MesaUpdate(BaseModel):
    numero: int | None = None
    capacidade: int | None = None
    status: str | None = None


class MesaOut(MesaBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    mesa_token: str
    created_at: datetime
    updated_at: datetime


class MesaDisponibilidadeOut(MesaOut):
    pedidos_ativos: int
    lugares_disponiveis: int


class ItemPedidoBase(BaseModel):
    produto_id: int
    quantidade: int = 1
    preco_unitario: Decimal | float = 0
    observacao: str | None = None


class ItemPedidoCreate(ItemPedidoBase):
    pass


class ItemPedidoOut(ItemPedidoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    created_at: datetime
    updated_at: datetime


class PedidoBase(BaseModel):
    mesa_id: int
    lugar_numero: int = 1
    usuario_id: int | None = None
    status: str = "aberto"

    forma_pagamento_id: int | None = None
    valor_total: Decimal | float = 0
    valor_recebido: Decimal | float = 0
    troco: Decimal | float = 0

    observacao_cozinha: str | None = None

    data_inicio: datetime | None = None
    data_fechamento: datetime | None = None


class PedidoCreate(PedidoBase):
    itens: list[ItemPedidoCreate] = []


class PedidoUpdate(BaseModel):
    mesa_id: int | None = None
    lugar_numero: int | None = None
    usuario_id: int | None = None
    status: str | None = None

    forma_pagamento_id: int | None = None
    valor_total: Decimal | float | None = None
    valor_recebido: Decimal | float | None = None
    troco: Decimal | float | None = None

    observacao_cozinha: str | None = None

    data_inicio: datetime | None = None
    data_fechamento: datetime | None = None


class PedidoOut(PedidoBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    uuid: str
    created_at: datetime
    updated_at: datetime
    itens: list[ItemPedidoOut] = []


class PublicCartItem(BaseModel):
    produto_id: int
    quantidade: int = 1
    observacao: str | None = None


class PublicPedidoCreate(BaseModel):
    mesa_id: int | None = None
    mesa_numero: int | None = None
    mesa_token: str | None = None
    lugar_numero: int = 1
    observacao_cozinha: str | None = None
    itens: list[PublicCartItem] = []


class PublicPedidoOut(BaseModel):
    pedido_id: int
    status: str
    valor_total: Decimal | float
    mesa_id: int
    lugar_numero: int
    created_at: datetime
