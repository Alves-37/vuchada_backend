from __future__ import annotations

import uuid
from datetime import datetime, timezone, time
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import get_current_admin_user, get_current_user, get_tenant_id
from app.db.database import get_db_session
from app.db.models import Turno, TurnoMembro, User


router = APIRouter(prefix="/api/turnos", tags=["turnos"])


class TurnoMembroIn(BaseModel):
    usuario_id: str
    papel: Optional[str] = "funcionario"
    is_chefe: bool = False


class TurnoMembroOut(BaseModel):
    id: str
    usuario_id: str
    usuario_nome: Optional[str] = None
    papel: Optional[str] = "funcionario"
    is_chefe: bool = False


class TurnoOut(BaseModel):
    id: str
    nome: str
    inicio: Optional[datetime] = None
    fim: Optional[datetime] = None
    dias_semana: Optional[list[int]] = None
    hora_inicio: Optional[str] = None
    hora_fim: Optional[str] = None
    ativo: bool = False
    membros: list[TurnoMembroOut] = []


class TurnoCreate(BaseModel):
    nome: str
    inicio: Optional[datetime] = None
    fim: Optional[datetime] = None
    dias_semana: Optional[list[int]] = None
    hora_inicio: Optional[str] = None  # HH:MM
    hora_fim: Optional[str] = None  # HH:MM


class TurnoUpdate(BaseModel):
    nome: Optional[str] = None
    inicio: Optional[datetime] = None
    fim: Optional[datetime] = None
    dias_semana: Optional[list[int]] = None
    hora_inicio: Optional[str] = None
    hora_fim: Optional[str] = None
    ativo: Optional[bool] = None


class TurnoMembrosUpdate(BaseModel):
    membros: list[TurnoMembroIn] = []


def _parse_uuid(value: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field} inválido")


def _turno_to_out(t: Turno) -> TurnoOut:
    membros_out: list[TurnoMembroOut] = []
    for m in (getattr(t, "membros", None) or []):
        membros_out.append(
            TurnoMembroOut(
                id=str(getattr(m, "id")),
                usuario_id=str(getattr(m, "usuario_id")),
                usuario_nome=getattr(getattr(m, "usuario", None), "nome", None),
                papel=getattr(m, "papel", None) or "funcionario",
                is_chefe=bool(getattr(m, "is_chefe", False)),
            )
        )
    return TurnoOut(
        id=str(getattr(t, "id")),
        nome=str(getattr(t, "nome")),
        inicio=getattr(t, "inicio", None),
        fim=getattr(t, "fim", None),
        dias_semana=_parse_dias_semana(getattr(t, "dias_semana", None)),
        hora_inicio=_fmt_hora(getattr(t, "hora_inicio", None)),
        hora_fim=_fmt_hora(getattr(t, "hora_fim", None)),
        ativo=bool(getattr(t, "ativo", False)),
        membros=membros_out,
    )


def _parse_dias_semana(value: Optional[str]) -> Optional[list[int]]:
    if not value:
        return None
    out: list[int] = []
    for part in str(value).split(','):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(int(part))
        except Exception:
            continue
    return out or None


def _dias_to_str(dias: Optional[list[int]]) -> Optional[str]:
    if dias is None:
        return None
    cleaned: list[int] = []
    for d in dias:
        try:
            di = int(d)
        except Exception:
            continue
        if 0 <= di <= 6:
            cleaned.append(di)
    cleaned = sorted(set(cleaned))
    return ','.join(str(d) for d in cleaned) if cleaned else None


def _parse_hora(h: Optional[str], field: str) -> Optional[time]:
    if h is None:
        return None
    s = str(h).strip()
    if not s:
        return None
    try:
        hh, mm = s.split(':', 1)
        return time(hour=int(hh), minute=int(mm))
    except Exception:
        raise HTTPException(status_code=400, detail=f"{field} inválido (use HH:MM)")


def _fmt_hora(t: Optional[time]) -> Optional[str]:
    if not t:
        return None
    try:
        return f"{int(t.hour):02d}:{int(t.minute):02d}"
    except Exception:
        return None


@router.get("/ativo", response_model=Optional[TurnoOut])
async def obter_turno_ativo(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_user),
):
    # Usar horário local do restaurante para decidir turno do dia.
    tz = ZoneInfo("Africa/Maputo") if ZoneInfo else timezone.utc
    now_local = datetime.now(tz)
    now_utc = datetime.now(timezone.utc)

    # 1) Preferir turno já marcado como ativo
    res_ativo = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.tenant_id == tenant_id, Turno.ativo == True)
        .order_by(Turno.created_at.desc())
        .limit(1)
    )
    ativo = res_ativo.scalars().first()

    # Se existe ativo mas está fora da janela (quando janela está definida), desativar
    if ativo:
        inicio = getattr(ativo, "inicio", None)
        fim = getattr(ativo, "fim", None)
        if inicio and fim and not (inicio <= now_utc <= fim):
            await db.execute(update(Turno).where(Turno.id == ativo.id, Turno.tenant_id == tenant_id).values({Turno.ativo: False}))
            await db.commit()
            ativo = None

    # 2) Auto ativar pelo horário absoluto (inicio <= now <= fim)
    if not ativo:
        res_sched = await db.execute(
            select(Turno)
            .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
            .where(
                Turno.tenant_id == tenant_id,
                Turno.inicio.is_not(None),
                Turno.fim.is_not(None),
                Turno.inicio <= now_utc,
                Turno.fim >= now_utc,
            )
            .order_by(Turno.inicio.desc())
            .limit(1)
        )
        candidato = res_sched.scalars().first()
        if candidato:
            await db.execute(update(Turno).where(Turno.tenant_id == tenant_id).values({Turno.ativo: False}))
            await db.execute(update(Turno).where(Turno.id == candidato.id, Turno.tenant_id == tenant_id).values({Turno.ativo: True}))
            await db.commit()

            res_full = await db.execute(
                select(Turno)
                .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
                .where(Turno.id == candidato.id, Turno.tenant_id == tenant_id)
            )
            ativo = res_full.scalar_one_or_none()

    # 3) Auto ativar por escala recorrente (dias da semana + hora)
    if not ativo:
        res_all = await db.execute(
            select(Turno)
            .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
            .where(Turno.tenant_id == tenant_id)
            .order_by(Turno.created_at.desc())
            .limit(200)
        )
        rows = res_all.scalars().all()

        dow = int(now_local.weekday())  # 0=Seg
        now_t = now_local.time().replace(second=0, microsecond=0)

        def matches(t: Turno) -> bool:
            dias = _parse_dias_semana(getattr(t, "dias_semana", None))
            hi = getattr(t, "hora_inicio", None)
            hf = getattr(t, "hora_fim", None)
            if not dias or hi is None or hf is None:
                return False
            if dow not in dias:
                return False
            if hi <= hf:
                return hi <= now_t <= hf
            # Turno atravessa meia-noite
            return now_t >= hi or now_t <= hf

        candidato = next((t for t in rows if matches(t)), None)
        if candidato:
            await db.execute(update(Turno).where(Turno.tenant_id == tenant_id).values({Turno.ativo: False}))
            await db.execute(update(Turno).where(Turno.id == candidato.id, Turno.tenant_id == tenant_id).values({Turno.ativo: True}))
            await db.commit()

            res_full = await db.execute(
                select(Turno)
                .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
                .where(Turno.id == candidato.id, Turno.tenant_id == tenant_id)
            )
            ativo = res_full.scalar_one_or_none()

    return _turno_to_out(ativo) if ativo else None


@router.get("/", response_model=list[TurnoOut])
async def listar_turnos(
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    res = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.tenant_id == tenant_id)
        .order_by(Turno.created_at.desc())
        .limit(200)
    )
    rows = res.scalars().all()
    return [_turno_to_out(t) for t in rows]


@router.post("/", response_model=TurnoOut, status_code=status.HTTP_201_CREATED)
async def criar_turno(
    payload: TurnoCreate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    nome = (payload.nome or "").strip()
    if not nome:
        raise HTTPException(status_code=400, detail="nome é obrigatório")

    t = Turno(
        tenant_id=tenant_id,
        nome=nome,
        inicio=payload.inicio,
        fim=payload.fim,
        dias_semana=_dias_to_str(payload.dias_semana),
        hora_inicio=_parse_hora(payload.hora_inicio, "hora_inicio"),
        hora_fim=_parse_hora(payload.hora_fim, "hora_fim"),
        ativo=False,
    )
    db.add(t)
    await db.commit()

    res = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.id == t.id, Turno.tenant_id == tenant_id)
    )
    full = res.scalar_one()
    return _turno_to_out(full)


@router.delete("/{turno_id}")
async def apagar_turno(
    turno_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    tid = _parse_uuid(turno_id, "turno_id")

    res = await db.execute(select(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id))
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Turno não encontrado")

    await db.execute(delete(TurnoMembro).where(TurnoMembro.turno_id == tid))
    await db.execute(delete(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id))
    await db.commit()
    return {"ok": True}


@router.put("/{turno_id}", response_model=TurnoOut)
async def atualizar_turno(
    turno_id: str,
    payload: TurnoUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    tid = _parse_uuid(turno_id, "turno_id")

    res = await db.execute(select(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id))
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Turno não encontrado")

    if payload.nome is not None:
        nome = (payload.nome or "").strip()
        if not nome:
            raise HTTPException(status_code=400, detail="nome inválido")
        t.nome = nome
    if payload.inicio is not None:
        t.inicio = payload.inicio
    if payload.fim is not None:
        t.fim = payload.fim
    if payload.dias_semana is not None:
        t.dias_semana = _dias_to_str(payload.dias_semana)
    if payload.hora_inicio is not None:
        t.hora_inicio = _parse_hora(payload.hora_inicio, "hora_inicio")
    if payload.hora_fim is not None:
        t.hora_fim = _parse_hora(payload.hora_fim, "hora_fim")
    if payload.ativo is not None:
        t.ativo = bool(payload.ativo)

    await db.commit()

    res2 = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.id == tid, Turno.tenant_id == tenant_id)
    )
    full = res2.scalar_one()
    return _turno_to_out(full)


@router.post("/{turno_id}/ativar", response_model=TurnoOut)
async def ativar_turno(
    turno_id: str,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    tid = _parse_uuid(turno_id, "turno_id")

    res = await db.execute(select(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id))
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Turno não encontrado")

    await db.execute(update(Turno).where(Turno.tenant_id == tenant_id).values({Turno.ativo: False}))
    await db.execute(update(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id).values({Turno.ativo: True}))
    await db.commit()

    res2 = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.id == tid, Turno.tenant_id == tenant_id)
    )
    full = res2.scalar_one()
    return _turno_to_out(full)


@router.put("/{turno_id}/membros", response_model=TurnoOut)
async def atualizar_membros_turno(
    turno_id: str,
    payload: TurnoMembrosUpdate,
    db: AsyncSession = Depends(get_db_session),
    tenant_id: uuid.UUID = Depends(get_tenant_id),
    user=Depends(get_current_admin_user),
):
    tid = _parse_uuid(turno_id, "turno_id")

    res = await db.execute(select(Turno).where(Turno.id == tid, Turno.tenant_id == tenant_id))
    t = res.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Turno não encontrado")

    membros = payload.membros or []
    if len(membros) > 3:
        raise HTTPException(status_code=400, detail="Máximo de 3 funcionários por turno")

    chefes = [m for m in membros if bool(m.is_chefe)]
    if len(chefes) != 1:
        raise HTTPException(status_code=400, detail="Selecione exatamente 1 chefe")

    # Validar se usuários existem e pertencem ao tenant
    usuario_ids: list[uuid.UUID] = []
    for m in membros:
        uid = _parse_uuid(m.usuario_id, "usuario_id")
        usuario_ids.append(uid)

    if len(set(usuario_ids)) != len(usuario_ids):
        raise HTTPException(status_code=400, detail="Usuários duplicados no turno")

    users_res = await db.execute(select(User).where(User.id.in_(usuario_ids), User.tenant_id == tenant_id, User.ativo == True))
    found = users_res.scalars().all()
    if len(found) != len(usuario_ids):
        raise HTTPException(status_code=400, detail="Um ou mais usuários são inválidos")

    await db.execute(delete(TurnoMembro).where(TurnoMembro.turno_id == tid))

    for m in membros:
        uid = _parse_uuid(m.usuario_id, "usuario_id")
        db.add(
            TurnoMembro(
                turno_id=tid,
                usuario_id=uid,
                papel=(m.papel or "funcionario"),
                is_chefe=bool(m.is_chefe),
            )
        )

    await db.commit()

    res2 = await db.execute(
        select(Turno)
        .options(selectinload(Turno.membros).selectinload(TurnoMembro.usuario))
        .where(Turno.id == tid, Turno.tenant_id == tenant_id)
    )
    full = res2.scalar_one()
    return _turno_to_out(full)
