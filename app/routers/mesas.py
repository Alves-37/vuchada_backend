from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter(prefix="/api/mesas", tags=["mesas"])


class MesaOut(BaseModel):
    numero: int
    capacidade: int
    status: str


def _default_mesas() -> list[MesaOut]:
    return [
        MesaOut(numero=1, capacidade=4, status="Livre"),
        MesaOut(numero=2, capacidade=4, status="Livre"),
        MesaOut(numero=3, capacidade=4, status="Livre"),
        MesaOut(numero=4, capacidade=4, status="Livre"),
    ]


@router.get("/", response_model=list[MesaOut])
@router.get("", response_model=list[MesaOut])
async def listar_mesas():
    return _default_mesas()
